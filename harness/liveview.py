"""Live view of the character for the visualizer: a cropped, continuous capture
of the game window, served as JPEG frames (viz_server /api/live.mjpeg).

Capture is the same passive Windows Graphics Capture as screen.py (ANTICHEAT.md
§8.16): DWM hands us the window's composited frames, nothing is sent to the
client, no border, no cursor. It runs only while someone watches: the first
frame request starts it, and it stops IDLE_S after the last one.

Where the character is: ClassicUO draws the player on the centre tile of the
game viewport. With the viewport filling the window (this machine's layout),
that's the client-area centre; the sprite's middle is ~40 px above it
(measured 2026-09-30 on a 2560x1494 client). A different viewport layout would
need a different centre [INFERENCE for other layouts].

Throttling: WGC's minimum_update_interval limits frames to CAPTURE_FPS; each
callback copies only the KEEP box around the character (not the whole window).

Process isolation: the native capture runs in a helper process (`python liveview.py
--helper HWND FPS`, frames on its stdout), never in the viz server. windows_capture 2.0.1
raised "Windows fatal exception: access violation" inside `start_free_threaded` on
2026-10-04, killing the whole server (the user had closed and reopened the game while a
capture was running). Now such a crash costs only the helper: the viewer sees an error,
and a new helper starts after RESTART_BACKOFF_S.
"""
import os
import struct
import subprocess
import sys
import threading
import time
from collections import deque

import screen

CAPTURE_FPS = 10
KEEP = (1600, 1200)                    # w, h kept per frame around the character (the widest zoom)
CHAR_OFFSET = (0, -40)                 # character sprite centre relative to the client-area centre
ZOOMS = {1: (1600, 1200), 2: (960, 720), 3: (640, 480)}   # crop per zoom level, 4:3
OUT_WIDTH = 640                        # served frame width by default (height follows 4:3)
MIN_OUT_WIDTH, MAX_OUT_WIDTH = 320, 1600   # bounds for a requested width (?w=); 1600 = the widest crop
JPEG_QUALITY = 75
IDLE_S = 15.0
GEOMETRY_REFRESH_S = 1.0
RESTART_BACKOFF_S = 5.0                # after a helper died abnormally, no new one for this long
FRAME_HEADER = struct.Struct("<4sII")  # helper -> server, per frame: magic, height, width; then h*w*3 BGR bytes
FRAME_MAGIC = b"LVF1"


def keep_box(frame_w: int, frame_h: int, client: tuple, keep=KEEP, offset=CHAR_OFFSET) -> tuple:
    """(x0, y0, x1, y1) inside a captured frame: the `keep` box centred on the
    character, clamped to the client area (client = (x, y, w, h) inside the
    frame). A client smaller than the box yields the whole client area."""
    cx, cy, cw, ch = client
    w, h = min(keep[0], cw), min(keep[1], ch)
    mx, my = cx + cw // 2 + offset[0], cy + ch // 2 + offset[1]
    x0 = min(max(cx, mx - w // 2), cx + cw - w)
    y0 = min(max(cy, my - h // 2), cy + ch - h)
    x0, y0 = max(0, x0), max(0, y0)
    return x0, y0, min(frame_w, x0 + w), min(frame_h, y0 + h)


def zoom_box(kept_w: int, kept_h: int, zoom: int) -> tuple:
    """(x0, y0, x1, y1) of a zoom level's crop centred in the kept image."""
    w, h = ZOOMS.get(zoom, ZOOMS[2])
    w, h = min(w, kept_w), min(h, kept_h)
    x0, y0 = (kept_w - w) // 2, (kept_h - h) // 2
    return x0, y0, x0 + w, y0 + h


def _read_exact(f, n: int):
    """n bytes from a binary stream as a bytearray, or None at EOF."""
    buf = bytearray(n)
    view, got = memoryview(buf), 0
    while got < n:
        r = f.readinto(view[got:])
        if not r:
            return None
        got += r
    return buf


class LiveCapture:
    """One capture session for all viewers; `latest()` starts it on demand."""

    def __init__(self, title_prefix: str = screen.TITLE_PREFIX, fps: int = CAPTURE_FPS, idle_s: float = IDLE_S):
        self.title_prefix, self.fps, self.idle_s = title_prefix, fps, idle_s
        self.lock = threading.Lock()
        self.proc = None                    # the helper process, while one is wanted
        self.hwnd = None
        self.image = None                   # latest kept crop (h, w, 3) BGR, or None
        self.image_t = 0.0
        self.last_use = 0.0
        self.error = None
        self.stderr_tail = deque(maxlen=6)
        self._retry_after = 0.0
        self._stopper = None

    def latest(self, max_wait: float = 2.0):
        """(BGR image, age seconds) of the newest frame, starting the capture if
        needed; (None, error text) when there's nothing to show."""
        with self.lock:
            self.last_use = time.monotonic()
            if self.proc is None:
                self._start()
        end = time.monotonic() + max_wait
        while self.image is None and self.error is None and time.monotonic() < end:
            time.sleep(0.05)
        img = self.image
        if img is None:
            return None, self.error or "no frame yet (is the game window minimized?)"
        return img, time.monotonic() - self.image_t

    def _start(self):
        if time.monotonic() < self._retry_after:
            return                          # the last helper crashed; self.error says so
        win = screen.find_window(self.title_prefix)
        if win is None:
            self.error = f"no visible window titled {self.title_prefix!r}* (is the client running?)"
            return
        self.hwnd, self.error, self.image = win[0], None, None
        self.stderr_tail.clear()
        cmd = [sys.executable, "-u", os.path.abspath(__file__), "--helper", str(self.hwnd), str(self.fps)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=flags)
        except OSError as e:
            self.error = f"window capture helper failed to start: {e}"
            return
        self.proc = proc
        threading.Thread(target=self._read_frames, args=(proc,), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()
        if self._stopper is None or not self._stopper.is_alive():
            self._stopper = threading.Thread(target=self._idle_stop, daemon=True)
            self._stopper.start()

    def _drain_stderr(self, proc):
        for line in proc.stderr:
            self.stderr_tail.append(line.decode("utf-8", "replace").rstrip())

    def _read_frames(self, proc):
        import numpy as np
        while True:
            head = _read_exact(proc.stdout, FRAME_HEADER.size)
            if head is None:
                break
            magic, h, w = FRAME_HEADER.unpack(head)
            data = _read_exact(proc.stdout, h * w * 3) if magic == FRAME_MAGIC else None
            if data is None:
                break
            if self.proc is proc:           # not a helper already stopped on purpose
                self.image, self.image_t = np.frombuffer(data, np.uint8).reshape(h, w, 3), time.monotonic()
        code = proc.wait()
        with self.lock:
            if self.proc is not proc:
                return                      # stopped on purpose (idle, or the server closing)
            self.proc, self.image = None, None
            if code == 0:
                self.error = f"the game window closed ({self.title_prefix!r}*)"
            else:
                why = (self.stderr_tail[-1] if self.stderr_tail else "no output")[:200]
                self.error = f"window capture helper crashed (exit {code & 0xFFFFFFFF:#x}): {why}"
                self._retry_after = time.monotonic() + RESTART_BACKOFF_S

    def _idle_stop(self):
        while True:
            time.sleep(1.0)
            with self.lock:
                if self.proc is None:
                    return
                if time.monotonic() - self.last_use > self.idle_s:
                    self.stop_locked()
                    return

    def stop_locked(self):
        proc, self.proc = self.proc, None
        self.image = None
        if proc is not None:
            try:
                proc.stdin.close()          # EOF on its stdin is the stop signal
            except OSError:
                pass
            try:
                proc.wait(3.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    @property
    def running(self) -> bool:
        return self.proc is not None


def _helper_main(hwnd: int, fps: int) -> int:
    """The helper process: capture window `hwnd` and write kept crops to stdout until
    stdin reaches EOF (the server stopped or died) or the window closes."""
    out = sys.stdout.buffer
    sys.stdout = sys.stderr                 # a stray print must not corrupt the frame stream
    from windows_capture import WindowsCapture
    done = threading.Event()
    geo = {"client": None, "t": 0.0}
    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd,
                         minimum_update_interval=max(1, 1000 // fps))

    @cap.event
    def on_frame_arrived(frame, control):
        now = time.monotonic()
        if geo["client"] is None or now - geo["t"] > GEOMETRY_REFRESH_S:
            geo["client"], geo["t"] = screen.client_offset(hwnd), now
        buf = frame.frame_buffer                      # zero-copy (h, w, 4) BGRA view: copy the crop only
        x0, y0, x1, y1 = keep_box(frame.width, frame.height, geo["client"])
        try:
            out.write(FRAME_HEADER.pack(FRAME_MAGIC, y1 - y0, x1 - x0))
            out.write(buf[y0:y1, x0:x1, :3].tobytes())
            out.flush()
        except OSError:
            done.set()                                # the server is gone

    @cap.event
    def on_closed():
        done.set()

    control = cap.start_free_threaded()
    threading.Thread(target=lambda: (sys.stdin.buffer.read(), done.set()), daemon=True).start()
    done.wait()
    try:
        control.stop()
    except Exception:  # noqa: BLE001
        pass
    sys.stderr.flush()
    os._exit(0)                                       # don't wait on native capture threads


def render_jpeg(img, zoom: int, out_width: int = OUT_WIDTH, quality: int = JPEG_QUALITY) -> bytes:
    """JPEG of a zoom level's crop of a kept image, scaled to out_width."""
    import cv2
    h, w = img.shape[:2]
    x0, y0, x1, y1 = zoom_box(w, h, zoom)
    part = img[y0:y1, x0:x1]
    ph, pw = part.shape[:2]
    if pw != out_width:
        part = cv2.resize(part, (out_width, max(1, round(ph * out_width / pw))),
                          interpolation=cv2.INTER_AREA if pw > out_width else cv2.INTER_LINEAR)
    ok, data = cv2.imencode(".jpg", part, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return data.tobytes()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--helper":
        sys.exit(_helper_main(int(sys.argv[2]), int(sys.argv[3])))
    sys.exit("usage: liveview.py --helper HWND FPS  (run by LiveCapture)")
