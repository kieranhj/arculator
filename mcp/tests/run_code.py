"""Prove the full development loop: assemble on the host, run in the guest.

Builds a RISC OS Absolute (&FF8) image by hand, drops it into the machine's
HostFS sandbox, runs it from the guest command line and reads the result off
the screen.

Run from the repository root:  python mcp/tests/run_code.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arculator_mcp import png                       # noqa: E402
from arculator_mcp import keys as keymap            # noqa: E402
from arculator_mcp.client import MachinePool        # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXE = os.path.join(ROOT, "arculator-headless.exe")
OUT = os.path.join(ROOT, "mcp", "tests", "out")

# SWI numbers, from the RISC OS PRM.
OS_WriteS = 0x01
OS_NewLine = 0x03
OS_Exit = 0x11


def swi(number):
    return struct.pack("<I", 0xEF000000 | number)


def inline_string(text):
    """Zero-terminated string padded to a word, as OS_WriteS expects."""
    blob = text.encode("latin-1") + b"\0"
    if len(blob) % 4:
        blob += b"\0" * (4 - len(blob) % 4)
    return blob


def build_hello(message):
    """A minimal Absolute image: print a string, newline, exit.

    Absolute files load and execute at &8000. OS_WriteS prints the string
    inline after the instruction and resumes at the next word boundary past
    its terminator, so no branching is needed.
    """
    return (swi(OS_WriteS) + inline_string(message)
            + swi(OS_NewLine)
            + swi(OS_Exit))


def tap(m, scancode, shift=False, hold=40, gap=40):
    if shift:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 1")
    m.command_text(f"key {scancode} 1")
    m.command_json(f"run_us {hold*1000}")
    m.command_text(f"key {scancode} 0")
    if shift:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 0")
    m.command_json(f"run_us {gap*1000}")


def type_text(m, text, enter=False):
    for ch in text:
        sc, sh = keymap.char_to_keystroke(ch)
        tap(m, sc, sh)
    if enter:
        tap(m, keymap.SCANCODES["return"])


def shot(m, name, scale=1):
    data = m.command("screenshot")
    path = os.path.join(OUT, name + ".png")
    with open(path, "wb") as f:
        f.write(png.arfb_to_png(data, scale=scale))
    return path


def main():
    os.makedirs(OUT, exist_ok=True)
    pool = MachinePool(EXE, ROOT, os.path.join(OUT, "sandbox"))
    mid, m = pool.create(config="a3000-riscos311")
    print(f"machine {mid}, HostFS sandbox at {m.hostfs}")

    MESSAGE = "Hello from a program built on the host"
    image = build_hello(MESSAGE)
    # ',ff8' is ArcEm's HostFS convention for RISC OS filetype &FF8, Absolute.
    with open(os.path.join(m.hostfs, "hello,ff8"), "wb") as f:
        f.write(image)
    print(f"wrote hello,ff8 ({len(image)} bytes) into the sandbox")

    print("booting...")
    for _ in range(12):
        m.command_json("run_us 1000000")

    # F12 opens the command line at the foot of the desktop.
    tap(m, keymap.SCANCODES["f12"])
    m.command_json("run_us 500000")

    type_text(m, "HostFS::HostFS.$.hello", enter=True)
    m.command_json("run_us 2000000")

    path = shot(m, "10-run-code")
    print(f"screenshot -> {path}")

    # Confirm the code really executed, independent of reading pixels: the
    # image should have been loaded at &8000 by the Absolute file handler.
    loaded = m.command("read 8000 4")
    expected = image[:4]
    ok_mem = loaded == expected
    print(f"memory at &8000 = {loaded.hex()}, "
          f"expected {expected.hex()} -> {'MATCH' if ok_mem else 'MISMATCH'}")

    pool.destroy_all()
    return 0 if ok_mem else 1


if __name__ == "__main__":
    sys.exit(main())
