"""Check every shipped machine configuration boots and reaches the desktop.

Run from the repository root:  python mcp/tests/configs.py
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arculator_mcp import png                       # noqa: E402
from arculator_mcp import keys as keymap            # noqa: E402
from arculator_mcp.client import MachinePool        # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXE = os.path.join(ROOT, "arculator-headless.exe")
OUT = os.path.join(ROOT, "mcp", "tests", "out")

# Only the configurations committed to the repository; a user's own configs/
# entries are their business and may reference ROMs that are not present.
CONFIGS = ["a3000-riscos311", "a3020-riscos311", "a5000-arm3-riscos311"]


def tap(m, sc, sh=False, hold=40, gap=40):
    if sh:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 1")
    m.command_text(f"key {sc} 1")
    m.command_json(f"run_us {hold*1000}")
    m.command_text(f"key {sc} 0")
    if sh:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 0")
    m.command_json(f"run_us {gap*1000}")


def type_text(m, text, enter=False):
    for ch in text:
        s, sh = keymap.char_to_keystroke(ch)
        tap(m, s, sh)
    if enter:
        tap(m, keymap.SCANCODES["return"])


def main():
    os.makedirs(OUT, exist_ok=True)
    pool = MachinePool(EXE, ROOT, os.path.join(OUT, "sandbox"))
    failures = []

    for config in CONFIGS:
        print(f"\n=== {config} ===")
        try:
            mid, m = pool.create(config=config)
        except Exception as e:
            print(f"  [FAIL] would not start: {e}")
            failures.append(config)
            continue

        info = m.command_json("info")
        print(f"  {info['speed_mhz']} MHz, {info['memsize_kb']} KB")

        # Prove HostFS is live on this machine, not just on the A3000.
        with open(os.path.join(m.hostfs, "Marker,fff"), "w") as f:
            f.write("present")

        t0 = time.time()
        for _ in range(14):
            m.command_json("run_us 1000000")
        wall = time.time() - t0

        info = m.command_json("info")
        d = info["display"]

        # Resolution alone is not evidence of a desktop - a machine hung during
        # boot still reports a valid mode with a blank screen. Count lit pixels.
        w, h, px = png.decode_arfb(m.command("screenshot"))
        lit = sum(1 for i in range(0, len(px), 4)
                  if px[i] or px[i + 1] or px[i + 2])
        booted = d["valid"] and d["width"] >= 640 and lit > 10000
        print(f"  boot: {d['width']}x{d['height']} in {wall:.1f}s wall "
              f"({14/wall:.1f}x realtime), {lit} lit pixels")

        tap(m, keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        type_text(m, "Cat HostFS::HostFS.$", enter=True)
        m.command_json("run_us 1500000")

        path = os.path.join(OUT, f"30-{config}.png")
        with open(path, "wb") as f:
            f.write(png.arfb_to_png(m.command("screenshot")))

        regs = m.command_json("regs")
        print(f"  PC &{regs['pc']:07X}, {regs['cycles']/1e6:.1f}M cycles")
        print(f"  screenshot -> {path}")
        print(f"  [{'PASS' if booted else 'FAIL'}] reached the desktop")
        if not booted:
            failures.append(config)

        pool.destroy(mid)

    pool.destroy_all()
    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print(f"All {len(CONFIGS)} configurations booted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
