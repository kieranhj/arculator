/*Arculator headless front-end - input injection backend.

  Implements the plat_input.h contract by publishing a synthetic key/mouse
  state supplied over the control socket, in place of reading the host.

  The only contract the emulator has for keyboard input is the key[512] array,
  indexed by SDL scancode (see keyboard_sdl.h), which keyboard_poll_host() is
  expected to refresh once per emulated slice. keyboard.c then feeds that into
  the IOC keyboard serial protocol at its own pace, which is why a key must be
  held across several slices to register - the controlling process decides how
  many cycles elapse between a key_down and the matching key_up.*/
#include <string.h>
#include "arc.h"
#include "headless.h"
#include "plat_input.h"
#include "plat_joystick.h"

int key[512];
int mouse[3];

/*Desired key state, published into key[] by keyboard_poll_host().*/
static int key_pending[512];

static int mouse_dx, mouse_dy;
static int mouse_buttons;

void headless_key_set(int scancode, int down)
{
	if (scancode < 0 || scancode >= 512)
		return;
	key_pending[scancode] = down ? 1 : 0;
}

void headless_key_clear_all(void)
{
	memset(key_pending, 0, sizeof(key_pending));
}

void headless_mouse_move(int dx, int dy)
{
	mouse_dx += dx;
	mouse_dy += dy;
}

void headless_mouse_buttons(int mask)
{
	mouse_buttons = mask & 7;
}

void keyboard_poll_host(void)
{
	memcpy(key, key_pending, sizeof(key));
}

void mouse_poll_host(void)
{
	mouse[0] = mouse_dx;
	mouse[1] = mouse_dy;
	mouse[2] = mouse_buttons;
}

void mouse_get_mickeys(int *x, int *y)
{
	*x = mouse_dx;
	*y = mouse_dy;
	mouse_dx = mouse_dy = 0;
}

int mouse_get_buttons(void)
{
	return mouse_buttons;
}

void get_mouse_deltas(int *x, int *y, int *b)
{
	*x = mouse_dx;
	*y = mouse_dy;
	*b = mouse_buttons;
	mouse_dx = mouse_dy = 0;
}

/*There is no window to capture into; the guest always sees the injected state.*/
void mouse_capture_enable(void)
{
}

void mouse_capture_disable(void)
{
}

void input_init(void)
{
	memset(key, 0, sizeof(key));
	memset(key_pending, 0, sizeof(key_pending));
	mouse_dx = mouse_dy = mouse_buttons = 0;
}

void input_close(void)
{
}

/*---------------------------------------------------------------------------
  Joysticks - none are present in a headless session.
  ---------------------------------------------------------------------------*/

plat_joystick_t plat_joystick_state[MAX_PLAT_JOYSTICKS];
int joysticks_present = 0;

void joystick_init(void)
{
	joysticks_present = 0;
}

void joystick_close(void)
{
}

void joystick_poll_host(void)
{
}
