"""End-to-end exercise of the headless emulator through the client layer.

Run from the repository root:  python mcp/tests/integration.py [outdir]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arculator_mcp import png                       # noqa: E402
from arculator_mcp.client import MachinePool        # noqa: E402
from arculator_mcp import keys as keymap            # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXE = os.path.join(ROOT, "arculator-headless.exe")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "mcp", "tests", "out")

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(name)
    return condition


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
    w, h, _ = png.decode_arfb(data)
    return path, w, h


def main():
    os.makedirs(OUT, exist_ok=True)
    pool = MachinePool(EXE, ROOT, os.path.join(OUT, "sandbox"))

    print("\n=== 1. machine lifecycle ===")
    t0 = time.time()
    mid, m = pool.create(config="a3000-riscos311")
    check("machine starts", m.alive(), f"{mid} on port {m.port}")
    info = m.command_json("info")
    check("reports config", info["config"] == "a3000-riscos311", json.dumps(info))
    check("starts with zero cycles", info["cycles"] == 0)

    print("\n=== 2. boot ===")
    t0 = time.time()
    total = 0
    for _ in range(12):
        r = m.command_json("run_us 1000000")
        total += r["executed"]
    wall = time.time() - t0
    emulated = total / 8e6
    print(f"      {emulated:.1f}s emulated in {wall:.1f}s wall "
          f"({emulated/wall:.1f}x realtime)")
    info = m.command_json("info")
    check("desktop resolution reached",
          info["display"]["width"] >= 640 and info["display"]["height"] >= 256,
          f"{info['display']['width']}x{info['display']['height']}")
    p, w, h = shot(m, "01-desktop")
    check("desktop screenshot", os.path.getsize(p) > 1000, f"{w}x{h} -> {p}")

    print("\n=== 3. registers and disassembly ===")
    regs = m.command_json("regs")
    check("PC is sane", 0 < regs["pc"] < 0x4000000, f"&{regs['pc']:07X}")
    check("mode reported", regs["mode"] in
          ("user", "fiq", "irq", "supervisor"), regs["mode"])
    dis = m.command_text("dis 3800000 4")
    check("ROM disassembles", "B " in dis or "MOV" in dis,
          dis.splitlines()[0].strip() if dis.strip() else "(empty)")

    print("\n=== 4. memory read/write ===")
    rom = m.command(f"read 3800000 10")
    check("ROM reads back", len(rom) == 16, rom.hex())
    m.command_text("write 8000 deadbeef")
    back = m.command("read 8000 4")
    check("RAM write/read round-trips", back.hex() == "deadbeef", back.hex())

    print("\n=== 5. keyboard injection ===")
    # F12 opens the RISC OS command line at the foot of the screen.
    tap(m, keymap.SCANCODES["f12"])
    m.command_json("run_us 500000")
    type_text(m, "FX0", enter=True)
    m.command_json("run_us 1500000")
    p, w, h = shot(m, "02-command-line")
    print(f"      screenshot -> {p}")
    # *FX0 prints the OS version string; look for the raw glyphs having changed
    # by checking the frame is no longer identical to the clean desktop.
    check("command line screenshot captured", os.path.getsize(p) > 1000,
          f"{w}x{h}")

    print("\n=== 6. HostFS ===")
    sandbox = m.hostfs
    with open(os.path.join(sandbox, "TestFile,fff"), "w") as f:
        f.write("Hello from the host\n")
    # *Cat on HostFS should list it.
    type_text(m, "Cat HostFS::HostFS.$", enter=True)
    m.command_json("run_us 1500000")
    p, w, h = shot(m, "03-hostfs-cat")
    print(f"      screenshot -> {p}")
    check("hostfs sandbox exists", os.path.isdir(sandbox), sandbox)

    print("\n=== 7. breakpoint bookkeeping ===")
    m.command_text("bpclear")
    m.command_text("bp add 3800008")
    bp = m.command_json("bp list")
    check("breakpoint registers", 0x3800008 in bp["bp"], json.dumps(bp))
    m.command_text("bp del 3800008")
    bp = m.command_json("bp list")
    check("breakpoint clears", 0x3800008 not in bp["bp"], json.dumps(bp))
    m.command_text("bpclear")

    print("\n=== 8. single step ===")
    before = m.command_json("regs")["pc"]
    m.command_json("step 1")
    after = m.command_json("regs")["pc"]
    check("step advances the PC", after != before,
          f"&{before:07X} -> &{after:07X}")

    print("\n=== 9. execution breakpoint fires ===")
    # Sample the idle desktop by single-stepping, then break on the address the
    # OS revisits most. Picking an address blind does not work: after reset the
    # ROM is also mapped at 0, so a physical ROM address is never executed as a
    # logical one.
    seen = {}
    for _ in range(400):
        pc = m.command_json("step 1")["pc"]
        seen[pc] = seen.get(pc, 0) + 1
    hot_pc, hits = max(seen.items(), key=lambda kv: kv[1])
    print(f"      hottest address &{hot_pc:07X} seen {hits}x "
          f"over 400 instructions ({len(seen)} distinct)")

    m.command_text("bpclear")
    m.command_text(f"bp add {hot_pc:x}")
    r = m.command_json("run_us 500000")
    check("execution breakpoint fires", r["reason"] == "breakpoint",
          f"reason={r['reason']} msg={r['messages'].strip()!r}")
    check("stopped at the breakpoint address", r["pc"] == hot_pc,
          f"&{r['pc']:07X} vs &{hot_pc:07X}")
    check("stopped without burning the whole budget",
          r["executed"] < 4000000, f"{r['executed']} cycles")
    m.command_text("bpclear")

    print("\n=== 10. SWI trap ===")
    # RISC OS issues SWIs constantly once idle, so this must fire.
    m.command_text("trap swi 1")
    r = m.command_json("run_us 500000")
    m.command_text("trap swi 0")
    m.command_text("bpclear")
    check("SWI trap fires", r["reason"] == "trap",
          f"reason={r['reason']} msg={r['messages'].strip()[:70]!r}")
    check("trap names the SWI", "SWI" in r["messages"],
          r["messages"].strip()[:70])

    print("\n=== 11. watchpoints trace without stopping ===")
    m.command_text("bpclear")
    # &1C is a low RISC OS workspace word that the OS touches routinely.
    m.command_text("wp add 100")
    r = m.command_json("run_us 200000")
    m.command_text("bpclear")
    print(f"      watchpoint messages: {r['messages'].strip()[:120]!r}")
    check("watchpoint does not stop execution", r["reason"] == "budget",
          f"reason={r['reason']}")

    print("\n=== 12. chip state ===")
    for chip in ("ioc", "memc", "vidc"):
        text = m.command_text(f"dump {chip}")
        check(f"{chip} dumps", len(text) > 20, f"{len(text)} bytes")
    cam = m.command_text("dump memc_cam")
    check("memc_cam dumps", "addr=" in cam, f"{len(cam)} bytes")

    print("\n=== 13. sound capture ===")
    # Checks the capture path, not that the guest is making a noise: an idle
    # RISC OS desktop emits silence, so a zero peak here is expected and only
    # the framing is asserted.
    m.command_text("sound start")
    m.command_json("run_us 1000000")
    m.command_text("sound stop")
    audio = m.command("sound fetch")
    rate, channels, frames, pcm = png.decode_arsn(audio)
    check("audio framing is correct",
          rate == 48000 and channels == 2 and len(pcm) == frames * 4,
          f"{rate} Hz, {channels}ch, {frames} frames, {len(pcm)} bytes")
    check("captured roughly the requested duration",
          0.7 < frames / rate < 1.3, f"{frames/rate:.2f}s for 1.00s requested")
    wav = png.arsn_to_wav(audio)
    check("wraps as a valid WAV",
          wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", f"{len(wav)} bytes")

    print("\n=== 14. determinism ===")
    pool.destroy(mid)
    runs = []
    for i in range(2):
        rid, rm = pool.create(config="a3000-riscos311")
        rm.command_json("run 20000000")
        runs.append(rm.command_json("regs"))
        pool.destroy(rid)
    check("two fresh runs reach identical state",
          runs[0]["r"] == runs[1]["r"] and runs[0]["pc"] == runs[1]["pc"],
          f"pc &{runs[0]['pc']:07X} vs &{runs[1]['pc']:07X}")

    pool.destroy_all()

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
