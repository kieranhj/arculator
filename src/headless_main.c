/*Arculator headless front-end - startup, run control and UI stubs.

  This is a third implementation of the arc_* emulator/UI boundary, alongside
  wx-sdl2.c and wx-mainwindow.cc, intended to be driven by another process over
  a control socket rather than by a person.

  It differs from the other two in one important way: it is single threaded and
  runs in lockstep with the controller. There is no emulation thread and no
  main_thread_mutex, because there is no GUI thread to race against - a control
  command is serviced, the machine is advanced by an explicit number of cycles,
  and the result is reported. Nothing advances the machine between commands.
  That removes a whole class of races and, more importantly, makes a session
  reproducible: the same command sequence produces the same execution.*/
#include <stdarg.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "arc.h"
#include "arm.h"
#include "config.h"
#include "debugger.h"
#include "disc.h"
#include "headless.h"
#include "hostfs.h"
#include "ioc.h"
#include "keyboard.h"
#include "plat_input.h"
#include "plat_joystick.h"
#include "plat_video.h"
#include "podules.h"
#include "timer.h"
#include "vidc.h"
#include "video.h"

int headless_quit = 0;

/*Set by the debugger stop handler when a breakpoint or trap fires.*/
static int run_stopped;
static int run_stop_reason;

/*Messages emitted by the debugger during a run - breakpoint hits, watchpoint
  traces, trap reports. Captured rather than printed so they can be returned to
  the controller.*/
#define DEBUG_MSG_SIZE 65536
static char debug_msgs[DEBUG_MSG_SIZE];

const char *headless_stop_reason_name(int reason)
{
	switch (reason)
	{
		case HEADLESS_STOP_BUDGET:     return "budget";
		case HEADLESS_STOP_BREAKPOINT: return "breakpoint";
		case HEADLESS_STOP_WATCHPOINT: return "watchpoint";
		case HEADLESS_STOP_TRAP:       return "trap";
		case HEADLESS_STOP_STEP:       return "step";
		case HEADLESS_STOP_PREDICATE:  return "predicate";
		default:                       return "unknown";
	}
}

/*Called from debugger_do() on the instruction that triggered the stop, in
  place of the interactive REPL. Unwinds out of execarm() immediately so no
  further instructions execute.*/
static void headless_stop_handler(uint32_t pc)
{
	run_stopped = 1;

	/*The debugger has already described the cause in the captured message
	  buffer; classify from that so the controller gets a machine-readable
	  reason as well as the text.*/
	if (strstr(debug_msgs, "abort") || strstr(debug_msgs, "Undefined") ||
	    strstr(debug_msgs, "Address exception") || strstr(debug_msgs, "SWI"))
		run_stop_reason = HEADLESS_STOP_TRAP;
	else if (strstr(debug_msgs, "(write"))
		run_stop_reason = HEADLESS_STOP_BREAKPOINT;
	else if (debug_msgs[0])
		run_stop_reason = HEADLESS_STOP_BREAKPOINT;
	else
		run_stop_reason = HEADLESS_STOP_STEP;

	arm_break_execution();
}

/*One emulated slice. Mirrors arc_run() in main.c, but with an explicit cycle
  count and without the drive-noise mixing and CMOS autosave, which are
  wall-clock-driven side effects that would make runs non-reproducible.*/
static void headless_slice(int cycles)
{
	joystick_poll_host();
	mouse_poll_host();
	keyboard_poll_host();
	if (mousehack)
		doosmouse();
	execarm(cycles);
}

/*Advance the machine by up to `cycles` CPU cycles, stopping early on a
  breakpoint or trap. Returns the stop reason; *executed receives the number of
  cycles actually consumed, measured from the global 32:32 timestamp counter.*/
int headless_run_cycles(int64_t cycles, int64_t *executed, const char **messages)
{
	/*Slice at 10 ms of emulated time so host input is republished at roughly
	  the rate the interactive builds use, and so a long run still services
	  timers in the usual pattern.*/
	int64_t slice_cycles = (speed_mhz * 1000000ll) / 100;
	uint64_t start_tsc = tsc;

	if (slice_cycles < 1)
		slice_cycles = 1;

	run_stopped = 0;
	run_stop_reason = HEADLESS_STOP_BUDGET;

	debugger_capture_begin(debug_msgs, DEBUG_MSG_SIZE);

	/*The per-instruction debugger hook is only worth paying for when something
	  is actually armed.*/
	debugon = debugger_any_armed() || debug;

	while (cycles > 0 && !run_stopped)
	{
		int this_slice = (cycles < slice_cycles) ? (int)cycles : (int)slice_cycles;

		headless_slice(this_slice);
		cycles -= this_slice;
	}

	debugger_capture_end();

	*executed = (int64_t)((tsc - start_tsc) >> 32);
	*messages = debug_msgs;
	return run_stopped ? run_stop_reason : HEADLESS_STOP_BUDGET;
}

/*Single-step n instructions.*/
int headless_step(int count, int64_t *executed, const char **messages)
{
	uint64_t start_tsc = tsc;
	/*Generous ceiling - a single instruction can stall for a long time on slow
	  memory, but not for millions of cycles.*/
	int64_t budget = (int64_t)count * 4096 + 4096;

	run_stopped = 0;
	run_stop_reason = HEADLESS_STOP_STEP;

	debugger_capture_begin(debug_msgs, DEBUG_MSG_SIZE);
	debugger_step(count);
	debugon = 1;

	while (budget > 0 && !run_stopped)
	{
		headless_slice(64);
		budget -= 64;
	}

	debugger_capture_end();
	debug = 0;
	debugon = debugger_any_armed();

	*executed = (int64_t)((tsc - start_tsc) >> 32);
	*messages = debug_msgs;
	return run_stopped ? run_stop_reason : HEADLESS_STOP_BUDGET;
}

/*---------------------------------------------------------------------------
  arc_* boundary. Single threaded, so the mutex discipline the other two
  front-ends need does not apply here.
  ---------------------------------------------------------------------------*/

void arc_start_main_thread(void *wx_window, void *wx_menu)
{
}

void arc_stop_main_thread(void)
{
	headless_quit = 1;
}

void arc_pause_main_thread(void)
{
}

void arc_resume_main_thread(void)
{
}

void arc_do_reset(void)
{
	debugger_start_reset();
	arc_reset();
	debugger_end_reset();
}

void arc_disc_change(int drive, char *fn)
{
	rpclog("arc_disc_change: drive=%i fn=%s\n", drive, fn);

	disc_close(drive);
	strcpy(discname[drive], fn);
	disc_load(drive, discname[drive]);
	ioc_discchange(drive);
}

void arc_disc_eject(int drive)
{
	rpclog("arc_disc_eject: drive=%i\n", drive);

	ioc_discchange(drive);
	disc_close(drive);
	discname[drive][0] = 0;
}

void arc_renderer_reset(void)
{
}

void arc_set_display_mode(int new_display_mode)
{
	display_mode = new_display_mode;
	clearbitmap();
	setredrawall();
}

void arc_set_dblscan(int new_dblscan)
{
	dblscan = new_dblscan;
	clearbitmap();
}

void arc_set_resizeable(void)
{
}

void arc_enter_fullscreen(void)
{
}

void arc_stop_emulation(void)
{
	headless_quit = 1;
}

/*---------------------------------------------------------------------------
  UI stubs. The emulation core calls into the front-end for a handful of things
  that have no meaning without a window.
  ---------------------------------------------------------------------------*/

void updatewindowsize(int x, int y)
{
	/*Screen mode changes are observable through the presented frame geometry,
	  so nothing needs to happen here.*/
}

void arc_update_menu(void)
{
}

void arc_popup_menu(void)
{
}

void arc_print_error(const char *format, ...)
{
	va_list ap;

	va_start(ap, format);
	vfprintf(stderr, format, ap);
	va_end(ap);
	fputc('\n', stderr);
}

/*The debugger console. The headless build captures debug_out() directly, so
  these only need to exist for linking, and to keep the debugger from blocking
  if its REPL is ever reached.*/
void console_output(char *s)
{
	fputs(s, stderr);
}

int console_input_get(char *s)
{
	return CONSOLE_INPUT_GET_ERROR_WINDOW_CLOSED;
}

void console_input_enable(void)
{
}

void console_input_disable(void)
{
}

int podule_config_file_selector(void *window_p, const char *title, const char *default_path,
		const char *default_fn, const char *default_ext, const char *wildcard,
		char *dest, int dest_len, int flags)
{
	/*No interactive file selection in a headless session.*/
	return 0;
}

/*Podule configuration dialogs. Podules are configured from the machine .cfg
  before startup, so there is nothing to open.*/
int podule_config_open(void *window_p, podule_config_t *config, const char *prefix)
{
	return 0;
}

void *podule_config_get_current(void *window_p, int id)
{
	return NULL;
}

void podule_config_set_current(void *window_p, int id, void *val)
{
}

/*Joystick mappings. config.c reads and writes these, so the storage has to
  exist even though no joystick is ever present.*/
joystick_t joystick_state[MAX_JOYSTICKS];

/*---------------------------------------------------------------------------
  Startup
  ---------------------------------------------------------------------------*/

static void usage(const char *argv0)
{
	fprintf(stderr,
		"Usage: %s [options]\n"
		"\n"
		"  --port <n>        TCP port for the control socket (default 0 = pick one\n"
		"                    and print it as 'PORT <n>' on stdout)\n"
		"  --config <name>   Machine configuration name from configs/ (default: the\n"
		"                    last one used, per arc.cfg)\n"
		"  --config-file <p> Machine configuration file, by full path\n"
		"  --hostfs <path>   Directory to expose as HostFS, instead of <exe>/hostfs\n"
		"  --root <path>     Directory holding roms/, cmos/ and configs/, if not the\n"
		"                    directory containing this executable\n"
		"  --persist         Write back arc.cfg and the CMOS image on exit. Off by\n"
		"                    default so a session leaves its inputs untouched and\n"
		"                    stays reproducible.\n"
		"  --help            This message\n",
		argv0);
}

int main(int argc, char **argv)
{
	int port = 0;
	const char *config_name = NULL;
	const char *config_file = NULL;
	const char *hostfs_path = NULL;
	const char *root = NULL;
	int c;

	/*Opt in rather than out - see config_persist_enabled in config.c.*/
	config_persist_enabled = 0;

	for (c = 1; c < argc; c++)
	{
		if (!strcmp(argv[c], "--port") && (c + 1) < argc)
			port = atoi(argv[++c]);
		else if (!strcmp(argv[c], "--config") && (c + 1) < argc)
			config_name = argv[++c];
		else if (!strcmp(argv[c], "--config-file") && (c + 1) < argc)
			config_file = argv[++c];
		else if (!strcmp(argv[c], "--hostfs") && (c + 1) < argc)
			hostfs_path = argv[++c];
		else if (!strcmp(argv[c], "--root") && (c + 1) < argc)
			root = argv[++c];
		else if (!strcmp(argv[c], "--persist"))
			config_persist_enabled = 1;
		else if (!strcmp(argv[c], "--help"))
		{
			usage(argv[0]);
			return 0;
		}
		else
		{
			fprintf(stderr, "Unrecognised option '%s'\n", argv[c]);
			usage(argv[0]);
			return 1;
		}
	}

	/*exname is the directory holding roms/, cmos/, configs/ and podules/.
	  Everything else resolves relative to it.*/
	if (root)
	{
		size_t len = strlen(root);

		strncpy(exname, root, sizeof(exname) - 2);
		exname[sizeof(exname) - 2] = 0;
		len = strlen(exname);
		if (len && exname[len - 1] != '/' && exname[len - 1] != '\\')
		{
			exname[len] = '/';
			exname[len + 1] = 0;
		}
	}
	else
	{
		char *p;

		strncpy(exname, argv[0], sizeof(exname) - 1);
		exname[sizeof(exname) - 1] = 0;
		p = (char *)get_filename(exname);
		*p = 0;
	}

	if (hostfs_path)
	{
		strncpy(hostfs_root_override, hostfs_path, sizeof(hostfs_root_override) - 1);
		hostfs_root_override[sizeof(hostfs_root_override) - 1] = 0;
	}

	if (config_file)
	{
		strncpy(machine_config_file, config_file, sizeof(machine_config_file) - 1);
		machine_config_file[sizeof(machine_config_file) - 1] = 0;
		strncpy(machine_config_name, config_name ? config_name : "headless", sizeof(machine_config_name) - 1);
		machine_config_name[sizeof(machine_config_name) - 1] = 0;
	}
	else if (config_name)
	{
		snprintf(machine_config_file, sizeof(machine_config_file), "%sconfigs/%s.cfg", exname, config_name);
		strncpy(machine_config_name, config_name, sizeof(machine_config_name) - 1);
		machine_config_name[sizeof(machine_config_name) - 1] = 0;
	}

	podule_build_list();
	opendlls();

	if (arc_init())
	{
		fprintf(stderr, "Emulator initialisation failed - check that the ROM set named by "
				"the machine configuration is present under %sroms/\n", exname);
		return 1;
	}

	if (!video_renderer_init(NULL))
	{
		fprintf(stderr, "Video capture initialisation failed\n");
		return 1;
	}
	input_init();

	debugger_stop_handler = headless_stop_handler;

	c = headless_control_run(port);

	arc_close();
	input_close();
	video_renderer_close();

	return c;
}
