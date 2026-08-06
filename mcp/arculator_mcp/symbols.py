"""Symbol tables from the vasm/vlink toolchain.

Three formats, all produced by a typical Archimedes build:

  vlink -vicelabels <file>   VICE label format, one 'al C:<hex> .<name>' per
                             line. The richest source of code labels, and the
                             one to prefer.
  vlink -M<file>             Link map: section layout plus symbols tagged
                             'local abs' (assembly-time constants) or
                             'local reloc' (real addresses).
  vasm -L<file>              Assembler listing. Parsed only for its symbol
                             table, if present.

The distinction that matters is constants versus addresses. A build defines
hundreds of `.equ` constants - VIDC register values, flags - and in the VICE
format they are indistinguishable from labels. Left unfiltered they poison
address lookup: a constant of 0 claims address 0. Symbols are therefore
classified, and only address-like ones are used to name an address. All of
them remain available for name lookup.
"""
import os
import re


class Symbol:
    __slots__ = ("name", "value", "kind", "source")

    def __init__(self, name, value, kind, source):
        self.name = name
        self.value = value
        self.kind = kind          # "address" or "constant"
        self.source = source

    def __repr__(self):
        return f"<{self.name}=&{self.value:08X} {self.kind}>"


class SymbolTable:
    """Symbols for one loaded program.

    The hard part is telling code labels from assembly-time constants. vlink
    is no help: it tags both 'local abs' when the code is linked to a fixed
    origin, so a real entry point and a VIDC register value look identical.
    Two signals are used instead, in order of reliability:

      1. Section ranges from a link map, when one has been loaded. A symbol
         inside .text/.data/.bss is an address, full stop.
      2. Otherwise, whether the value looks like an Archimedes address. The
         floor is the standard &8000 application load address, which is what
         keeps small constants - frame counts, sizes, flags - out of the
         address table.
    """

    # Used only when no section information is available.
    DEFAULT_FLOOR = 0x8000
    DEFAULT_CEILING = 0x4000000

    def __init__(self, floor=None, ceiling=None):
        self.by_name = {}
        self.addresses = []       # sorted [(value, name)] for address symbols
        self.sections = []        # [(start, end, name)] from a link map
        self.sources = []
        self.floor = self.DEFAULT_FLOOR if floor is None else floor
        self.ceiling = self.DEFAULT_CEILING if ceiling is None else ceiling
        self._dirty = False
        self._pending = []        # symbols seen before sections were known

    # -- loading -----------------------------------------------------------

    def add(self, symbol):
        self.by_name[symbol.name] = symbol
        if symbol.kind == "address":
            self.addresses.append((symbol.value, symbol.name))
            self._dirty = True

    def _classify(self, value):
        if self.sections:
            for start, end, _ in self.sections:
                if start <= value < end:
                    return "address"
            return "constant"
        return "address" if self.floor <= value < self.ceiling else "constant"

    def _reclassify(self):
        """Re-run classification once section ranges become known."""
        self.addresses = []
        self._dirty = True
        for symbol in self.by_name.values():
            symbol.kind = self._classify(symbol.value)
            if symbol.kind == "address":
                self.addresses.append((symbol.value, symbol.name))

    def load(self, path, fmt=None):
        """Load a symbol file, guessing the format from its contents."""
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        with open(path, "r", errors="replace") as f:
            text = f.read()

        if fmt is None:
            fmt = self.detect_format(text)

        before = len(self.by_name)
        if fmt == "vice":
            self._load_vice(text, path)
        elif fmt == "map":
            self._load_vlink_map(text, path)
        elif fmt == "listing":
            self._load_vasm_listing(text, path)
        else:
            raise ValueError(
                f"unrecognised symbol file format for {path}; expected a "
                f"vlink -vicelabels file, a vlink -M map, or a vasm -L listing")

        self.sources.append({"path": path, "format": fmt,
                             "symbols": len(self.by_name) - before})
        return self.sources[-1]

    @staticmethod
    def detect_format(text):
        head = text[:8192]
        if re.search(r"^al\s+C:[0-9a-fA-F]+\s+\.", head, re.M):
            return "vice"
        if "Section mapping" in head or re.search(r"^Symbols of ", head, re.M):
            return "map"
        if re.search(r"^F\d+:\d+", head, re.M) or "Symbols:" in text:
            return "listing"
        return None

    def _load_vice(self, text, source):
        for line in text.splitlines():
            m = re.match(r"^al\s+C:([0-9a-fA-F]+)\s+\.?(\S+)", line.strip())
            if not m:
                continue
            value = int(m.group(1), 16)
            name = m.group(2)
            self.add(Symbol(name, value, self._classify(value), source))

    def _load_vlink_map(self, text, source):
        # Section layout first: it is what makes classification exact.
        #   00008000 .text  (size 923c, allocated 91f8)
        found_sections = False
        for line in text.splitlines():
            m = re.match(r"^\s{0,4}([0-9a-fA-F]{8})\s+(\.\S+)\s+\(size\s+"
                         r"([0-9a-fA-F]+)", line)
            if m:
                start = int(m.group(1), 16)
                size = int(m.group(3), 16)
                if size:
                    self.sections.append((start, start + size, m.group(2)))
                    found_sections = True
        if found_sections:
            self.sections.sort()
            self._reclassify()

        for line in text.splitlines():
            m = re.match(r"^\s+0x([0-9a-fA-F]+)\s+(\S+?):\s*(.*)$", line)
            if not m:
                continue
            value = int(m.group(1), 16)
            name = m.group(2)
            # vlink tags both real labels and assembly-time constants 'abs'
            # when the code is linked to a fixed origin, so the tag is not
            # usable; classification is by section membership instead.
            self.add(Symbol(name, value, self._classify(value), source))

    def _load_vasm_listing(self, text, source):
        in_symbols = False
        for line in text.splitlines():
            if line.startswith("Symbols:"):
                in_symbols = True
                continue
            if not in_symbols:
                continue
            m = re.match(r"^(\S+)\s+.*?([0-9a-fA-F]{4,8})\b", line)
            if m:
                value = int(m.group(2), 16)
                self.add(Symbol(m.group(1), value, self._classify(value), source))

    # -- lookup ------------------------------------------------------------

    def _ensure_sorted(self):
        if self._dirty:
            self.addresses.sort()
            self._dirty = False

    def resolve(self, text):
        """Resolve a symbol name, or a hex address, to an integer.

        Accepts `name`, `name+8`, `name-4`, `&8000`, `0x8000` and bare hex.
        Returns None if it is neither a known symbol nor valid hex.
        """
        text = str(text).strip()

        m = re.match(r"^([A-Za-z_.][\w.$]*)\s*([+-])\s*(\w+)$", text)
        if m and m.group(1) in self.by_name:
            base = self.by_name[m.group(1)].value
            offset = int(m.group(3), 0 if m.group(3).startswith("0x") else 16)
            return base + offset if m.group(2) == "+" else base - offset

        if text in self.by_name:
            return self.by_name[text].value

        stripped = text
        for prefix in ("&", "0x", "0X"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):]
                break
        try:
            return int(stripped, 16)
        except ValueError:
            return None

    def name_for(self, address, max_distance=4096):
        """Nearest preceding address symbol, as (name, offset), or None."""
        self._ensure_sorted()
        if not self.addresses:
            return None

        lo, hi = 0, len(self.addresses)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.addresses[mid][0] <= address:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return None
        value, name = self.addresses[lo - 1]
        offset = address - value
        if offset > max_distance:
            return None
        return name, offset

    def format_address(self, address, max_distance=4096):
        """'&8134 <main_loop+0x18>', or just '&8134' if nothing is near."""
        hit = self.name_for(address, max_distance)
        if not hit:
            return f"&{address:07X}"
        name, offset = hit
        if offset == 0:
            return f"&{address:07X} <{name}>"
        return f"&{address:07X} <{name}+0x{offset:x}>"

    def annotate(self, text, max_distance=4096):
        """Append symbol names to bare hex addresses in disassembly output."""
        def repl(m):
            address = int(m.group(0), 16)
            hit = self.name_for(address, max_distance)
            if not hit:
                return m.group(0)
            name, offset = hit
            suffix = f"+0x{offset:x}" if offset else ""
            return f"{m.group(0)} <{name}{suffix}>"

        out = []
        for line in text.splitlines():
            # The leading address of each disassembly line, and any branch
            # target at the end of it.
            line = re.sub(r"^[0-9A-Fa-f]{6,8}(?= :)", repl, line)
            line = re.sub(r"(?<=\b[BL]\s)([0-9a-f]{6,8})$", repl, line)
            out.append(line)
        return "\n".join(out)

    def stats(self):
        addresses = sum(1 for s in self.by_name.values() if s.kind == "address")
        return {
            "total": len(self.by_name),
            "addresses": addresses,
            "constants": len(self.by_name) - addresses,
            "sources": self.sources,
        }
