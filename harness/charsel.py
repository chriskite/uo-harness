"""Character selectors: which logged-in character a request is for.

One syntax everywhere (the state port's `char` field, ctl `--char`, viz `?char=`,
env `UO_CHAR`, runner `--char`): `0x...` hex or all digits is a player serial;
anything else is a character name, compared case-insensitively (str.casefold).
Stdlib only, so the proxy, ctl, viz_server, agent_link and alerts can all import it.
"""

# A control-port frame starting with this binds the connection to a character:
# `@char <selector>` -> `OK <label>` or `ERR <reason>` (harness/proxy.py handle_control).
CONTROL_PREFIX = b"@char "


def parse(sel: str) -> int | str:
    """A selector -> serial (int) or casefolded name (str)."""
    s = (sel or "").strip()
    if not s:
        raise ValueError("empty character selector")
    if s[:2].lower() == "0x":
        return int(s, 16)
    if s.isdigit():
        return int(s)
    return s.casefold()


def matches(p: int | str, serial: int | None, name: str | None) -> bool:
    """Does parsed selector `p` name the character (serial, name)?"""
    if isinstance(p, int):
        return serial == p
    return name is not None and name.casefold() == p


def label(serial: int | None, name: str | None) -> str:
    """How a session is named in messages: its name, else its serial, else 'unidentified'."""
    if name:
        return name
    if serial is not None:
        return f"0x{serial:08X}"
    return "unidentified"


def hex_serial(serial: int | None) -> str | None:
    return None if serial is None else f"0x{serial:08X}"


def meta_key(base: str, char_serial: int | None) -> str:
    """Per-character meta key: `base` for no character, else `base:0xSSSSSSSS`."""
    return base if char_serial is None else f"{base}:0x{char_serial:08X}"
