#ifndef __DEBUGGER_H__
#define __DEBUGGER_H__

#include <stddef.h>
#include <stdint.h>

enum
{
	DEBUG_TRAP_PREF_ABORT = 0,
	DEBUG_TRAP_DATA_ABORT,
	DEBUG_TRAP_ADDR_EXCEP,
	DEBUG_TRAP_UNDEF,
	DEBUG_TRAP_SWI
};

void debug_start(void);
void debug_kill(void);
void debug_end(void);
//void debug_read(uint16_t addr);
//void debug_write(uint16_t addr, uint8_t val);
void debugger_do();
void debug_out(char *s);
void debug_trap(int trap, uint32_t opcode);
void debug_writememb(uint32_t a, uint8_t v);
void debug_writememl(uint32_t a, uint32_t v);

void debugger_start_reset(void);
void debugger_end_reset(void);

extern int debug, debugon;
extern int debugger_in_reset;
extern int indebug;

void console_output(char *s);
int console_input_get(char *s);
void console_input_disable();
void console_input_enable();

#define CONSOLE_INPUT_GET_ERROR_WINDOW_CLOSED -1
#define CONSOLE_INPUT_GET_ERROR_IN_RESET -2

/*Redirect debug_out() into a caller-supplied buffer. Used to reuse the
  debugger's formatting from an automated front-end.*/
void debugger_capture_begin(char *buf, size_t size);
size_t debugger_capture_end(void);

/*When installed, replaces the interactive REPL in debugger_do(). Called on the
  emulation thread with the PC of the stopped instruction.*/
extern void (*debugger_stop_handler)(uint32_t pc);

/*Programmatic equivalents of the break/breakw/watchw/bclear/wclear/t/s
  commands. Breakpoint setters return the slot used, or -1 if all 8 are taken;
  clearers return non-zero if an entry was removed.*/
int debugger_breakpoint_set(uint32_t addr);
int debugger_breakpoint_clear(uint32_t addr);
int debugger_wbreakpoint_set(uint32_t addr);
int debugger_wbreakpoint_clear(uint32_t addr);
int debugger_watchpoint_set(uint32_t addr);
int debugger_watchpoint_clear(uint32_t addr);
void debugger_breakpoint_list(int32_t *out);   /*8 entries, -1 where unused*/
void debugger_wbreakpoint_list(int32_t *out);
void debugger_watchpoint_list(int32_t *out);
void debugger_clear_all(void);
int debugger_any_armed(void);
void debugger_trap_set(int trap, int enable);
uint32_t debugger_trap_mask(void);
void debugger_step(int count);
void debugger_disassemble(uint32_t addr, int count, char *buf, size_t size);

#endif /*__DEBUGGER_H__*/