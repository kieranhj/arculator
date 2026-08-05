/*Arculator headless front-end - control socket.

  A deliberately dumb line-oriented protocol. Everything ergonomic - symbol
  lookup, PNG encoding, retry loops - belongs in the controlling process, which
  is far easier to iterate on than C.

  Request:   <command> [args...]\n
  Response:  OK <payload length>\n<payload>
        or:  ERR <payload length>\n<message>

  The payload is text for most commands and raw binary for the bulk ones
  (memory reads, framebuffer, audio), which is why it is length-prefixed rather
  than line-terminated.*/
#include <stdarg.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
	/*NOGDI keeps wingdi.h from defining its own BITMAP, which would collide
	  with the one vidc.h declares.*/
	#define WIN32_LEAN_AND_MEAN
	#define NOGDI
	#include <winsock2.h>
	#include <ws2tcpip.h>
	typedef int socklen_t;
	#define CLOSESOCKET closesocket
	#define SOCKET_T SOCKET
	#define BAD_SOCKET INVALID_SOCKET
#else
	#include <arpa/inet.h>
	#include <netinet/in.h>
	#include <netinet/tcp.h>
	#include <sys/socket.h>
	#include <unistd.h>
	typedef int SOCKET_T;
	#define CLOSESOCKET close
	#define BAD_SOCKET (-1)
#endif

#include "arc.h"
#include "arm.h"
#include "config.h"
#include "debugger.h"
#include "headless.h"
#include "ioc.h"
#include "mem.h"
#include "memc.h"
#include "timer.h"
#include "vidc.h"
#include "video.h"

/*Largest address the emulator will accept, matching the range check on the
  debugger's own 'save' command.*/
#define ADDR_LIMIT 0x4000000

#define MAX_PAYLOAD (16 * 1024 * 1024)

static SOCKET_T client_sock = BAD_SOCKET;

/*---------------------------------------------------------------------------
  Transport
  ---------------------------------------------------------------------------*/

static int send_all(const void *buf, size_t len)
{
	const char *p = (const char *)buf;

	while (len)
	{
		int n = send(client_sock, p, (int)len, 0);

		if (n <= 0)
			return -1;
		p += n;
		len -= n;
	}
	return 0;
}

static int respond(const char *status, const void *payload, size_t len)
{
	char header[64];
	int header_len = snprintf(header, sizeof(header), "%s %llu\n", status, (unsigned long long)len);

	if (send_all(header, header_len))
		return -1;
	if (len && send_all(payload, len))
		return -1;
	return 0;
}

static int respond_ok(const void *payload, size_t len)
{
	return respond("OK", payload, len);
}

static int respond_text(const char *fmt, ...)
{
	char buf[8192];
	va_list ap;
	int len;

	va_start(ap, fmt);
	len = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);

	if (len < 0)
		len = 0;
	if (len > (int)sizeof(buf) - 1)
		len = sizeof(buf) - 1;
	return respond("OK", buf, len);
}

static int respond_err(const char *fmt, ...)
{
	char buf[1024];
	va_list ap;
	int len;

	va_start(ap, fmt);
	len = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);

	if (len < 0)
		len = 0;
	if (len > (int)sizeof(buf) - 1)
		len = sizeof(buf) - 1;
	return respond("ERR", buf, len);
}

/*Read one \n-terminated line. Returns length, or -1 on disconnect/error.*/
static int read_line(char *buf, size_t size)
{
	static char pending[8192];
	static size_t pending_len;
	size_t i;
	int n;

	for (;;)
	{
		for (i = 0; i < pending_len; i++)
		{
			if (pending[i] == '\n')
			{
				size_t line_len = i;

				if (line_len && pending[line_len - 1] == '\r')
					line_len--;
				if (line_len >= size)
					line_len = size - 1;
				memcpy(buf, pending, line_len);
				buf[line_len] = 0;

				memmove(pending, &pending[i + 1], pending_len - i - 1);
				pending_len -= i + 1;
				return (int)line_len;
			}
		}

		if (pending_len == sizeof(pending))
			return -1;

		n = recv(client_sock, &pending[pending_len], (int)(sizeof(pending) - pending_len), 0);
		if (n <= 0)
			return -1;
		pending_len += n;
	}
}

/*---------------------------------------------------------------------------
  Helpers
  ---------------------------------------------------------------------------*/

/*Split a command line into whitespace-separated tokens in place.*/
static int tokenise(char *line, char **argv, int max)
{
	int argc = 0;
	char *p = line;

	while (*p && argc < max)
	{
		while (*p == ' ' || *p == '\t')
			p++;
		if (!*p)
			break;
		argv[argc++] = p;
		while (*p && *p != ' ' && *p != '\t')
			p++;
		if (*p)
			*p++ = 0;
	}
	return argc;
}

static int parse_u32(const char *s, uint32_t *out)
{
	char *end;
	unsigned long long v;

	if (!s || !*s)
		return 0;
	/*Accept 0x-prefixed, &-prefixed (Acorn convention) and bare hex.*/
	if (s[0] == '&')
		v = strtoull(s + 1, &end, 16);
	else if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X'))
		v = strtoull(s + 2, &end, 16);
	else
		v = strtoull(s, &end, 16);

	if (*end)
		return 0;
	*out = (uint32_t)v;
	return 1;
}

static int parse_i64(const char *s, int64_t *out)
{
	char *end;
	long long v;

	if (!s || !*s)
		return 0;
	v = strtoll(s, &end, 0);
	if (*end)
		return 0;
	*out = v;
	return 1;
}

static int hex_nibble(char c)
{
	if (c >= '0' && c <= '9') return c - '0';
	if (c >= 'a' && c <= 'f') return c - 'a' + 10;
	if (c >= 'A' && c <= 'F') return c - 'A' + 10;
	return -1;
}

/*Escape a string for embedding in JSON.*/
static void json_escape(const char *in, char *out, size_t out_size)
{
	size_t o = 0;

	for (; *in && o < out_size - 7; in++)
	{
		unsigned char c = (unsigned char)*in;

		switch (c)
		{
			case '"':  out[o++] = '\\'; out[o++] = '"';  break;
			case '\\': out[o++] = '\\'; out[o++] = '\\'; break;
			case '\n': out[o++] = '\\'; out[o++] = 'n';  break;
			case '\r': out[o++] = '\\'; out[o++] = 'r';  break;
			case '\t': out[o++] = '\\'; out[o++] = 't';  break;
			default:
			if (c < 0x20 || c >= 0x7f)
				o += snprintf(&out[o], out_size - o, "\\u%04x", c);
			else
				out[o++] = (char)c;
			break;
		}
	}
	out[o] = 0;
}

/*---------------------------------------------------------------------------
  Commands
  ---------------------------------------------------------------------------*/

#define NFSET ((armregs[15] & 0x80000000) ? 1 : 0)
#define ZFSET ((armregs[15] & 0x40000000) ? 1 : 0)
#define CFSET ((armregs[15] & 0x20000000) ? 1 : 0)
#define VFSET ((armregs[15] & 0x10000000) ? 1 : 0)
#define IFSET ((armregs[15] & 0x08000000) ? 1 : 0)
#define FFSET ((armregs[15] & 0x04000000) ? 1 : 0)

static const char *cpu_mode_names[4] = { "user", "fiq", "irq", "supervisor" };

static int cmd_regs(void)
{
	char buf[2048];
	int len, c, o = 0;

	o += snprintf(&buf[o], sizeof(buf) - o, "{\"r\":[");
	for (c = 0; c < 16; c++)
		o += snprintf(&buf[o], sizeof(buf) - o, "%s%u", c ? "," : "", armregs[c]);
	o += snprintf(&buf[o], sizeof(buf) - o, "],");
	o += snprintf(&buf[o], sizeof(buf) - o,
		"\"pc\":%u,\"mode\":\"%s\","
		"\"flags\":{\"n\":%d,\"z\":%d,\"c\":%d,\"v\":%d,\"i\":%d,\"f\":%d},"
		"\"cycles\":%llu}",
		(PC - 8) & 0x3fffffc, cpu_mode_names[armregs[15] & 3],
		NFSET, ZFSET, CFSET, VFSET, IFSET, FFSET,
		(unsigned long long)(tsc >> 32));

	len = o;
	return respond_ok(buf, len);
}

static int cmd_dump(const char *what)
{
	char buf[65536];

	if (!strcmp(what, "ioc"))
	{
		char tmp[8192];
		ioc_debug_print(tmp);
		return respond_ok(tmp, strlen(tmp));
	}
	if (!strcmp(what, "memc"))
	{
		char tmp[8192];
		memc_debug_print(tmp);
		return respond_ok(tmp, strlen(tmp));
	}
	if (!strcmp(what, "vidc"))
	{
		char tmp[8192];
		vidc_debug_print(tmp);
		return respond_ok(tmp, strlen(tmp));
	}
	if (!strcmp(what, "memc_cam"))
	{
		size_t len;

		debugger_capture_begin(buf, sizeof(buf));
		memc_debug_print_cam();
		len = debugger_capture_end();
		return respond_ok(buf, len);
	}
	return respond_err("unknown dump target '%s' (want ioc, memc, memc_cam or vidc)", what);
}

static int cmd_read(uint32_t addr, uint32_t len)
{
	uint8_t *buf;
	uint32_t i;
	int ret;

	if (len == 0)
		return respond_ok(NULL, 0);
	if (len > MAX_PAYLOAD)
		return respond_err("length %u exceeds maximum %u", len, MAX_PAYLOAD);
	if (addr >= ADDR_LIMIT || (uint64_t)addr + len > ADDR_LIMIT)
		return respond_err("address range %07x+%u is outside 0..%07x", addr, len, ADDR_LIMIT);

	buf = malloc(len);
	if (!buf)
		return respond_err("out of memory");

	for (i = 0; i < len; i++)
	{
		uint32_t a = addr + i;
		uint32_t word = readmemf_debug(a & ~3);

		buf[i] = (uint8_t)(word >> ((a & 3) * 8));
	}

	ret = respond_ok(buf, len);
	free(buf);
	return ret;
}

static int cmd_write(uint32_t addr, const char *hex)
{
	size_t hex_len = strlen(hex);
	size_t n = hex_len / 2;
	size_t i;

	if (hex_len & 1)
		return respond_err("hex payload has an odd number of digits");
	if (addr >= ADDR_LIMIT || (uint64_t)addr + n > ADDR_LIMIT)
		return respond_err("address range %07x+%llu is outside 0..%07x",
			addr, (unsigned long long)n, ADDR_LIMIT);

	for (i = 0; i < n; i++)
	{
		int hi = hex_nibble(hex[i * 2]);
		int lo = hex_nibble(hex[i * 2 + 1]);

		if (hi < 0 || lo < 0)
			return respond_err("bad hex digit at offset %llu", (unsigned long long)(i * 2));

		writememfb_debug(addr + (uint32_t)i, (uint8_t)((hi << 4) | lo));
	}

	return respond_text("%llu", (unsigned long long)n);
}

static int cmd_run(int64_t cycles)
{
	int64_t executed = 0;
	const char *messages = "";
	int reason;
	char escaped[8192];
	char buf[16384];
	int len;

	if (cycles < 0)
		return respond_err("cycle count must not be negative");

	reason = headless_run_cycles(cycles, &executed, &messages);
	json_escape(messages, escaped, sizeof(escaped));

	len = snprintf(buf, sizeof(buf),
		"{\"reason\":\"%s\",\"executed\":%lld,\"pc\":%u,\"cycles\":%llu,"
		"\"frames\":%llu,\"messages\":\"%s\"}",
		headless_stop_reason_name(reason), (long long)executed,
		(PC - 8) & 0x3fffffc, (unsigned long long)(tsc >> 32),
		(unsigned long long)headless_frame.frame_count, escaped);

	return respond_ok(buf, len);
}

static int cmd_step(int count)
{
	int64_t executed = 0;
	const char *messages = "";
	int reason;
	char escaped[8192];
	char buf[16384];
	int len;

	if (count < 1)
		count = 1;

	reason = headless_step(count, &executed, &messages);
	json_escape(messages, escaped, sizeof(escaped));

	len = snprintf(buf, sizeof(buf),
		"{\"reason\":\"%s\",\"executed\":%lld,\"pc\":%u,\"cycles\":%llu,\"messages\":\"%s\"}",
		headless_stop_reason_name(reason), (long long)executed,
		(PC - 8) & 0x3fffffc, (unsigned long long)(tsc >> 32), escaped);

	return respond_ok(buf, len);
}

static int cmd_screenshot(void)
{
	int w = 0, h = 0;
	uint32_t *pixels = headless_video_snapshot(&w, &h);
	uint8_t header[16];
	size_t pixel_bytes;
	uint8_t *payload;
	int ret;

	if (!pixels)
		return respond_err("no frame has been presented yet - run the machine first");

	pixel_bytes = (size_t)w * h * 4;

	/*'ARFB', width, height, bytes per pixel - all little-endian.*/
	memcpy(header, "ARFB", 4);
	header[4]  = (uint8_t)w;        header[5]  = (uint8_t)(w >> 8);
	header[6]  = (uint8_t)(w >> 16); header[7] = (uint8_t)(w >> 24);
	header[8]  = (uint8_t)h;        header[9]  = (uint8_t)(h >> 8);
	header[10] = (uint8_t)(h >> 16); header[11] = (uint8_t)(h >> 24);
	header[12] = 4; header[13] = 0; header[14] = 0; header[15] = 0;

	payload = malloc(sizeof(header) + pixel_bytes);
	if (!payload)
	{
		free(pixels);
		return respond_err("out of memory");
	}
	memcpy(payload, header, sizeof(header));
	memcpy(payload + sizeof(header), pixels, pixel_bytes);

	ret = respond_ok(payload, sizeof(header) + pixel_bytes);
	free(payload);
	free(pixels);
	return ret;
}

static int cmd_sound(const char *action)
{
	if (!strcmp(action, "start"))
	{
		headless_sound_capture_start();
		return respond_text("started");
	}
	if (!strcmp(action, "stop"))
	{
		headless_sound_capture_stop();
		return respond_text("stopped");
	}
	if (!strcmp(action, "fetch"))
	{
		size_t samples = 0;
		int16_t *data = headless_sound_fetch(&samples);
		uint8_t header[16];
		uint8_t *payload;
		size_t bytes = samples * sizeof(int16_t);
		int ret;

		/*'ARSN', sample rate, channels, frame count - all little-endian.*/
		memcpy(header, "ARSN", 4);
		header[4] = (uint8_t)headless_sound_rate;
		header[5] = (uint8_t)(headless_sound_rate >> 8);
		header[6] = (uint8_t)(headless_sound_rate >> 16);
		header[7] = (uint8_t)(headless_sound_rate >> 24);
		header[8] = 2; header[9] = 0; header[10] = 0; header[11] = 0;
		header[12] = (uint8_t)(samples / 2);
		header[13] = (uint8_t)((samples / 2) >> 8);
		header[14] = (uint8_t)((samples / 2) >> 16);
		header[15] = (uint8_t)((samples / 2) >> 24);

		payload = malloc(sizeof(header) + bytes);
		if (!payload)
		{
			free(data);
			return respond_err("out of memory");
		}
		memcpy(payload, header, sizeof(header));
		if (bytes)
			memcpy(payload + sizeof(header), data, bytes);

		ret = respond_ok(payload, sizeof(header) + bytes);
		free(payload);
		free(data);
		return ret;
	}
	return respond_err("unknown sound action '%s' (want start, stop or fetch)", action);
}

static int breakpoint_list_json(void (*list_fn)(int32_t *), const char *label)
{
	int32_t list[8];
	char buf[512];
	int o = 0, c, first = 1;

	list_fn(list);
	o += snprintf(&buf[o], sizeof(buf) - o, "{\"%s\":[", label);
	for (c = 0; c < 8; c++)
	{
		if (list[c] != -1)
		{
			o += snprintf(&buf[o], sizeof(buf) - o, "%s%u", first ? "" : ",", (uint32_t)list[c]);
			first = 0;
		}
	}
	o += snprintf(&buf[o], sizeof(buf) - o, "]}");
	return respond_ok(buf, o);
}

static int cmd_breakpoint(int argc, char **argv,
		int (*set_fn)(uint32_t), int (*clear_fn)(uint32_t),
		void (*list_fn)(int32_t *), const char *label)
{
	uint32_t addr;

	if (argc < 2)
		return respond_err("%s: want add <addr>, del <addr> or list", label);

	if (!strcmp(argv[1], "list"))
		return breakpoint_list_json(list_fn, label);

	if (argc < 3 || !parse_u32(argv[2], &addr))
		return respond_err("%s %s: expected a hex address", label, argv[1]);

	if (!strcmp(argv[1], "add"))
	{
		int slot = set_fn(addr);

		if (slot < 0)
			return respond_err("all 8 %s slots are in use", label);
		return respond_text("%d", slot);
	}
	if (!strcmp(argv[1], "del"))
		return respond_text("%d", clear_fn(addr));

	return respond_err("%s: unknown action '%s'", label, argv[1]);
}

static int cmd_trap(const char *name, int enable)
{
	static const struct { const char *name; int trap; } traps[] =
	{
		{ "prefabort", DEBUG_TRAP_PREF_ABORT },
		{ "dataabort", DEBUG_TRAP_DATA_ABORT },
		{ "addrexcep", DEBUG_TRAP_ADDR_EXCEP },
		{ "undefins",  DEBUG_TRAP_UNDEF },
		{ "swi",       DEBUG_TRAP_SWI },
	};
	size_t c;

	for (c = 0; c < sizeof(traps) / sizeof(traps[0]); c++)
	{
		if (!strcmp(name, traps[c].name))
		{
			debugger_trap_set(traps[c].trap, enable);
			return respond_text("%s %s", traps[c].name, enable ? "enabled" : "disabled");
		}
	}
	return respond_err("unknown trap '%s' (want prefabort, dataabort, addrexcep, undefins or swi)", name);
}

static int cmd_info(void)
{
	char buf[2048];
	char config_name[512];
	int len;

	json_escape(machine_config_name, config_name, sizeof(config_name));

	len = snprintf(buf, sizeof(buf),
		"{\"version\":\"%s\",\"config\":\"%s\",\"speed_mhz\":%d,\"memsize_kb\":%d,"
		"\"cycles\":%llu,\"frames\":%llu,"
		"\"display\":{\"valid\":%d,\"width\":%d,\"height\":%d,\"dblscan\":%d}}",
		VERSION_STRING, config_name, speed_mhz, memsize,
		(unsigned long long)(tsc >> 32),
		(unsigned long long)headless_frame.frame_count,
		headless_frame.valid, headless_frame.w,
		headless_frame.dblscan ? headless_frame.h * 2 : headless_frame.h,
		headless_frame.dblscan);

	return respond_ok(buf, len);
}

/*---------------------------------------------------------------------------
  Dispatch
  ---------------------------------------------------------------------------*/

static int dispatch(char *line)
{
	char *argv[8];
	int argc = tokenise(line, argv, 8);
	uint32_t addr, val;
	int64_t n;

	if (argc == 0)
		return respond_ok(NULL, 0);

	if (!strcmp(argv[0], "ping"))
		return respond_text("pong");

	if (!strcmp(argv[0], "quit"))
	{
		headless_quit = 1;
		return respond_text("bye");
	}

	if (!strcmp(argv[0], "info"))
		return cmd_info();

	if (!strcmp(argv[0], "reset"))
	{
		arc_do_reset();
		return respond_text("reset");
	}

	if (!strcmp(argv[0], "regs"))
		return cmd_regs();

	if (!strcmp(argv[0], "dump"))
	{
		if (argc < 2)
			return respond_err("dump: want ioc, memc, memc_cam or vidc");
		return cmd_dump(argv[1]);
	}

	if (!strcmp(argv[0], "run"))
	{
		if (argc < 2 || !parse_i64(argv[1], &n))
			return respond_err("run: expected a cycle count");
		return cmd_run(n);
	}

	if (!strcmp(argv[0], "run_us"))
	{
		if (argc < 2 || !parse_i64(argv[1], &n))
			return respond_err("run_us: expected a duration in microseconds");
		return cmd_run(n * speed_mhz);
	}

	if (!strcmp(argv[0], "step"))
	{
		n = 1;
		if (argc >= 2 && !parse_i64(argv[1], &n))
			return respond_err("step: expected an instruction count");
		return cmd_step((int)n);
	}

	if (!strcmp(argv[0], "dis"))
	{
		char buf[65536];
		int count = 16;

		if (argc < 2 || !parse_u32(argv[1], &addr))
			return respond_err("dis: expected a hex address");
		if (argc >= 3)
		{
			if (!parse_i64(argv[2], &n))
				return respond_err("dis: expected an instruction count");
			count = (int)n;
		}
		if (count < 1 || count > 1024)
			return respond_err("dis: instruction count must be 1..1024");

		debugger_disassemble(addr, count, buf, sizeof(buf));
		return respond_ok(buf, strlen(buf));
	}

	if (!strcmp(argv[0], "read"))
	{
		if (argc < 3 || !parse_u32(argv[1], &addr) || !parse_u32(argv[2], &val))
			return respond_err("read: expected <hex addr> <hex length>");
		return cmd_read(addr, val);
	}

	if (!strcmp(argv[0], "write"))
	{
		if (argc < 3 || !parse_u32(argv[1], &addr))
			return respond_err("write: expected <hex addr> <hex bytes>");
		return cmd_write(addr, argv[2]);
	}

	if (!strcmp(argv[0], "writel"))
	{
		if (argc < 3 || !parse_u32(argv[1], &addr) || !parse_u32(argv[2], &val))
			return respond_err("writel: expected <hex addr> <hex word>");
		if (addr >= ADDR_LIMIT)
			return respond_err("address %07x is outside 0..%07x", addr, ADDR_LIMIT);
		writememfl_debug(addr, val);
		return respond_text("ok");
	}

	if (!strcmp(argv[0], "key"))
	{
		if (argc < 3 || !parse_i64(argv[1], &n))
			return respond_err("key: expected <scancode> <0|1>");
		headless_key_set((int)n, atoi(argv[2]));
		return respond_text("ok");
	}

	if (!strcmp(argv[0], "keyclear"))
	{
		headless_key_clear_all();
		return respond_text("ok");
	}

	if (!strcmp(argv[0], "mouse"))
	{
		if (argc < 4)
			return respond_err("mouse: expected <dx> <dy> <buttons>");
		headless_mouse_move(atoi(argv[1]), atoi(argv[2]));
		headless_mouse_buttons(atoi(argv[3]));
		return respond_text("ok");
	}

	if (!strcmp(argv[0], "bp"))
		return cmd_breakpoint(argc, argv, debugger_breakpoint_set,
			debugger_breakpoint_clear, debugger_breakpoint_list, "bp");

	if (!strcmp(argv[0], "wbp"))
		return cmd_breakpoint(argc, argv, debugger_wbreakpoint_set,
			debugger_wbreakpoint_clear, debugger_wbreakpoint_list, "wbp");

	if (!strcmp(argv[0], "wp"))
		return cmd_breakpoint(argc, argv, debugger_watchpoint_set,
			debugger_watchpoint_clear, debugger_watchpoint_list, "wp");

	if (!strcmp(argv[0], "bpclear"))
	{
		debugger_clear_all();
		return respond_text("ok");
	}

	if (!strcmp(argv[0], "trap"))
	{
		if (argc < 3)
			return respond_err("trap: expected <name> <0|1>");
		return cmd_trap(argv[1], atoi(argv[2]));
	}

	if (!strcmp(argv[0], "screenshot"))
		return cmd_screenshot();

	if (!strcmp(argv[0], "sound"))
	{
		if (argc < 2)
			return respond_err("sound: want start, stop or fetch");
		return cmd_sound(argv[1]);
	}

	if (!strcmp(argv[0], "disc"))
	{
		if (argc < 3)
			return respond_err("disc: expected <drive> <path>");
		n = atoi(argv[1]);
		if (n < 0 || n > 3)
			return respond_err("disc: drive must be 0..3");
		arc_disc_change((int)n, argv[2]);
		return respond_text("ok");
	}

	if (!strcmp(argv[0], "eject"))
	{
		if (argc < 2)
			return respond_err("eject: expected <drive>");
		n = atoi(argv[1]);
		if (n < 0 || n > 3)
			return respond_err("eject: drive must be 0..3");
		arc_disc_eject((int)n);
		return respond_text("ok");
	}

	return respond_err("unknown command '%s'", argv[0]);
}

/*---------------------------------------------------------------------------
  Server
  ---------------------------------------------------------------------------*/

int headless_control_run(int port)
{
	SOCKET_T listen_sock;
	struct sockaddr_in addr;
	socklen_t addr_len = sizeof(addr);
	int one = 1;
	char line[8192];

#ifdef _WIN32
	WSADATA wsa;

	if (WSAStartup(MAKEWORD(2, 2), &wsa))
	{
		fprintf(stderr, "WSAStartup failed\n");
		return 1;
	}
#endif

	listen_sock = socket(AF_INET, SOCK_STREAM, 0);
	if (listen_sock == BAD_SOCKET)
	{
		fprintf(stderr, "Could not create control socket\n");
		return 1;
	}

	setsockopt(listen_sock, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof(one));

	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	addr.sin_port = htons((uint16_t)port);

	if (bind(listen_sock, (struct sockaddr *)&addr, sizeof(addr)))
	{
		fprintf(stderr, "Could not bind control socket to port %d\n", port);
		CLOSESOCKET(listen_sock);
		return 1;
	}
	if (listen(listen_sock, 1))
	{
		fprintf(stderr, "Could not listen on control socket\n");
		CLOSESOCKET(listen_sock);
		return 1;
	}

	if (getsockname(listen_sock, (struct sockaddr *)&addr, &addr_len) == 0)
		port = ntohs(addr.sin_port);

	/*The controlling process reads this to discover the port when it asked for
	  an ephemeral one.*/
	printf("PORT %d\n", port);
	fflush(stdout);

	addr_len = sizeof(addr);
	client_sock = accept(listen_sock, (struct sockaddr *)&addr, &addr_len);
	if (client_sock == BAD_SOCKET)
	{
		fprintf(stderr, "accept() on control socket failed\n");
		CLOSESOCKET(listen_sock);
		return 1;
	}

	/*Responses are small and latency matters more than packet count.*/
	setsockopt(client_sock, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof(one));

	while (!headless_quit)
	{
		int len = read_line(line, sizeof(line));

		if (len < 0)
			break;
		if (dispatch(line))
			break;
	}

	CLOSESOCKET(client_sock);
	CLOSESOCKET(listen_sock);
#ifdef _WIN32
	WSACleanup();
#endif
	return 0;
}
