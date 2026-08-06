# Arculator MCP

An MCP server that drives the Arculator Acorn Archimedes emulator, so an agent
can build, run, debug and verify RISC OS software without a human in the loop.

## How it fits together

```
  MCP client (agent)
        |  MCP over stdio
  arculator_mcp (Python)          session management, PNG/WAV encoding,
        |                         keyboard layout, HostFS sandboxing
        |  line protocol over a loopback TCP socket, one per machine
  arculator-headless (C)          the emulation core with a null video/
                                  sound/input backend instead of a window
```

Every piece of machine state in Arculator is a file-scope global - `armregs[]`,
`mempoint[]`, the `vidc`/`ioc`/`memc` structs - so there is no machine context
to instantiate twice. **One emulator process is one machine**, and several
machines means several processes. `MachinePool` supervises them.

The emulator runs **in lockstep**: nothing executes between commands. To
advance the machine you must call `run_for_ms` or `run_for_cycles`. That is
what makes a session reproducible - two identical command sequences produce bit
identical machine state.

## Building

From the repository root, in an MSYS2 UCRT64 shell:

```sh
export ACLOCAL_PATH=/ucrt64/share/aclocal
autoreconf -i
./configure --enable-release-build --enable-podules --enable-headless \
    CFLAGS="-std=gnu17 -O3 -fno-strict-aliasing" \
    CXXFLAGS="-O3 -fno-strict-aliasing"
make MAKE=make -j$(nproc)
```

This produces `arculator-headless.exe` alongside the normal `arculator.exe`.
See the root `CLAUDE.md` for why each flag is needed.

```sh
pip install mcp
```

## Registering with Claude Code

```sh
claude mcp add arculator -- python -m arculator_mcp
```

or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "arculator": {
      "command": "python",
      "args": ["-m", "arculator_mcp"],
      "cwd": "C:/Users/khcon/OneDrive/Projects/arculator-sarah/mcp",
      "env": {
        "ARCULATOR_ROOT": "C:/Users/khcon/OneDrive/Projects/arculator-sarah"
      }
    }
  }
}
```

Environment:

| Variable | Meaning |
|---|---|
| `ARCULATOR_ROOT` | Directory holding `arculator-headless`, `roms/`, `configs/`, `cmos/`. Defaults to the repository containing this package. |
| `ARCULATOR_EXE` | Path to the binary, if not `<root>/arculator-headless`. |
| `ARCULATOR_WORK` | Where per-machine HostFS sandboxes are created. Defaults to a temp directory. |
| `ARCULATOR_DLL_PATH` | Directory holding SDL2.dll and the other MinGW runtime DLLs. Probed automatically at `C:\msys64\ucrt64\bin`. |

## The development loop

```
create_machine                     -> machine_id, and a HostFS sandbox path
run_for_ms(12000)                  -> boots RISC OS 3.11 to the desktop
hostfs_write_file("prog,ff8", ...) -> your build output, visible to the guest
press_key("f12")                   -> RISC OS command line
type_text("HostFS::HostFS.$.prog",
          press_enter=True)        -> run it
screenshot / read_memory /
read_registers                     -> verify
```

HostFS is the fast path: the sandbox directory *is* the guest's filesystem, so
getting a build into the emulator costs a file write, not a disc image. It
needs RISC OS 3 and the support ROM (`roms/arcrom_ext`), which is why the
default machine is RISC OS 3.11. For Arthur or RISC OS 2 you need `load_disc`
and a real image.

RISC OS filetypes use ArcEm's convention of a `,xxx` suffix on the host
filename: `,ff8` Absolute, `,ffb` BASIC, `,ffa` Module, `,fff` Text.

## Tools

**Machines** — `create_machine`, `destroy_machine`, `list_machines`,
`reset_machine`, `machine_info`

Three machine configurations ship in `configs/`, all RISC OS 3.11:

| Config | CPU | MEMC |
|---|---|---|
| `a3000-riscos311` (default) | ARM2 at 8 MHz | MEMC1 |
| `a3020-riscos311` | ARM250 at 12 MHz | MEMC1A |
| `a5000-arm3-riscos311` | ARM3 at 25 MHz | MEMC1A |

The A3020 and A5000 specify the WD1772 rather than the 82C711 their real
hardware has — see "Known issues" below. It affects floppy emulation only.

**Execution** — `run_for_cycles`, `run_for_ms`, `step_instructions`,
`run_until_break`

**State** — `read_registers`, `read_memory`, `write_memory`, `disassemble`,
`read_video_state`, `read_memc_state`, `dump_chip_state` (ioc / memc /
memc_cam / vidc, as text)

**Symbols** — `load_symbols`, `lookup_symbol`, `list_symbols`

**Profiling** — `profile_start`, `profile_read`, `profile_stop`

**Debugging** — `set_breakpoint`, `clear_breakpoint`, `set_write_breakpoint`,
`set_watchpoint`, `list_breakpoints`, `clear_all_breakpoints`, `set_trap`

**Display** — `screenshot`, `save_screenshot`

**Input** — `type_text`, `press_key`, `set_key_state`, `release_all_keys`,
`move_mouse`

**Files** — `hostfs_write_file`, `hostfs_read_file`, `hostfs_list`,
`hostfs_delete`, `load_disc`, `eject_disc`

**Audio** — `start_sound_capture`, `save_sound_capture`

## Behaviour worth knowing

**Breakpoints stop before the instruction executes**, so the state you inspect
is the state going in. Eight slots each for execution breakpoints, write
breakpoints and watchpoints - that is the emulator's own limit. Watchpoints
log every write without stopping; the hits come back in the `messages` field of
the next run result.

**Traps** (`dataabort`, `prefabort`, `addrexcep`, `undefins`, `swi`) stop on the
instruction *after* the faulting one, since the exception has already been
raised. `dataabort` is the quickest way to find a wild pointer. `swi` is very
noisy on a running desktop.

**Keyboard timing.** Keys are held for 40 ms of emulated time and spaced 40 ms
apart by default. The guest's keyboard is a serial protocol driven by its own
timer, so a key pressed and released within one slice is never seen. Raise
`hold_ms` if input is being dropped.

**A session leaves its inputs alone.** `saveconfig()` and `cmos_save()` are
disabled, so a run does not rewrite the machine `.cfg` or the CMOS image and
cannot depend on what ran before it. Pass `--persist` to the binary if you want
the normal write-back.

## Symbols and profiling

`load_symbols` reads what a vasm/vlink build already produces — prefer
`vlink -vicelabels`, but link maps (`-M`) and vasm listings (`-L`) work too.
Once loaded, a symbol name or `name+offset` is accepted anywhere an address
is, and disassembly and register dumps are annotated:

```
0008020 <main> : (e28f0f86) ADD R0, R15, #218
```

Constants are separated from addresses, because a build defines hundreds of
`.equ` values that would otherwise poison address lookup. When a link map is
loaded its section ranges decide this exactly; otherwise the value has to look
like an address, floored at `&8000`. Note that vlink tags real code labels
`local abs` exactly as it tags constants when code is linked to a fixed
origin, so the tag itself is not usable.

`profile_start` / `profile_read` accumulate an execution count **and a cycle
cost** per instruction word. Cycles are the point: the same instruction costs
very different amounts depending on whether it hits ROM, DRAM in page or out
of page, and on how much the video DMA is stealing. `profile_read` ranks by
cycles and names each hot spot from the symbol table.

`read_video_state` gives the derived mode (resolution, bpp, frame rate, pixel
clock), the full VIDC timing registers, the palette, and — useful for raster
work — `timing.current_line` and `in_display`, which say where the beam is
right now. `read_memc_state` gives page size, DMA enables and both the
programmed and live DMA pointers.

## Known issues

**82C711 machines hang at boot with an empty drive.** `82c711_fdc.c` cases 7
(Recalibrate) and 0xf (Seek) disable the FDC timer and call `disc_seek()`,
which does nothing when no image is loaded, so no completion interrupt is ever
raised and the OS spins forever — observed at `&38E56F8`, unresponsive even to
keystrokes. A real 82C711 signals seek-end from the controller without needing
media. This is why the shipped A3020 and A5000 configs specify the WD1772,
which has an independent index-pulse timeout. Loading a disc image does not
help, because the seek still never completes.

## Tests

```sh
python mcp/tests/integration.py                 # 23 checks across every subsystem
python mcp/tests/configs.py                     # every shipped machine boots to the desktop
python mcp/tests/run_code.py                    # host-assembled ARM code run in the guest
python mcp/tests/symbols_e2e.py                 # symbols against a real vasm/vlink build
python mcp/tests/profile_video.py               # VIDC/MEMC state and the profiler
python mcp/tests/run_demo.py '<path to !App>'   # a real RISC OS application
```

Screenshots land in `mcp/tests/out/`.
