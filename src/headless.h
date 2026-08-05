/*Arculator headless front-end.

  Shared declarations for the headless (MCP-driven) build. This build replaces
  the wxWidgets/SDL main window with a line-oriented control socket, and runs
  the emulator in lockstep with the controlling process rather than against
  wall-clock time.*/
#ifndef _HEADLESS_H_
#define _HEADLESS_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*---------------------------------------------------------------------------
  Video capture (headless_video.c)
  ---------------------------------------------------------------------------*/

/*Dimensions of the internal composition buffer. Matches the 2048x2048 texture
  used by the SDL renderer, and the clipping limits in video_renderer_update().*/
#define HEADLESS_FB_W 2048
#define HEADLESS_FB_H 2048

/*Last rectangle passed to video_renderer_present(), i.e. the part of the
  composition buffer that a real renderer would have shown.*/
typedef struct headless_frame_t
{
	int valid;              /*Zero until the guest has presented a frame*/
	int x, y, w, h;         /*Visible rect within the composition buffer*/
	int dblscan;            /*Guest asked for line doubling*/
	uint64_t frame_count;   /*Incremented on every present*/
} headless_frame_t;

extern headless_frame_t headless_frame;

/*Copy the visible rect into a freshly allocated buffer of w*h uint32_t in
  0x00RRGGBB host order (little-endian byte order B,G,R,X). Caller frees.
  Returns NULL if no frame has been presented yet. If dblscan is set the rows
  are duplicated, so the result is what a viewer would actually see.*/
uint32_t *headless_video_snapshot(int *w, int *h);

/*---------------------------------------------------------------------------
  Input injection (headless_input.c)
  ---------------------------------------------------------------------------*/

/*key[] is indexed by SDL scancode, exactly as the SDL input backend does.
  These set the state that keyboard_poll_host() will publish on the next slice.*/
void headless_key_set(int scancode, int down);
void headless_key_clear_all(void);

/*Mouse. Deltas accumulate and are consumed by the next mouse_get_mickeys().*/
void headless_mouse_move(int dx, int dy);
void headless_mouse_buttons(int mask);

/*---------------------------------------------------------------------------
  Sound capture (headless_sound.c)
  ---------------------------------------------------------------------------*/

/*Begin/end accumulating the 16-bit stereo stream the VIDC hands to the sound
  backend. Returns 0 on success. headless_sound_fetch() hands over ownership of
  the captured buffer and resets the accumulator.*/
int headless_sound_capture_start(void);
void headless_sound_capture_stop(void);
int16_t *headless_sound_fetch(size_t *samples);
extern int headless_sound_rate;

/*---------------------------------------------------------------------------
  Run control (headless_main.c)
  ---------------------------------------------------------------------------*/

/*Reasons a run request stopped early.*/
enum
{
	HEADLESS_STOP_BUDGET = 0,   /*Ran the requested number of cycles*/
	HEADLESS_STOP_BREAKPOINT,
	HEADLESS_STOP_WATCHPOINT,
	HEADLESS_STOP_TRAP,         /*Abort/undefined instruction/SWI trap*/
	HEADLESS_STOP_STEP,         /*Completed a single-step request*/
	HEADLESS_STOP_PREDICATE     /*A run_until predicate matched*/
};

extern int headless_quit;

const char *headless_stop_reason_name(int reason);

/*Advance the machine by up to `cycles` CPU cycles, or single-step `count`
  instructions, stopping early on a breakpoint or an enabled trap. Return the
  stop reason; *executed receives the cycles actually consumed and *messages
  points at any text the debugger emitted during the run (breakpoint hits,
  watchpoint traces, trap reports).*/
int headless_run_cycles(int64_t cycles, int64_t *executed, const char **messages);
int headless_step(int count, int64_t *executed, const char **messages);

/*---------------------------------------------------------------------------
  Control socket (headless_control.c)
  ---------------------------------------------------------------------------*/

/*Bind to the given TCP port on the loopback interface (0 to let the OS pick
  one), announce it on stdout as "PORT <n>", then serve control commands until
  the client disconnects or sends quit.*/
int headless_control_run(int port);

#ifdef __cplusplus
}
#endif

#endif /*_HEADLESS_H_*/
