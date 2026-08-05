"""Minimal PNG encoder for framebuffer payloads.

Avoids a Pillow dependency: the emulator hands over raw 32bpp pixels and all
that is needed is a truecolour PNG wrapper around them.
"""
import struct
import zlib


def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def decode_arfb(data):
    """Split an ARFB payload into (width, height, pixel bytes).

    Pixels are 0x00RRGGBB words in host (little-endian) order, so the bytes
    arrive as B, G, R, unused.
    """
    if len(data) < 16 or data[:4] != b"ARFB":
        raise ValueError("not an ARFB framebuffer payload")
    width, height, bpp = struct.unpack("<III", data[4:16])
    pixels = data[16:]
    expected = width * height * bpp
    if len(pixels) != expected:
        raise ValueError(
            f"framebuffer payload is {len(pixels)} bytes, expected {expected}")
    return width, height, pixels


def arfb_to_png(data, scale=1):
    """Encode an ARFB payload as PNG bytes.

    scale nearest-neighbour magnifies the image, which is occasionally useful
    for reading 8x8 system font text in a 640-wide mode.
    """
    width, height, pixels = decode_arfb(data)
    if scale < 1:
        scale = 1

    raw = bytearray()
    for y in range(height):
        row = pixels[y * width * 4:(y + 1) * width * 4]
        # BGRX -> RGB
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        if scale > 1:
            wide = bytearray()
            for x in range(width):
                wide.extend(rgb[x * 3:x * 3 + 3] * scale)
            rgb = wide
        for _ in range(scale):
            raw.append(0)          # filter type: none
            raw.extend(rgb)

    out_w, out_h = width * scale, height * scale
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", out_w, out_h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _chunk(b"IEND", b""))


def decode_arsn(data):
    """Split an ARSN payload into (sample_rate, channels, frames, pcm bytes)."""
    if len(data) < 16 or data[:4] != b"ARSN":
        raise ValueError("not an ARSN audio payload")
    rate, channels, frames = struct.unpack("<III", data[4:16])
    return rate, channels, frames, data[16:]


def arsn_to_wav(data):
    """Wrap an ARSN payload in a RIFF/WAVE container."""
    rate, channels, frames, pcm = decode_arsn(data)
    byte_rate = rate * channels * 2
    fmt = struct.pack("<HHIIHH", 1, channels, rate, byte_rate, channels * 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
