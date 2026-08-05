"""MCP server exposing Arculator for autonomous Archimedes development.

Run with:  python -m arculator_mcp

Environment:
  ARCULATOR_ROOT   Directory holding arculator-headless, roms/, configs/ and
                   cmos/. Defaults to the repository this package sits in.
  ARCULATOR_EXE    Path to arculator-headless, if not <root>/arculator-headless.
  ARCULATOR_WORK   Scratch directory for per-machine HostFS sandboxes.
"""
import atexit
import base64
import binascii
import os
import sys
import tempfile

from mcp.server.mcpserver import MCPServer, Image

from . import keys as keymap
from . import png
from .client import EmulatorError, MachinePool

DEFAULT_CONFIG = "a3000-riscos311"

# Held long enough for keyboard.c's serial protocol to latch the key, and
# spaced far enough apart that RISC OS does not treat consecutive presses as
# auto-repeat. Tuned empirically against RISC OS 3.11.
KEY_HOLD_MS = 40
KEY_GAP_MS = 40


def _find_root():
    if os.environ.get("ARCULATOR_ROOT"):
        return os.path.abspath(os.environ["ARCULATOR_ROOT"])
    # <repo>/mcp/arculator_mcp/server.py -> <repo>
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


ROOT = _find_root()
EXE = os.environ.get("ARCULATOR_EXE") or os.path.join(
    ROOT, "arculator-headless.exe" if os.name == "nt" else "arculator-headless")
WORK = os.environ.get("ARCULATOR_WORK") or os.path.join(
    tempfile.gettempdir(), "arculator-mcp")

pool = MachinePool(EXE, ROOT, WORK)
atexit.register(pool.destroy_all)

mcp = MCPServer(
    name="arculator",
    instructions=(
        "Drives the Arculator Acorn Archimedes emulator for building and "
        "testing RISC OS software.\n\n"
        "Typical loop: create_machine -> run_for_ms to boot -> put files into "
        "the machine's HostFS sandbox with hostfs_write_file -> drive the "
        "guest with type_text -> verify with screenshot, read_memory or "
        "read_registers.\n\n"
        "Machines run in lockstep: nothing executes between commands, so you "
        "must call run_for_ms/run_for_cycles to advance emulated time. Cycle "
        "counts are exact and reproducible."
    ),
)


# ---------------------------------------------------------------------------
# Machine lifecycle
# ---------------------------------------------------------------------------

@mcp.tool()
def create_machine(config: str = DEFAULT_CONFIG,
                   machine_id: str | None = None) -> dict:
    """Start a new emulated Archimedes and return its id.

    Each machine is a separate emulator process with its own HostFS sandbox
    directory. The machine starts at the reset vector with nothing executed -
    call run_for_ms to boot it (RISC OS 3.11 reaches the desktop in roughly
    12000 ms of emulated time).

    config names a file in configs/ without the .cfg suffix. The default is an
    A3000 with 4MB and RISC OS 3.11, which is the only tier where HostFS works.
    """
    machine_id, machine = pool.create(config=config, machine_id=machine_id)
    info = machine.command_json("info")
    return {
        "machine_id": machine_id,
        "config": config,
        "hostfs_path": machine.hostfs,
        "machine": info,
    }


@mcp.tool()
def destroy_machine(machine_id: str, delete_files: bool = False) -> str:
    """Stop a machine. delete_files also removes its HostFS sandbox."""
    pool.destroy(machine_id, wipe_sandbox=delete_files)
    return f"destroyed {machine_id}"


@mcp.tool()
def list_machines() -> list:
    """List running machines."""
    return pool.list()


@mcp.tool()
def reset_machine(machine_id: str) -> str:
    """Hard-reset a machine, as the reset button would."""
    return pool.get(machine_id).command_text("reset")


@mcp.tool()
def machine_info(machine_id: str) -> dict:
    """Report configuration, elapsed cycles and current display geometry."""
    return pool.get(machine_id).command_json("info")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _run_result(result):
    """Annotate a raw run result with a human-readable note."""
    if result["reason"] == "budget":
        result["note"] = "ran the full requested budget without stopping"
    else:
        result["note"] = (
            f"stopped early ({result['reason']}) at PC &{result['pc']:07X}")
    result["pc_hex"] = f"&{result['pc']:07X}"
    return result


@mcp.tool()
def run_for_cycles(machine_id: str, cycles: int) -> dict:
    """Advance the machine by up to `cycles` CPU cycles.

    Stops early if a breakpoint or an enabled trap fires. Returns the stop
    reason, cycles actually executed, the PC, and any debugger output such as
    watchpoint hits.
    """
    return _run_result(pool.get(machine_id).command_json(f"run {cycles}"))


@mcp.tool()
def run_for_ms(machine_id: str, ms: int) -> dict:
    """Advance the machine by `ms` milliseconds of emulated time.

    This is emulated time, not wall-clock - the emulator runs unthrottled, so
    typically several times faster than real hardware.
    """
    return _run_result(
        pool.get(machine_id).command_json(f"run_us {int(ms) * 1000}"))


@mcp.tool()
def step_instructions(machine_id: str, count: int = 1) -> dict:
    """Single-step `count` instructions and report where execution landed."""
    return _run_result(pool.get(machine_id).command_json(f"step {count}"))


@mcp.tool()
def run_until_break(machine_id: str, max_ms: int = 5000,
                    chunk_ms: int = 250) -> dict:
    """Run until a breakpoint or trap fires, or `max_ms` of emulated time.

    Use after set_breakpoint when you do not know how long the guest will take
    to reach it.
    """
    machine = pool.get(machine_id)
    elapsed = 0
    messages = []
    while elapsed < max_ms:
        step = min(chunk_ms, max_ms - elapsed)
        result = machine.command_json(f"run_us {step * 1000}")
        elapsed += step
        if result.get("messages"):
            messages.append(result["messages"])
        if result["reason"] != "budget":
            result["messages"] = "".join(messages)
            result["emulated_ms"] = elapsed
            return _run_result(result)
    result["messages"] = "".join(messages)
    result["emulated_ms"] = elapsed
    result["note"] = f"no breakpoint hit within {max_ms} ms of emulated time"
    result["pc_hex"] = f"&{result['pc']:07X}"
    return result


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------

@mcp.tool()
def read_registers(machine_id: str) -> dict:
    """Read ARM registers, PSR flags, processor mode and the cycle count."""
    regs = pool.get(machine_id).command_json("regs")
    regs["r_hex"] = [f"&{v:08X}" for v in regs["r"]]
    regs["pc_hex"] = f"&{regs['pc']:07X}"
    return regs


@mcp.tool()
def read_memory(machine_id: str, address: str, length: int,
                as_text: bool = False) -> dict:
    """Read guest memory. `address` is hex, with or without a & or 0x prefix.

    Returns hex by default; as_text also decodes the bytes as Latin-1 with
    non-printables shown as '.', which is handy for finding strings.
    """
    data = pool.get(machine_id).command(f"read {_hex(address)} {length:x}")
    out = {"address": address, "length": len(data), "hex": data.hex()}
    if as_text:
        out["text"] = "".join(
            chr(b) if 32 <= b < 127 else "." for b in data)
    return out


@mcp.tool()
def write_memory(machine_id: str, address: str, data_hex: str) -> str:
    """Write bytes to guest memory. data_hex is a plain hex string."""
    cleaned = data_hex.replace(" ", "").replace("\n", "")
    written = pool.get(machine_id).command_text(
        f"write {_hex(address)} {cleaned}")
    return f"wrote {written} bytes at {address}"


@mcp.tool()
def disassemble(machine_id: str, address: str, count: int = 16) -> str:
    """Disassemble ARM instructions, using the emulator's own disassembler."""
    return pool.get(machine_id).command_text(
        f"dis {_hex(address)} {count}")


@mcp.tool()
def dump_chip_state(machine_id: str, chip: str) -> str:
    """Dump peripheral state. chip is ioc, memc, memc_cam or vidc.

    memc_cam lists the MMU's logical-to-physical page mappings.
    """
    return pool.get(machine_id).command_text(f"dump {chip}")


# ---------------------------------------------------------------------------
# Debugging
# ---------------------------------------------------------------------------

@mcp.tool()
def set_breakpoint(machine_id: str, address: str) -> str:
    """Break when the PC reaches `address`. Eight slots are available."""
    slot = pool.get(machine_id).command_text(f"bp add {_hex(address)}")
    return f"breakpoint at {address} in slot {slot}"


@mcp.tool()
def clear_breakpoint(machine_id: str, address: str) -> str:
    """Remove an execution breakpoint."""
    found = pool.get(machine_id).command_text(f"bp del {_hex(address)}")
    return "cleared" if found == "1" else f"no breakpoint at {address}"


@mcp.tool()
def set_write_breakpoint(machine_id: str, address: str) -> str:
    """Break when the guest writes to `address`."""
    slot = pool.get(machine_id).command_text(f"wbp add {_hex(address)}")
    return f"write breakpoint at {address} in slot {slot}"


@mcp.tool()
def set_watchpoint(machine_id: str, address: str) -> str:
    """Log, without stopping, every write to `address`.

    Hits appear in the `messages` field of the next run result.
    """
    slot = pool.get(machine_id).command_text(f"wp add {_hex(address)}")
    return f"watchpoint at {address} in slot {slot}"


@mcp.tool()
def list_breakpoints(machine_id: str) -> dict:
    """List execution breakpoints, write breakpoints and watchpoints."""
    machine = pool.get(machine_id)
    out = {}
    for cmd, label in (("bp", "breakpoints"),
                       ("wbp", "write_breakpoints"),
                       ("wp", "watchpoints")):
        raw = machine.command_json(f"{cmd} list")
        out[label] = [f"&{a:07X}" for a in raw[cmd]]
    return out


@mcp.tool()
def clear_all_breakpoints(machine_id: str) -> str:
    """Remove every breakpoint, watchpoint and trap."""
    pool.get(machine_id).command_text("bpclear")
    return "cleared"


@mcp.tool()
def set_trap(machine_id: str, trap: str, enabled: bool = True) -> str:
    """Stop on a CPU exception.

    trap is one of prefabort, dataabort, addrexcep, undefins or swi. Enabling
    dataabort is the fastest way to find a wild pointer in guest code; swi
    traps on every SWI and is very noisy.
    """
    return pool.get(machine_id).command_text(
        f"trap {trap} {1 if enabled else 0}")


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

@mcp.tool()
def screenshot(machine_id: str, scale: int = 1) -> Image:
    """Capture what the guest is displaying, as a PNG.

    scale magnifies by nearest neighbour, which helps when reading the 8x8
    system font. Fails if the guest has not yet presented a frame.
    """
    data = pool.get(machine_id).command("screenshot")
    return Image(data=png.arfb_to_png(data, scale=scale), format="png")


@mcp.tool()
def save_screenshot(machine_id: str, path: str, scale: int = 1) -> str:
    """Write a screenshot to a file on the host."""
    data = pool.get(machine_id).command("screenshot")
    blob = png.arfb_to_png(data, scale=scale)
    with open(path, "wb") as f:
        f.write(blob)
    return f"wrote {len(blob)} bytes to {path}"


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def _tap(machine, scancode, shift=False, hold_ms=KEY_HOLD_MS,
         gap_ms=KEY_GAP_MS):
    if shift:
        machine.command_text(f"key {keymap.SCANCODES['lshift']} 1")
    machine.command_text(f"key {scancode} 1")
    machine.command_json(f"run_us {hold_ms * 1000}")
    machine.command_text(f"key {scancode} 0")
    if shift:
        machine.command_text(f"key {keymap.SCANCODES['lshift']} 0")
    machine.command_json(f"run_us {gap_ms * 1000}")


@mcp.tool()
def type_text(machine_id: str, text: str, press_enter: bool = False,
              hold_ms: int = KEY_HOLD_MS, gap_ms: int = KEY_GAP_MS) -> dict:
    """Type text into the guest, one key at a time.

    Advances emulated time as it goes - roughly (hold_ms + gap_ms) per
    character - so no separate run call is needed. Uses a UK keyboard layout,
    matching real Archimedes hardware.

    press_enter appends Return, which is usually what you want for a command.
    """
    machine = pool.get(machine_id)
    for ch in text:
        scancode, shift = keymap.char_to_keystroke(ch)
        _tap(machine, scancode, shift, hold_ms, gap_ms)
    if press_enter:
        _tap(machine, keymap.SCANCODES["return"], False, hold_ms, gap_ms)
    total = (len(text) + (1 if press_enter else 0)) * (hold_ms + gap_ms)
    return {"typed": len(text), "emulated_ms": total,
            "machine": machine.command_json("info")}


@mcp.tool()
def press_key(machine_id: str, key: str, ctrl: bool = False,
              shift: bool = False, alt: bool = False,
              hold_ms: int = KEY_HOLD_MS) -> str:
    """Press and release a named key, optionally with modifiers.

    Key names are SDL-style: return, escape, space, f1..f12, up/down/left/
    right, home, delete, break, or a single character.
    """
    machine = pool.get(machine_id)
    scancode = keymap.resolve_key(key)
    mods = []
    if ctrl:
        mods.append(keymap.SCANCODES["lctrl"])
    if shift:
        mods.append(keymap.SCANCODES["lshift"])
    if alt:
        mods.append(keymap.SCANCODES["lalt"])

    for m in mods:
        machine.command_text(f"key {m} 1")
    machine.command_text(f"key {scancode} 1")
    machine.command_json(f"run_us {hold_ms * 1000}")
    machine.command_text(f"key {scancode} 0")
    for m in mods:
        machine.command_text(f"key {m} 0")
    machine.command_json(f"run_us {KEY_GAP_MS * 1000}")
    return f"pressed {key}"


@mcp.tool()
def set_key_state(machine_id: str, key: str, down: bool) -> str:
    """Hold or release a key without advancing time.

    For held-key cases such as Shift-Break, or game input where you control the
    timing yourself with run_for_ms.
    """
    scancode = keymap.resolve_key(key)
    pool.get(machine_id).command_text(f"key {scancode} {1 if down else 0}")
    return f"{key} {'down' if down else 'up'}"


@mcp.tool()
def release_all_keys(machine_id: str) -> str:
    """Release every held key."""
    pool.get(machine_id).command_text("keyclear")
    return "all keys released"


@mcp.tool()
def move_mouse(machine_id: str, dx: int, dy: int, buttons: int = 0) -> str:
    """Move the mouse by a relative delta and set the button mask.

    buttons is a bitmask: 1 select, 2 menu, 4 adjust.
    """
    pool.get(machine_id).command_text(f"mouse {dx} {dy} {buttons}")
    return f"mouse moved {dx},{dy} buttons={buttons}"


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _hostfs_path(machine, rel):
    """Resolve a path inside the machine's HostFS sandbox, refusing escapes."""
    base = os.path.abspath(machine.hostfs)
    full = os.path.abspath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        raise EmulatorError(
            f"{rel!r} resolves outside the machine's HostFS sandbox")
    return full


@mcp.tool()
def hostfs_write_file(machine_id: str, path: str, content: str = "",
                      base64_content: str | None = None) -> str:
    """Write a file into the machine's HostFS, visible to the guest immediately.

    This is the fast way to get code into the emulator - no disc image needed.
    The guest sees the sandbox as HostFS::HostFS.$ and it appears on the icon
    bar. Requires RISC OS 3 and the support ROM.

    RISC OS filetypes use ArcEm's convention of a ',xxx' suffix on the host
    filename: 'hello,ffb' is BASIC, ',ff8' Absolute, ',fff' Text. Without a
    suffix the guest sees the file as Text.

    Give binary content as base64_content; text as content.
    """
    machine = pool.get(machine_id)
    full = _hostfs_path(machine, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if base64_content is not None:
        try:
            blob = base64.b64decode(base64_content, validate=True)
        except binascii.Error as e:
            raise EmulatorError(f"base64_content is not valid base64: {e}")
        with open(full, "wb") as f:
            f.write(blob)
        return f"wrote {len(blob)} bytes to {path}"
    data = content.encode("latin-1", "replace")
    with open(full, "wb") as f:
        f.write(data)
    return f"wrote {len(data)} bytes to {path}"


@mcp.tool()
def hostfs_read_file(machine_id: str, path: str,
                     as_base64: bool = False) -> dict:
    """Read a file back out of the machine's HostFS."""
    machine = pool.get(machine_id)
    full = _hostfs_path(machine, path)
    if not os.path.isfile(full):
        raise EmulatorError(f"no file {path!r} in the HostFS sandbox")
    with open(full, "rb") as f:
        blob = f.read()
    out = {"path": path, "size": len(blob)}
    if as_base64:
        out["base64"] = base64.b64encode(blob).decode()
    else:
        out["text"] = blob.decode("latin-1")
    return out


@mcp.tool()
def hostfs_list(machine_id: str, path: str = "") -> list:
    """List the machine's HostFS sandbox."""
    machine = pool.get(machine_id)
    full = _hostfs_path(machine, path)
    if not os.path.isdir(full):
        raise EmulatorError(f"{path!r} is not a directory in the sandbox")
    out = []
    for name in sorted(os.listdir(full)):
        p = os.path.join(full, name)
        out.append({
            "name": name,
            "type": "dir" if os.path.isdir(p) else "file",
            "size": os.path.getsize(p) if os.path.isfile(p) else None,
        })
    return out


@mcp.tool()
def hostfs_delete(machine_id: str, path: str) -> str:
    """Delete a file from the machine's HostFS sandbox."""
    machine = pool.get(machine_id)
    full = _hostfs_path(machine, path)
    if not os.path.isfile(full):
        raise EmulatorError(f"no file {path!r} in the HostFS sandbox")
    os.remove(full)
    return f"deleted {path}"


@mcp.tool()
def load_disc(machine_id: str, path: str, drive: int = 0) -> str:
    """Insert a disc image into a drive.

    The image format is chosen by extension and file size, so use a real
    filename: .adf, .apd, .fdi, .hfe, .jfd or .scp.
    """
    if not os.path.isfile(path):
        raise EmulatorError(f"no such disc image: {path}")
    pool.get(machine_id).command_text(f"disc {drive} {path}")
    return f"loaded {path} into drive {drive}"


@mcp.tool()
def eject_disc(machine_id: str, drive: int = 0) -> str:
    """Eject the disc in a drive."""
    pool.get(machine_id).command_text(f"eject {drive}")
    return f"ejected drive {drive}"


# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------

@mcp.tool()
def start_sound_capture(machine_id: str) -> str:
    """Begin capturing the guest's audio output (48 kHz 16-bit stereo)."""
    return pool.get(machine_id).command_text("sound start")


@mcp.tool()
def save_sound_capture(machine_id: str, path: str) -> str:
    """Stop capturing and write the captured audio to a WAV file."""
    machine = pool.get(machine_id)
    machine.command_text("sound stop")
    data = machine.command("sound fetch")
    wav = png.arsn_to_wav(data)
    with open(path, "wb") as f:
        f.write(wav)
    rate, channels, frames, _ = png.decode_arsn(data)
    return (f"wrote {len(wav)} bytes to {path} "
            f"({frames} frames, {frames / rate:.2f}s at {rate} Hz)")


# ---------------------------------------------------------------------------

def _hex(value):
    """Normalise a user-supplied address to bare hex."""
    text = str(value).strip()
    for prefix in ("&", "0x", "0X"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    try:
        int(text, 16)
    except ValueError:
        raise EmulatorError(
            f"{value!r} is not a hex address (try 8000, &8000 or 0x8000)")
    return text


def main():
    if not os.path.isfile(EXE):
        sys.stderr.write(
            f"arculator-headless not found at {EXE}\n"
            f"Build it with ./configure --enable-headless && make, or set "
            f"ARCULATOR_EXE.\n")
        return 1
    os.makedirs(WORK, exist_ok=True)
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
