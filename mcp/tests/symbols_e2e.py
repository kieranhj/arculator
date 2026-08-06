"""Symbol support against a real vasm/vlink build.

Loads a demo's !App into HostFS, loads the vice labels its build produced, and
checks that names resolve to code the emulator agrees is there.

Usage:  python mcp/tests/symbols_e2e.py [repo path] [symbol file]
Defaults to the arc-check repo.
"""
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
SYMS = sys.argv[2] if len(sys.argv) > 2 else \
    os.path.join(REPO, "build", "linker.txt")
APP = sys.argv[3] if len(sys.argv) > 3 else os.path.join(REPO, "!Reise")

# A label the build is known to define, used to check name resolution. Falls
# back to whatever the table contains if absent.
PROBE = os.environ.get("SYMBOL_PROBE", "main")

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

    print("=== 1. parse the symbol file ===")
    table = SymbolTable()
    info = table.load(SYMS)
    stats = table.stats()
    check("symbol file parsed", stats["total"] > 50,
          f"{info['format']} format, {stats['total']} symbols "
          f"({stats['addresses']} addresses, {stats['constants']} constants)")
    check("constants separated from addresses", stats["constants"] > 0,
          "e.g. VIDC register values are not treated as code addresses")

    # Probe with the configured label if the build defines one, otherwise the
    # lowest address symbol - code either way.
    table._ensure_sorted()
    probe = PROBE if PROBE in table.by_name else (
        table.addresses[0][1] if table.addresses else None)
    entry = table.resolve(probe) if probe else None
    check("a label resolves to an address", entry is not None,
          f"{probe} = &{entry:X}" if entry else "no address symbols at all")
    check("name+offset arithmetic",
          table.resolve(f"{probe}+18") == entry + 0x18,
          f"{probe}+18 = &{table.resolve(f'{probe}+18'):X}")

    print("\n=== 2. run the program and check symbols point at real code ===")
    pool = MachinePool(EXE, ROOT, os.path.join(OUT, "sandbox"))
    mid, m = pool.create(config="a3000-riscos311")
    m.symbols = table

    app_name = os.path.basename(APP)
    dest = os.path.join(m.hostfs, app_name)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(APP, dest, dirs_exist_ok=True)

    for _ in range(12):
        m.command_json("run_us 1000000")
    tap(m, keymap.SCANCODES["f12"])
    m.command_json("run_us 500000")
    type_text(m, f"Run HostFS::HostFS.$.{app_name}.!Run", enter=True)
    m.command_json("run_us 6000000")

    info = m.command_json("info")
    print(f"      display {info['display']['width']}x{info['display']['height']}, "
          f"{info['frames']} frames presented")
    tag = app_name.lstrip("!").lower()
    with open(os.path.join(OUT, f"40-{tag}.png"), "wb") as f:
        f.write(png.arfb_to_png(m.command("screenshot")))

    # An Absolute file loads at &8000, the address the symbols were linked
    # for, so memory there should be the file byte for byte.
    image_path = os.path.join(APP, "!RunImage,ff8")
    if os.path.isfile(image_path):
        with open(image_path, "rb") as f:
            image = f.read()
        live = m.command("read 8000 40")
        check("program image is in memory at &8000", live == image[:0x40],
              f"first 8 bytes {live[:8].hex()} vs {image[:8].hex()}")

    by_name = m.command_text(f"dis {entry:x} 4")
    check("disassembly at the label is real code",
          "Undefined" not in by_name and by_name.strip() != "",
          by_name.splitlines()[0].strip())

    annotated = table.annotate(by_name)
    check("disassembly is annotated with the name", f"<{probe}" in annotated,
          annotated.splitlines()[0].strip())

    print("\n=== 3. breakpoint on a symbol name ===")
    # Rather than guess which label the program revisits, sample where it is
    # actually executing and pick a sampled address that has an exact name.
    # That exercises name -> address -> breakpoint end to end without
    # depending on the demo's control flow.
    seen = {}
    for _ in range(400):
        pc = m.command_json("step 1")["pc"]
        seen[pc] = seen.get(pc, 0) + 1

    named = [(pc, table.name_for(pc)) for pc in sorted(seen)]
    named = [(pc, hit[0], hit[1]) for pc, hit in named if hit]

    if not named:
        in_app = [pc for pc in seen if 0x8000 <= pc < 0x400000]
        print(f"      not executing named code right now ({len(in_app)} of "
              f"{len(seen)} sampled PCs are in application space); skipping "
              f"the live breakpoint check")
    else:
        target_pc, target_name, offset = named[0]
        # Address the breakpoint the way a user would - by name, not hex - so
        # the whole name -> address path is under test.
        expression = target_name if offset == 0 else f"{target_name}+{offset:x}"
        resolved = table.resolve(expression)
        check("symbol expression resolves to the sampled PC",
              resolved == target_pc,
              f"{expression} -> &{resolved:07X}, sampled &{target_pc:07X}")

        m.command_text("bpclear")
        m.command_text(f"bp add {resolved:x}")
        r = m.command_json("run_us 3000000")
        hit = r["reason"] == "breakpoint" and r["pc"] == target_pc
        check(f"breakpoint at {expression} fires", hit,
              f"reason={r['reason']} pc=&{r['pc']:07X} "
              f"expected &{target_pc:07X}")
        if hit:
            print("      stopped at "
                  f"{table.format_address(m.command_json('regs')['pc'])}")
        m.command_text("bpclear")

    pool.destroy_all()
    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
