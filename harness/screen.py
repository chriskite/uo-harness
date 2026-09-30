"""Screenshot of the game window, for the overseer to look at (ctl screenshot).

Windows Graphics Capture (WGC, the compositor's window capture, as in OBS's
"Window Capture (Windows 10+)"): it hands over the window's own composited
content even when other windows cover it. It's passive and outside the client
(AGENTS.md rule 2; ANTICHEAT.md §8.1):
- no injection, and no message goes to the client: WGC gets frames from DWM;
  the window is found with EnumWindows + GetWindowTextW, which for another
  process's window reads the caption the window manager keeps (no
  WM_GETTEXT, Win32 docs); geometry comes from GetClientRect, ClientToScreen
  and the DWM frame bounds (window manager reads)
- no capture border (draw_border=False) and no cursor in the image
Verified live 2026-09-30 on the elevated ClassicUO window while it was fully
covered by other windows. A minimized window has no content to capture.

Dependency: `windows-capture` (PyPI, 2.0.1; pulls numpy + opencv-python).
Output: PNG of the client area (the game view, no title bar), or a crop of it.

CLI: python harness/screen.py [--out file.png] [--crop X Y W H] [--title-prefix "UO - "]
"""
import argparse
import ctypes
import json
import os
import struct
import sys
import threading
import time
import zlib
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCREENS_DIR = os.path.join(ROOT, "logs", "screens")     # gitignored runtime data
TITLE_PREFIX = "UO - "                                  # ClassicUO: "UO - <character> - <version>"
FRAME_TIMEOUT_S = 5.0
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class ScreenError(Exception):
    pass


def _user32():
    if sys.platform != "win32":
        raise ScreenError("screen capture needs Windows")
    user32 = ctypes.windll.user32
    try:                                   # physical pixels on scaled displays
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass
    return user32


def find_window(title_prefix: str = TITLE_PREFIX):
    """(hwnd, title) of the first visible top-level window whose caption
    starts with title_prefix, or None."""
    user32 = _user32()
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.startswith(title_prefix):
            found.append((hwnd, buf.value))
            return False
        return True
    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else None


def client_offset(hwnd) -> tuple[int, int, int, int]:
    """The client area inside the captured frame: (x, y, w, h). WGC frames
    cover the DWM extended frame bounds (title bar included, shadow not)."""
    user32 = _user32()
    fr = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(fr),
                                                  ctypes.sizeof(fr)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(fr))
    cr = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x - fr.left, pt.y - fr.top, cr.right, cr.bottom


def grab_window(hwnd, timeout: float = FRAME_TIMEOUT_S):
    """One WGC frame of the window: (width, height, top-down BGRA bytes)."""
    try:
        from windows_capture import WindowsCapture
    except ImportError as e:
        raise ScreenError(f"windows-capture isn't installed (pip install --user windows-capture): {e}")
    got, err = {}, []
    done = threading.Event()
    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

    @cap.event
    def on_frame_arrived(frame, control):
        if not got:
            buf = frame.frame_buffer                 # zero-copy view: copy before stopping
            got.update(w=frame.width, h=frame.height, data=buf[:, :, :4].copy())
        control.stop()
        done.set()

    @cap.event
    def on_closed():
        done.set()

    try:
        cap.start_free_threaded()
    except Exception as e:  # noqa: BLE001  (the native layer raises plain Exception)
        err.append(e)
    if err:
        raise ScreenError(f"window capture failed: {err[0]}")
    if not done.wait(timeout) or not got:
        raise ScreenError(f"no frame within {timeout:.0f} s (is the window minimized?)")
    return got["w"], got["h"], got["data"]


def png_bytes(w: int, h: int, bgra: bytes) -> bytes:
    """A truecolour PNG (8-bit RGB) from top-down BGRA."""
    stride = w * 4
    raw = bytearray()
    row_rgb = bytearray(w * 3)
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        row_rgb[0::3] = row[2::4]
        row_rgb[1::3] = row[1::4]
        row_rgb[2::3] = row[0::4]
        raw += b"\x00" + row_rgb

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def screenshot(out: str | None = None, crop=None, title_prefix: str = TITLE_PREFIX) -> dict:
    """Capture the game window's client area (or `crop` = (x, y, w, h) in
    client coordinates) to a PNG; returns metadata. Raises ScreenError."""
    user32 = _user32()
    win = find_window(title_prefix)
    if win is None:
        raise ScreenError(f"no visible window titled {title_prefix!r}* (is the client running?)")
    hwnd, title = win
    if user32.IsIconic(hwnd):
        raise ScreenError("the game window is minimized; there is nothing to capture")
    fw, fh, img = grab_window(hwnd)
    cx, cy, cw, ch = client_offset(hwnd)
    x, y, w, h = (0, 0, cw, ch) if crop is None else crop
    x0, y0 = max(0, cx + x), max(0, cy + y)
    x1, y1 = min(fw, cx + x + w), min(fh, cy + y + h)
    if x1 <= x0 or y1 <= y0:
        raise ScreenError(f"crop {crop} is outside the {cw}x{ch} client area")
    part = img[y0:y1, x0:x1]
    data = png_bytes(x1 - x0, y1 - y0, part.tobytes())
    if out is None:
        os.makedirs(SCREENS_DIR, exist_ok=True)
        out = os.path.join(SCREENS_DIR, time.strftime("screen_%Y%m%d_%H%M%S.png"))
    with open(out, "wb") as f:
        f.write(data)
    return {"ok": True, "path": out, "window": title, "size": [x1 - x0, y1 - y0], "client_size": [cw, ch],
            "crop": list(crop) if crop else None, "foreground": user32.GetForegroundWindow() == hwnd,
            "bytes": len(data)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out")
    ap.add_argument("--crop", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    ap.add_argument("--title-prefix", default=TITLE_PREFIX)
    a = ap.parse_args(argv)
    try:
        print(json.dumps(screenshot(a.out, a.crop, a.title_prefix)))
        return 0
    except ScreenError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
