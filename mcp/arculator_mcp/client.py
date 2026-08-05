"""Process supervision and control-socket client for arculator-headless.

Every piece of machine state in Arculator is a file-scope global - armregs[],
mempoint[], the vidc/ioc/memc structs - so there is no machine context to
instantiate twice. One emulator process is therefore one machine, and running
several means running several processes. That is what MachinePool does.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time

DEFAULT_TIMEOUT = 300.0

# Directories that might hold the runtime DLLs a MinGW build links against
# (SDL2, zlib, libstdc++, libwinpthread). The emulator resolves these from
# PATH, so a session started outside the MSYS2 shell fails at exec with a
# bare "cannot open shared object file" and no other diagnostic.
_DLL_DIR_CANDIDATES = (
    r"C:\msys64\ucrt64\bin",
    r"C:\msys64\mingw64\bin",
)


def _child_env():
    """Environment for the emulator, with the toolchain DLL directory added."""
    env = dict(os.environ)
    if os.name != "nt":
        return env

    extra = []
    override = env.get("ARCULATOR_DLL_PATH")
    candidates = [override] if override else list(_DLL_DIR_CANDIDATES)
    existing = env.get("PATH", "").split(os.pathsep)
    existing_lower = {p.rstrip("\\/").lower() for p in existing}

    for path in candidates:
        if path and os.path.isdir(path) \
                and path.rstrip("\\/").lower() not in existing_lower:
            extra.append(path)

    if extra:
        env["PATH"] = os.pathsep.join(extra + existing)
    return env


class EmulatorError(RuntimeError):
    """The emulator rejected a command."""


class Machine:
    """One arculator-headless process and its control connection."""

    def __init__(self, exe, root, config=None, config_file=None,
                 hostfs=None, timeout=DEFAULT_TIMEOUT):
        self.exe = exe
        self.root = root
        self.config = config
        self.hostfs = hostfs
        self._buf = b""
        self._lock = threading.Lock()
        self.closed = False

        argv = [exe, "--port", "0", "--root", root]
        if config_file:
            argv += ["--config-file", config_file]
        elif config:
            argv += ["--config", config]
        if hostfs:
            os.makedirs(hostfs, exist_ok=True)
            argv += ["--hostfs", hostfs]

        self.proc = subprocess.Popen(
            argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=_child_env())

        line = self.proc.stdout.readline().strip()
        if not line.startswith("PORT "):
            err = self._drain_stderr()
            self.proc.kill()
            hint = ""
            if "shared object file" in err or "shared libraries" in err:
                hint = ("\nThe emulator could not find its runtime DLLs. Set "
                        "ARCULATOR_DLL_PATH to the directory holding SDL2.dll "
                        "(typically C:\\msys64\\ucrt64\\bin).")
            raise EmulatorError(
                f"emulator did not start (said {line!r})\n{err}{hint}")
        self.port = int(line.split()[1])

        self.sock = socket.create_connection(("127.0.0.1", self.port),
                                             timeout=timeout)
        self.sock.settimeout(timeout)

    # -- transport ---------------------------------------------------------

    def _drain_stderr(self):
        try:
            self.proc.stderr.flush()
        except Exception:
            pass
        try:
            return self.proc.stderr.read() or ""
        except Exception:
            return ""

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(262144)
            if not chunk:
                raise EmulatorError("emulator closed the control connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_line(self):
        while b"\n" not in self._buf:
            chunk = self.sock.recv(262144)
            if not chunk:
                raise EmulatorError("emulator closed the control connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("utf-8", "replace")

    def command(self, text):
        """Send one command; return the raw payload bytes."""
        if self.closed:
            raise EmulatorError("machine has been destroyed")
        with self._lock:
            self.sock.sendall((text + "\n").encode())
            header = self._read_line()
            status, _, length = header.partition(" ")
            try:
                payload = self._read_exact(int(length))
            except ValueError:
                raise EmulatorError(f"malformed response header {header!r}")
            if status != "OK":
                raise EmulatorError(payload.decode("utf-8", "replace"))
            return payload

    def command_text(self, text):
        return self.command(text).decode("utf-8", "replace")

    def command_json(self, text):
        return json.loads(self.command(text))

    # -- lifecycle ---------------------------------------------------------

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.sendall(b"quit\n")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def alive(self):
        return not self.closed and self.proc.poll() is None


class MachinePool:
    """Named collection of running machines."""

    def __init__(self, exe, root, sandbox_root):
        self.exe = exe
        self.root = root
        self.sandbox_root = sandbox_root
        self.machines = {}
        self._counter = 0
        self._lock = threading.Lock()

    def create(self, config=None, config_file=None, machine_id=None,
               hostfs=None):
        with self._lock:
            if machine_id is None:
                self._counter += 1
                machine_id = f"m{self._counter}"
            if machine_id in self.machines:
                raise EmulatorError(f"machine {machine_id!r} already exists")

        if hostfs is None:
            hostfs = os.path.join(self.sandbox_root, machine_id, "hostfs")
        os.makedirs(hostfs, exist_ok=True)

        machine = Machine(self.exe, self.root, config=config,
                          config_file=config_file, hostfs=hostfs)
        machine.machine_id = machine_id
        with self._lock:
            self.machines[machine_id] = machine
        return machine_id, machine

    def get(self, machine_id):
        machine = self.machines.get(machine_id)
        if machine is None:
            known = ", ".join(sorted(self.machines)) or "none running"
            raise EmulatorError(
                f"no machine {machine_id!r} (running: {known})")
        if not machine.alive():
            err = machine._drain_stderr()
            del self.machines[machine_id]
            raise EmulatorError(
                f"machine {machine_id!r} has exited"
                + (f":\n{err}" if err.strip() else ""))
        return machine

    def destroy(self, machine_id, wipe_sandbox=False):
        machine = self.machines.pop(machine_id, None)
        if machine is None:
            raise EmulatorError(f"no machine {machine_id!r}")
        machine.close()
        if wipe_sandbox:
            sandbox = os.path.join(self.sandbox_root, machine_id)
            shutil.rmtree(sandbox, ignore_errors=True)

    def destroy_all(self):
        for machine_id in list(self.machines):
            try:
                self.destroy(machine_id)
            except Exception:
                pass

    def list(self):
        out = []
        for machine_id, machine in sorted(self.machines.items()):
            out.append({
                "machine_id": machine_id,
                "config": machine.config,
                "hostfs": machine.hostfs,
                "port": machine.port,
                "alive": machine.alive(),
            })
        return out
