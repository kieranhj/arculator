"""SDL scancodes and text-to-keystroke translation.

The emulator's only keyboard input contract is the key[512] array in
plat_input.h, indexed by SDL scancode. keytable.h then maps those positionally
onto Archimedes key codes, so what a key produces in the guest depends on the
*Archimedes* keyboard layout, not the host's. Arc keyboards are UK layout,
which is why SHIFTED below is a UK map - shift-2 is '"', not '@'.
"""

# Values taken from SDL2/SDL_scancode.h.
SCANCODES = {
    "a": 4, "b": 5, "c": 6, "d": 7, "e": 8, "f": 9, "g": 10, "h": 11,
    "i": 12, "j": 13, "k": 14, "l": 15, "m": 16, "n": 17, "o": 18, "p": 19,
    "q": 20, "r": 21, "s": 22, "t": 23, "u": 24, "v": 25, "w": 26, "x": 27,
    "y": 28, "z": 29,
    "1": 30, "2": 31, "3": 32, "4": 33, "5": 34,
    "6": 35, "7": 36, "8": 37, "9": 38, "0": 39,
    "return": 40, "escape": 41, "backspace": 42, "tab": 43, "space": 44,
    "minus": 45, "equals": 46, "leftbracket": 47, "rightbracket": 48,
    "backslash": 49, "nonushash": 50, "semicolon": 51, "apostrophe": 52,
    "grave": 53, "comma": 54, "period": 55, "slash": 56, "capslock": 57,
    "f1": 58, "f2": 59, "f3": 60, "f4": 61, "f5": 62, "f6": 63,
    "f7": 64, "f8": 65, "f9": 66, "f10": 67, "f11": 68, "f12": 69,
    "printscreen": 70, "scrolllock": 71, "pause": 72, "insert": 73,
    "home": 74, "pageup": 75, "delete": 76, "end": 77, "pagedown": 78,
    "right": 79, "left": 80, "down": 81, "up": 82,
    "numlock": 83, "kp_divide": 84, "kp_multiply": 85, "kp_minus": 86,
    "kp_plus": 87, "kp_enter": 88,
    "kp_1": 89, "kp_2": 90, "kp_3": 91, "kp_4": 92, "kp_5": 93,
    "kp_6": 94, "kp_7": 95, "kp_8": 96, "kp_9": 97, "kp_0": 98,
    "kp_period": 99,
    "lctrl": 224, "lshift": 225, "lalt": 226, "lgui": 227,
    "rctrl": 228, "rshift": 229, "ralt": 230, "rgui": 231,
}

# Aliases for the names people actually type.
SCANCODES.update({
    "enter": SCANCODES["return"],
    "esc": SCANCODES["escape"],
    "ctrl": SCANCODES["lctrl"],
    "shift": SCANCODES["lshift"],
    "alt": SCANCODES["lalt"],
    "del": SCANCODES["delete"],
    "pgup": SCANCODES["pageup"],
    "pgdn": SCANCODES["pagedown"],
    "break": SCANCODES["pause"],
})

# Characters reachable without shift, on a UK layout.
UNSHIFTED = {
    " ": "space", "\n": "return", "\r": "return", "\t": "tab",
    "-": "minus", "=": "equals", "[": "leftbracket", "]": "rightbracket",
    "\\": "backslash", "#": "nonushash", ";": "semicolon",
    "'": "apostrophe", "`": "grave", ",": "comma", ".": "period",
    "/": "slash",
}
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    UNSHIFTED[_c] = _c

# Characters needing shift, on a UK layout. Note '"' is shift-2 and '@' is
# shift-apostrophe, which is where a US map would disagree.
SHIFTED = {
    "!": "1", '"': "2", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "minus", "+": "equals", "{": "leftbracket", "}": "rightbracket",
    "|": "backslash", "~": "nonushash", ":": "semicolon", "@": "apostrophe",
    "<": "comma", ">": "period", "?": "slash",
}
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    SHIFTED[_c] = _c.lower()

# GBP is shift-3 on a UK keyboard. '3' shifted gives '#' on US maps, hence the
# separate entry rather than folding it into SHIFTED's loop.
SHIFTED["£"] = "3"


class UntypableCharacter(ValueError):
    """Raised for a character with no key combination on an Arc keyboard."""


def char_to_keystroke(ch):
    """Return (scancode, needs_shift) for a single character."""
    if ch in UNSHIFTED:
        return SCANCODES[UNSHIFTED[ch]], False
    if ch in SHIFTED:
        return SCANCODES[SHIFTED[ch]], True
    raise UntypableCharacter(
        f"no Archimedes key combination for {ch!r} (U+{ord(ch):04X})")


def resolve_key(name):
    """Resolve a key name or single character to a scancode."""
    key = name.lower()
    if key in SCANCODES:
        return SCANCODES[key]
    if len(name) == 1:
        return char_to_keystroke(name)[0]
    raise ValueError(
        f"unknown key {name!r}; expected one of {', '.join(sorted(SCANCODES))} "
        f"or a single character")
