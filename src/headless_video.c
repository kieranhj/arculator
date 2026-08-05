/*Arculator headless front-end - video capture backend.

  Implements the plat_video.h contract without a window. Mirrors the geometry
  and clipping of video_sdl2.c exactly: a 2048x2048 32bpp composition buffer
  standing in for the SDL texture, blitted into by video_renderer_update() and
  read back by video_renderer_present().*/
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "arc.h"
#include "headless.h"
#include "plat_video.h"
#include "vidc.h"
#include "video.h"

int selected_video_renderer;

headless_frame_t headless_frame;

static uint32_t *composition;

/*Rect written by the last video_renderer_update(), in composition-buffer
  coordinates. Kept as file scope purely to mirror video_sdl2.c's structure.*/
static struct { int x, y, w, h; } update_rect;

static const char *renderer_names[RENDERER_COUNT] =
{
	"Auto", "Direct3D", "OpenGL", "Software"
};

int video_renderer_available(int id)
{
	/*Only the software path is meaningful without a window.*/
	return (id == RENDERER_AUTO || id == RENDERER_SOFTWARE);
}

char *video_renderer_get_name(int id)
{
	if (id < 0 || id >= RENDERER_COUNT)
		return (char *)renderer_names[RENDERER_AUTO];
	return (char *)renderer_names[id];
}

int video_renderer_get_id(char *name)
{
	int c;

	for (c = 0; c < RENDERER_COUNT; c++)
	{
		if (!strcmp(name, renderer_names[c]))
			return c;
	}
	return RENDERER_AUTO;
}

int video_renderer_init(void *main_window)
{
	if (!composition)
	{
		composition = calloc(HEADLESS_FB_W * HEADLESS_FB_H, sizeof(uint32_t));
		if (!composition)
		{
			rpclog("headless_video: out of memory allocating composition buffer\n");
			return 0;
		}
	}
	memset(&headless_frame, 0, sizeof(headless_frame));
	return 1;
}

int video_renderer_reinit(void *main_window)
{
	return video_renderer_init(main_window);
}

void video_renderer_close(void)
{
	free(composition);
	composition = NULL;
	memset(&headless_frame, 0, sizeof(headless_frame));
}

void video_renderer_update(BITMAP *src, int src_x, int src_y, int dest_x, int dest_y, int w, int h)
{
	int row;

	if (!composition || !src || !src->dat)
		return;

	update_rect.x = dest_x;
	update_rect.y = dest_y;
	update_rect.w = w;
	update_rect.h = h;

	/*Clip against the source bitmap. Identical to video_sdl2.c - the guest can
	  legitimately ask for a rect that starts off the top or left of the buffer
	  during mode changes.*/
	if (src_x < 0)
	{
		update_rect.w += src_x;
		src_x = 0;
	}
	if (src_x > (HEADLESS_FB_W - 1))
		return;
	if ((src_x + update_rect.w) > (HEADLESS_FB_W - 1))
		update_rect.w = HEADLESS_FB_W - src_x;

	if (src_y < 0)
	{
		update_rect.h += src_y;
		src_y = 0;
	}
	if (src_y > (HEADLESS_FB_H - 1))
		return;
	if ((src_y + update_rect.h) > (HEADLESS_FB_H - 1))
		update_rect.h = HEADLESS_FB_H - src_y;

	/*Clip against the destination.*/
	if (update_rect.x < 0)
	{
		update_rect.w += update_rect.x;
		update_rect.x = 0;
	}
	if (update_rect.x > (HEADLESS_FB_W - 1))
		return;
	if ((update_rect.x + update_rect.w) > (HEADLESS_FB_W - 1))
		update_rect.w = HEADLESS_FB_W - update_rect.x;

	if (update_rect.y < 0)
	{
		update_rect.h += update_rect.y;
		update_rect.y = 0;
	}
	if (update_rect.y > (HEADLESS_FB_H - 1))
		return;
	if ((update_rect.y + update_rect.h) > (HEADLESS_FB_H - 1))
		update_rect.h = HEADLESS_FB_H - update_rect.y;

	if (update_rect.w <= 0 || update_rect.h <= 0)
		return;

	/*Guard against a source bitmap narrower than the requested rect - src->w is
	  the stride, and reading past it would walk into the next scanline.*/
	if ((src_x + update_rect.w) > src->w)
	{
		update_rect.w = src->w - src_x;
		if (update_rect.w <= 0)
			return;
	}
	if ((src_y + update_rect.h) > src->h)
	{
		update_rect.h = src->h - src_y;
		if (update_rect.h <= 0)
			return;
	}

	for (row = 0; row < update_rect.h; row++)
	{
		const uint32_t *s = &((uint32_t *)src->dat)[(src_y + row) * src->w + src_x];
		uint32_t *d = &composition[(update_rect.y + row) * HEADLESS_FB_W + update_rect.x];

		memcpy(d, s, update_rect.w * sizeof(uint32_t));
	}
}

void video_renderer_present(int src_x, int src_y, int src_w, int src_h, int dblscan)
{
	if (!composition)
		return;

	if (src_x < 0) src_x = 0;
	if (src_y < 0) src_y = 0;
	if (src_w < 0) src_w = 0;
	if (src_h < 0) src_h = 0;
	if ((src_x + src_w) > HEADLESS_FB_W) src_w = HEADLESS_FB_W - src_x;
	if ((src_y + src_h) > HEADLESS_FB_H) src_h = HEADLESS_FB_H - src_y;

	headless_frame.x = src_x;
	headless_frame.y = src_y;
	headless_frame.w = src_w;
	headless_frame.h = src_h;
	headless_frame.dblscan = dblscan;
	headless_frame.frame_count++;
	headless_frame.valid = (src_w > 0 && src_h > 0);
}

uint32_t *headless_video_snapshot(int *w, int *h)
{
	int out_w, out_h, row;
	uint32_t *out;

	if (!composition || !headless_frame.valid)
		return NULL;

	out_w = headless_frame.w;
	out_h = headless_frame.dblscan ? headless_frame.h * 2 : headless_frame.h;

	if (out_w <= 0 || out_h <= 0)
		return NULL;

	out = malloc((size_t)out_w * out_h * sizeof(uint32_t));
	if (!out)
		return NULL;

	for (row = 0; row < out_h; row++)
	{
		int src_row = headless_frame.dblscan ? (row / 2) : row;
		const uint32_t *s = &composition[(headless_frame.y + src_row) * HEADLESS_FB_W + headless_frame.x];

		memcpy(&out[(size_t)row * out_w], s, out_w * sizeof(uint32_t));
	}

	*w = out_w;
	*h = out_h;
	return out;
}
