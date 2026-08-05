# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Arculator 2.2 — an Acorn Archimedes / A-series emulator by Sarah Walker. C (emulation core) + C++ (wxWidgets UI). No test suite; verification is by building and running.

## Build

Autotools on Unix/macOS/MinGW:

```sh
./configure --enable-release-build    # recommended for regular use
make                                  # binary is copied to ./arculator (or ./arculator.exe)
```

### Windows (MSYS2 UCRT64) — known-good recipe

This is the verified path on this machine. Run everything from the **UCRT64** environment
(`C:\msys64\ucrt64.exe`), not Git Bash, and not the MSYS shell:

```sh
pacman -S --needed autoconf automake libtool make pkgconf \
    mingw-w64-ucrt-x86_64-{gcc,SDL2,wxwidgets3.2-msw,zlib,libpcap}

export ACLOCAL_PATH=/ucrt64/share/aclocal   # sdl2.m4 / wxwin.m4 live here
autoreconf -fi                              # REQUIRED, see below
./configure --enable-release-build --enable-podules \
    CFLAGS="-std=gnu17 -O3 -fno-strict-aliasing" \
    CXXFLAGS="-O3 -fno-strict-aliasing"
make MAKE=make -j$(nproc)
```

Four non-obvious things, each of which will bite again on a fresh clone:

- **`autoreconf -fi` is mandatory.** The checked-in `missing`, `compile`, `depcomp`,
  `install-sh`, `config.guess`, `config.sub` and `INSTALL` are Unix symlinks into
  `/usr/share/automake-1.15/`, committed as *plain text files* by a Windows checkout without
  symlink support. They are not valid scripts. Delete and regenerate them.
- **`--enable-podules` must be passed explicitly.** `configure.ac` tests `!= "no"` when
  deciding to print "building podules: yes" and set `SO_EXT`, but the `AM_CONDITIONAL` at
  `configure.ac:145` tests `= "yes"`. With no flag the report says yes while every podule is
  silently skipped — `make` succeeds and simply produces no DLLs.
- **`MAKE=make` overrides `SET_MAKE`.** `configure.ac:105` substitutes `MAKE=mingw32-make` for
  MinGW hosts, so the recursive make dies with "mingw32-make: command not found" unless you
  override it or install `mingw-w64-ucrt-x86_64-make`.
- **`-std=gnu17` is load-bearing.** GCC 15 defaults to C23, where `f()` declares a prototype
  taking no arguments rather than an unspecified list. This tree uses K&R-style empty-paren
  declarations throughout (27 in `arc.h` alone), and `ide_hccs_a3k.c:59` forward-declares
  functions that are then defined taking an `ide_t *`. The code is valid C17; do not "fix" the
  declarations one by one.

The `-O3` is deliberate: `configure.ac:47` overwrites `CFLAGS_BUILD` when podules are enabled,
discarding the `-O3` set for release builds at line 25, so a plain `--enable-release-build`
silently falls back to autoconf's default `-O2`. Passing `CFLAGS` restores it (user `CFLAGS`
are appended last and win over the per-target `-O0 -g3` in `src/Makefile.am`).

To run the result outside MSYS2, `C:\msys64\ucrt64\bin` must be on `PATH` — all 53 DLL
dependencies (wx, SDL2, zlib, libstdc++, libpcap) resolve from there.

`configure` options:
- `--enable-release-build` — defines `RELEASE_BUILD`, which compiles out internal consistency `fatal()` checks (see `timer.c`, `arm.c`) and the per-instruction trace block.
- `--enable-debug` — `-O0 -g -D_DEBUG -DDEBUG_LOG`.
- `--disable-podules` — skip the external podule shared libraries.
- `--enable-wx-main-window` — defines `UI_WX`; see "UI variants" below.
- `--enable-headless` — additionally build `arculator-headless`, a windowless binary driven over a control socket; see "Headless build" below.

Requires SDL2 ≥ 2.0.1, wxWidgets ≥ 3.0, zlib, X11 (non-Windows), ALSA (Linux, for podules).

`mac-setup` bootstraps the macOS toolchain via Homebrew. Windows/MinGW also has hand-written non-autotools makefiles: `src/Makefile.mingw` and `podules/*/src/Makefile.mingw` (note `src/Makefile.mingw` hardcodes `WXINCLUDE`).

After editing `Makefile.am` or `configure.ac`, regenerate with `autoreconf -i` (or `automake`) before `./configure`.

Each podule under `podules/<name>/src/` builds as a standalone shared object, copied to `podules/<name>/<name>.so|.dll` — the layout `opendlls()` scans at runtime. Adding a podule means adding its `src/Makefile` to both `AC_CONFIG_FILES` in `configure.ac` and `SUBDIRS` in the top-level `Makefile.am`.

## UI variants

Three mutually exclusive main-window builds, selected in `src/Makefile.am`:

| Build | Sources | Notes |
|---|---|---|
| Windows (`OS_WINDOWS`) | `input_sdl2.c video_sdl2.c wx-win32.c` | `UI_WX` is ignored on Windows |
| Unix, default | `input_sdl2.c video_sdl2.c wx-sdl2.c` | Emulated screen is its own SDL window; wx used only for dialogs; menu is a right-click popup |
| Unix, `--enable-wx-main-window` | `input_wx.c video_wx.c wx-mainwindow.cc` | Screen is a wx canvas with a real `wxMenuBar` |

`wx-sdl2.c` and `wx-mainwindow.cc` define the *same* `arc_*` symbols (`arc_start_main_thread`, `arc_disc_change`, `arc_set_display_mode`, …, declared in `arc.h`). That `arc_*` set is the emulator↔UI boundary; changes to one implementation almost always need mirroring in the other — and in `headless_main.c`, below.

## Headless build

`--enable-headless` builds a second binary, `arculator-headless`, from the same emulation core with `headless_video.c`/`headless_input.c`/`headless_sound.c` in place of the SDL platform backends, and no wxWidgets at all. `headless_main.c` is the third `arc_*` implementation. It exists to be driven by the MCP server in `mcp/` — see `mcp/README.md`.

Two things differ from the interactive front-ends and are deliberate:

- **Single threaded, in lockstep with the controller.** There is no emulation thread and no `main_thread_mutex`; a control command is serviced, the machine is advanced by an explicit cycle count, and the result is reported. Nothing runs between commands. This is what makes a session reproducible, and it is why the `indebug` mutex dance does not appear in `headless_main.c`.
- **No persistence.** `config_persist_enabled` is cleared, so `saveconfig()` and `cmos_save()` do nothing. Otherwise a session would rewrite the `.cfg` and CMOS images it was handed, and runs would depend on what ran before them. `--persist` restores the normal behaviour.

The hooks it needed in shared code are small, and worth knowing about because they change debugger behaviour for *any* non-interactive caller:

- `arm_break_execution()` (`arm.c`) zeroes the `execarm()` cycle budget **and** makes the loop break at the debugger hook, rewinding the 3-stage pipeline by one so the un-executed instruction is re-issued on resume. This is what makes a breakpoint stop *before* its instruction executes. The interactive REPL never needed it because it blocks inside `debugger_do()`.
- `debugger_stop_handler` (`debugger.c`), when installed, replaces the interactive REPL. `debug_resume_pc` stops a resume from immediately re-triggering the breakpoint it just stopped on.
- `debugger_capture_begin/end()` redirects `debug_out()` into a buffer, which is how the headless build reuses the debugger's disassembler and register/IOC/MEMC/VIDC formatting rather than duplicating it. Anything reachable from a `debug_out()` call is available this way.
- `hostfs_root_override` (`hostfs.c`) replaces the fixed `<exe>/hostfs` so each session gets its own sandbox.

## Architecture

### Threading

`main()` is in `wx-main.cc`; it builds the podule list, then hands off to wxWidgets. The **GUI thread** owns menus and dialogs (`wx-app.cc`). A separate **emulation thread** (`arc_main_thread()` in `wx-sdl2.c` / `wx-mainwindow.cc`) runs `arc_run()` in 10 ms slices, padding each slice to realtime with an accumulating `timer_offset`.

`main_thread_mutex` is the single lock between the two. Every GUI-initiated state change (reset, disc change/eject, display mode) must take it. The `indebug` flag makes those acquisitions conditional — the debugger runs *on* the emulation thread inside `execarm`, already holding it.

### CPU and timing

`arc_run()` (`main.c`) calls `execarm(cycles_for_10ms)`. `execarm()` (`arm.c`) runs a 3-stage-pipeline fetch/decode/execute loop dispatching through `opcode_fns[(opcode >> 20) & 0xff]`, a 256-entry function-pointer table.

**Everything time-related is 32:32 fixed point**, where `1ull << 32` is one CPU cycle: the global `tsc`, `mem_speed[][]`, `TIMER_USEC`, `mem_spd_multi`. Three clock domains are modelled explicitly (documented in a comment block at the top of `arm.c`): FCLK (CPU), MCLK (MEMC, 8/12 MHz), IOCLK (fixed 8 MHz), with `sync_to_mclk()` / `sync_to_fclk()` charging the crossing cost and `CLOCK_N/S/I` charging per-access cycles from `mem_speed[]`. `run_dma()` interleaves MEMC video/cursor/sound DMA against the CPU.

`timer.c` is a sorted linked list of `emu_timer_t`, polled by `execarm` against `timer_target` — there is no scheduler thread. **A timer is disabled before its callback runs**; recurring timers must re-arm themselves with `timer_advance_u64()` (see `ioc_timer_callback`). Periods are expressed in µs via `TIMER_USEC`, which scales with `speed_mhz`.

### Memory

Two parallel 16384-entry tables covering the 26-bit space in 4 KB pages (`mem.c`, declared in `arc.h`):
- `mempoint[page]` — **pre-biased** host pointer (`&ram[...] - (page << 12)`), so the access macro indexes with the full address.
- `memstat[page]` — access-permission class, checked against `modepritabler`/`modepritablew[memmode][class]`.

`readmemb`/`readmeml` etc. hit the fast path when the priority table entry is non-zero; otherwise they fall through to `readmemfb`/`writememfl`/… in `mem.c`, which either raise `databort` or dispatch I/O via a `switch (a >> 20)`. `writememb`/`writememl` are inline functions rather than macros so they can hook the debugger.

`memc.c` implements the MEMC registers and the MMU: `writecam()` rewrites `memstat[]` and `mempoint[]` for the current page size; `writememc()` decodes VINIT/VSTART/VEND/CINIT/SSTART/SEND/SPTR/CTRL.

### Chip modules

IOC (`ioc.c`) is the interrupt hub — peripherals call `ioc_irqa/irqb/fiq` (+ `*c` to clear) and `ioc_updateirqs()` sets the global `irq` that `execarm` samples. IRQ bit assignments differ between "Old" (A300/A400) and "New" (A3000+) machines; see `ioc.h`.

`vidc.c` is the largest module (video + sound + cursor), driven by one timer whose callback renders a scanline and calls `video_renderer_update()`/`_present()` at frame end. `vidc_attach()` lets podule video cards (ColourCard, G16, G332) take over the display.

Recurring idiom for peripherals: a **per-instance struct carrying its own `emu_timer_t` plus `irq_raise`/`irq_clear` function pointers** rather than globals — this is why one `ide.c` / `st506.c` serves the internal controller and every podule variant (`ide_a3in.c`, `ide_idea.c`, `ide_zidefs.c`, `st506_akd52.c`, …).

### Machine configuration

There is no single `models[]` table. Machines are composed from orthogonal axes, mostly in `config.c`/`config.h` with the tables in `main.c`:
- `arc_cpus[]` and `arc_memcs[]` (`main.c`), applied together by `arc_set_cpu()`.
- ROM set (`ROM_*` enum), FDC type, monitor type, and `MACHINE_TYPE_NORMAL`/`_A4`.
- The `machine` string (default `"a3000"`) is largely a label; ROM set + CMOS + podules are what actually differentiate machines. `configs/` holds shipped presets, `roms/` the ROM images.

`config.c` is a two-tier INI store: `CFG_GLOBAL` (`arc.cfg` beside the executable) and `CFG_MACHINE` (per-machine file), accessed via `config_get_int/string/float(is_global, ...)`. Subsystems register load/save hooks with `add_config_callback()` rather than editing `loadconfig()`/`saveconfig()` directly. `win-config.c` is a legacy Win32-native dialog largely superseded by `wx-config*.cc`.

### Disc images

Two cooperating vtables in `disc.h`:
- `disc_funcs_t` — the image-format driver (`seek`, `readsector`, `format`, `poll`, …), one per drive in `drive_funcs[4]`.
- `fdc_funcs_t` — the upward callback interface into whichever controller is active (`wd1770.c` or `82c711_fdc.c` installs the global `fdc_funcs`).

`disc_load()` selects a loader from the `loaders[]` table in `disc.c` by **extension *and* file size** (e.g. `"ADF"` appears three times for 640K/800K/1.6M variants), falling back to size-only guessing. All disc activity is polled from one `disc_poll` timer whose interval depends on density, or ÷16 when a driver sets `high_res_poll`.

### Podules

`src/podule_api.h` is the **only** header a podule includes and is deliberately self-contained. ABI is `PODULE_API_VERSION` = `MAKE_PODULE_API_VERSION(1, 1)`; bump it if you change the structs. Entry point is `podule_probe(callbacks, path)` returning a `podule_header_t` (NULL = unavailable, e.g. missing ROM). `run(podule, timeslice_us)` returns the next delay in µs, giving podules a free-running timer. `podule_callbacks_t` is the emulator services table (IRQ/FIQ, timers, config, file selector).

`podules.c` registers `internal_podules[]` (AKD52, ColourCard, G16, IDEA, RISC Dev HDFC, ZIDEFS, and the 8-bit minipodules) by calling their `*_probe()` directly, then `opendlls()` loads external ones from `<exe>/podules/*/`. Four slots only; selection is by `short_name` stored as `podule_0`..`podule_3` in config. `PODULE_FLAGS_NEXT` lets one library export an array of headers.

### HostFS

Host filesystem passthrough inherited from ArcEm. `arm.c`'s SWI handler intercepts `ARCEM_SWI_HOSTFS` and services it entirely on the host — no SWI exception is taken. `hostfs.h` fakes ArcEm's `ARMul_State`/`ARMul_LoadWordS` API so `hostfs.c` needed almost no modification. The guest-side RISC OS modules live in `src/hostfs/` as ARM assembler and are delivered via the support ROM (`romload.c`, `roms/arcrom_ext`), which is RISC OS 3 only.

## Conventions

- Debug logging uses the `LOG_*` macro family in `arc.h`; each is compiled out unless its matching `DEBUG_*` is defined (`DEBUG_EVENT_LOOP`, `DEBUG_MEMC_VIDEO`, `DEBUG_VIDC_TIMING`, `DEBUG_DATABORT`, …). Add new categories there rather than using bare `rpclog`/`printf`.
- Platform-specific code goes behind the thin `plat_video.h` / `plat_sound.h` / `plat_input.h` / `plat_joystick.h` contracts, which are resolved by link-time source selection in `src/Makefile.am`, not vtables.
- wx dialogs are laid out in `src/arculator.xrc`, compiled to `wx-resources.cc` by `wxrc` during the build — edit the XRC, not the generated file.
- **Never pass a `wxString` straight to a C string function.** MSYS2's wxWidgets is built with
  `wxUSE_STD_STRING_CONV_IN_WXSTRING=1`, which disables `wxString::operator const char*()`, so
  `atoi(s)` / `strncpy(dst, s, n)` fail to compile even though upstream's wx build accepts them.
  Use `s.mb_str()`, which is already the prevailing idiom in `wx-config.cc`.
