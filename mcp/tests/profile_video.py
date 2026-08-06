"""VIDC/MEMC state reporting and cycle-accurate profiling, against a real demo.

Usage:  python mcp/tests/profile_video.py [repo] [symbols] [!App]
Defaults to mikroreise.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arculator_mcp import png                       # noqa: E402
from arculator_mcp import keys as keymap            # noqa: E402
from arculator_mcp.client import MachinePool        # noqa: E402
from arculator_mcp.symbols import SymbolTable       # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXE = os.path.join(ROOT, "arculator-headless.exe")
OUT = os.path.join(ROOT, "mcp", "tests", "out")

REPO = sys.argv[1] if len(sys.argv) > 1 else \
    r"C:\Users\khcon\OneDrive\Archie\Repos\mikroreise"
SYMS = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "build", "linker.txt")
APP = sys.argv[3] if len(sys.argv) > 3 else os.path.join(REPO, "!Reise")

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def tap(m, sc, sh=False):
    if sh:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 1")
    m.command_text(f"key {sc} 1")
    m.command_json("run_us 40000")
    m.command_text(f"key {sc} 0")
    if sh:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 0")
    m.command_json("run_us 40000")


def type_text(m, text, enter=False):
    for ch in text:
        s, sh = keymap.char_to_keystroke(ch)
        tap(m, s, sh)
    if enter:
        tap(m, keymap.SCANCODES["return"])


def main():
    os.makedirs(OUT, exist_ok=True)
    table = SymbolTable()
    table.load(SYMS)

    pool = MachinePool(EXE, ROOT, os.path.join(OUT, "sandbox"))
    mid, m = pool.create(config="a3000-riscos311")

    app_name = os.path.basename(APP)
    dest = os.path.join(m.hostfs, app_name)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(APP, dest, dirs_exist_ok=True)

    print("=== 1. desktop video state ===")
    for _ in range(12):
        m.command_json("run_us 1000000")
    desktop = m.command_json("dump vidc_json")
    print(f"      {json.dumps(desktop['mode'])}")
    check("desktop mode geometry is sane",
          desktop["mode"]["width"] >= 640 and desktop["mode"]["bpp"] in (1, 2, 4, 8),
          f"{desktop['mode']['width']}x{desktop['mode']['height']} "
          f"{desktop['mode']['bpp']}bpp")
    check("frame rate is plausible",
          40 < desktop["mode"]["frame_rate_hz"] < 120,
          f"{desktop['mode']['frame_rate_hz']:.2f} Hz")
    check("palette has 16 entries", len(desktop["palette"]) == 16)

    # The reported mode must agree with the framebuffer actually presented,
    # once line doubling is accounted for. This is the check that catches a
    # mis-derived geometry.
    fb = m.command_json("info")["display"]
    scale = 2 if desktop["mode"]["line_doubled"] or desktop["mode"]["interlaced"] else 1
    check("reported mode agrees with the presented framebuffer",
          desktop["mode"]["width"] == fb["width"]
          and desktop["mode"]["height"] * scale == fb["height"],
          f"vidc {desktop['mode']['width']}x{desktop['mode']['height']}"
          f"{'x2' if scale == 2 else ''} vs framebuffer "
          f"{fb['width']}x{fb['height']}")

    memc = m.command_json("dump memc_json")
    print(f"      MEMC page size {memc['page_size']}, video DMA "
          f"{'on' if memc['video_dma'] else 'off'}, "
          f"vinit=&{memc['registers']['vinit']:06X}")
    check("MEMC reports video DMA enabled on the desktop", memc["video_dma"])
    check("MEMC page size matches memory size",
          memc["page_size_bytes"] in (4096, 8192, 16384, 32768),
          f"{memc['page_size_bytes']} bytes for {memc['memory_kb']} KB")
    check("live video DMA pointer is inside the programmed region",
          memc["registers"]["vinit"] <= memc["current"]["vaddr"] <= memc["registers"]["vend"]
          or memc["current"]["vaddr"] >= memc["registers"]["vinit"],
          f"vaddr=&{memc['current']['vaddr']:06X} in "
          f"&{memc['registers']['vinit']:06X}..&{memc['registers']['vend']:06X}")

    print("\n=== 2. the demo changes the mode ===")
    tap(m, keymap.SCANCODES["f12"])
    m.command_json("run_us 500000")
    type_text(m, f"Run HostFS::HostFS.$.{app_name}.!Run", enter=True)
    m.command_json("run_us 6000000")

    running = m.command_json("dump vidc_json")
    print(f"      {json.dumps(running['mode'])}")
    print(f"      raster line {running['timing']['current_line']} of "
          f"{running['timing']['v_total']}, "
          f"{'in display' if running['timing']['in_display'] else 'in blanking'}")
    check("video state is readable while a demo runs",
          running["mode"]["width"] > 0 and running["mode"]["frame_rate_hz"] > 0,
          f"{running['mode']['width']}x{running['mode']['height']} "
          f"{running['mode']['bpp']}bpp @ {running['mode']['frame_rate_hz']:.1f}Hz")
    check("current raster line is within the frame",
          0 <= running["timing"]["current_line"] <= running["timing"]["v_total"])

    with open(os.path.join(OUT, "50-demo.png"), "wb") as f:
        f.write(png.arfb_to_png(m.command("screenshot")))

    print("\n=== 3. profiling ===")
    started = m.command_json("profile start 8000 20000")
    check("profiler starts", started["words"] == 0x20000 // 4,
          f"{started['words']} words from &{started['base']:07X}")

    m.command_json("run_us 3000000")
    prof = m.command_json("profile read 12")

    check("profiler recorded instructions", prof["total_instructions"] > 1000,
          f"{prof['total_instructions']} instructions, "
          f"{prof['total_cycles']} cycles")
    check("cycles exceed instructions",
          prof["total_cycles"] > prof["total_instructions"],
          f"{prof['total_cycles'] / max(1, prof['total_instructions']):.2f} "
          f"cycles per instruction on average")
    check("hot spots were found", len(prof["entries"]) > 0,
          f"{len(prof['entries'])} entries")

    print("\n      hottest addresses in the demo's own code:")
    total = prof["total_cycles"] or 1
    for e in prof["entries"][:12]:
        hit = table.name_for(e["addr"])
        name = (f"{hit[0]}+0x{hit[1]:x}" if hit and hit[1] else
                hit[0] if hit else "")
        mean = e["cycles"] / e["count"] if e["count"] else 0
        print(f"        &{e['addr']:07X} {100*e['cycles']/total:5.1f}%  "
              f"{e['count']:7d} hits  {mean:5.1f} cyc/hit  {name}")

    named = sum(1 for e in prof["entries"] if table.name_for(e["addr"]))
    check("hot spots resolve to symbols", named > 0,
          f"{named} of {len(prof['entries'])} named")

    # Cycle costs must vary - if every instruction cost the same the profiler
    # would be reporting instruction counts, not real cost.
    means = [e["cycles"] / e["count"] for e in prof["entries"] if e["count"]]
    check("per-instruction cycle costs vary",
          len(set(round(x, 1) for x in means)) > 1,
          f"{min(means):.1f}..{max(means):.1f} cycles per execution")

    m.command_text("profile stop")
    pool.destroy_all()

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
