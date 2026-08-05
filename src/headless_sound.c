/*Arculator headless front-end - sound backend.

  Implements the plat_sound.h contract with no audio device. Buffers handed
  over by sound.c can optionally be accumulated so the controlling process can
  fetch the emitted waveform and assert on it.

  sound.c produces 2400 stereo frames per buffer at 48 kHz (see update_sound()
  and OUTPUT_FREQ in sound_sdl2.c).*/
#include <stdlib.h>
#include <string.h>
#include "arc.h"
#include "headless.h"
#include "plat_sound.h"

#define SOUND_FRAMES_PER_BUFFER 2400
#define SOUND_CHANNELS          2

/*Cap the capture so a runaway session cannot exhaust memory. 48 kHz stereo
  int16 is 192 KB/sec, so this is a little over five minutes of audio.*/
#define SOUND_CAPTURE_MAX_SAMPLES (48000 * SOUND_CHANNELS * 330)

int headless_sound_rate = 48000;

static int capturing;
static int16_t *capture_buf;
static size_t capture_len;      /*In int16 samples, i.e. frames * channels*/
static size_t capture_alloc;
static int capture_overflowed;

void sound_dev_init(void)
{
}

void sound_dev_close(void)
{
	free(capture_buf);
	capture_buf = NULL;
	capture_len = capture_alloc = 0;
	capturing = 0;
}

static void capture_append(int16_t *buf, size_t samples)
{
	if (!capturing || capture_overflowed)
		return;

	if ((capture_len + samples) > SOUND_CAPTURE_MAX_SAMPLES)
	{
		capture_overflowed = 1;
		rpclog("headless_sound: capture buffer full, dropping further audio\n");
		return;
	}

	if ((capture_len + samples) > capture_alloc)
	{
		size_t want = capture_alloc ? capture_alloc * 2 : (SOUND_FRAMES_PER_BUFFER * SOUND_CHANNELS * 16);
		int16_t *new_buf;

		while (want < (capture_len + samples))
			want *= 2;

		new_buf = realloc(capture_buf, want * sizeof(int16_t));
		if (!new_buf)
		{
			capture_overflowed = 1;
			rpclog("headless_sound: out of memory growing capture buffer\n");
			return;
		}
		capture_buf = new_buf;
		capture_alloc = want;
	}

	memcpy(&capture_buf[capture_len], buf, samples * sizeof(int16_t));
	capture_len += samples;
}

void sound_givebuffer(int16_t *buf)
{
	capture_append(buf, SOUND_FRAMES_PER_BUFFER * SOUND_CHANNELS);
}

void sound_givebufferdd(int16_t *buf)
{
	/*Drive noise is a host-side effect layered on top of the guest's audio by
	  the SDL backend. It is not part of what the guest emitted, so it is
	  deliberately not captured.*/
}

int headless_sound_capture_start(void)
{
	free(capture_buf);
	capture_buf = NULL;
	capture_len = capture_alloc = 0;
	capture_overflowed = 0;
	capturing = 1;
	return 0;
}

void headless_sound_capture_stop(void)
{
	capturing = 0;
}

int16_t *headless_sound_fetch(size_t *samples)
{
	int16_t *out = capture_buf;

	*samples = capture_len;
	capture_buf = NULL;
	capture_len = capture_alloc = 0;
	capture_overflowed = 0;
	return out;
}
