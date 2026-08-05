"""Run a real RISC OS application from the demo repos, headlessly.

Copies a !App directory into the machine's HostFS sandbox, runs its !Run file
from the command line, and captures frames as it executes.

Usage:  python mcp/tests/run_demo.py <path to !App> [frames]
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arculator_mcp import png                       # noqa: E402
from arculator_mcp import keys as keymap            # noqa: E402
from arculator_mcp.client import MachinePool        # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXE = os.path.join(ROOT, "arculator-headless.exe")
OUT = os.path.join(ROOT, "mcp", "tests", "out")


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


def shot(m, name):
    data = m.command("screenshot")
    path = os.path.join(OUT, name + ".png")
    with open(path, "wb") as f:
        f.write(png.arfb_to_png(data))
    w, h, _ = png.decode_arfb(data)
    return path, w, h


def main():
    app_path = sys.argv[1]
    frames = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    app_name = os.path.basename(app_path.rstrip("/\\"))

    os.makedirs(OUT, exist_ok=True)
    pool = MachinePool(EXE, ROOT, os.path.join(OUT, "sandbox"))
    mid, m = pool.create(config="a3000-riscos311")
    print(f"machine {mid}; copying {app_name} into the sandbox")

    dest = os.path.join(m.hostfs, app_name)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(app_path, dest)

    print("booting...")
    for _ in range(12):
        m.command_json("run_us 1000000")
    print(f"   display now {m.command_json('info')['display']}")

    tap(m, keymap.SCANCODES["f12"])
    m.command_json("run_us 500000")
    cmd = f"Run HostFS::HostFS.$.{app_name}.!Run"
    print(f"running: *{cmd}")
    type_text(m, cmd, enter=True)

    tag = app_name.lstrip("!").lower()
    for i in range(frames):
        m.command_json("run_us 1500000")
        info = m.command_json("info")
        path, w, h = shot(m, f"20-{tag}-{i}")
        print(f"   frame {i}: {w}x{h}, {info['frames']} presented -> {path}")

    regs = m.command_json("regs")
    print(f"   PC &{regs['pc']:07X}, mode {regs['mode']}, "
          f"{regs['cycles']/8e6:.1f}s emulated")

    pool.destroy_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
