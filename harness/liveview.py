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
"""
import threading
import time

import screen

CAPTURE_FPS = 10
KEEP = (1600, 1200)                    # w, h kept per frame around the character (the widest zoom)
CHAR_OFFSET = (0, -40)                 # character sprite centre relative to the client-area centre
ZOOMS = {1: (1600, 1200), 2: (960, 720), 3: (640, 480)}   # crop per zoom level, 4:3
OUT_WIDTH = 640                        # served frame width (height follows 4:3)
JPEG_QUALITY = 75
IDLE_S = 15.0
GEOMETRY_REFRESH_S = 1.0


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


class LiveCapture:
    """One capture session for all viewers; `latest()` starts it on demand."""

    def __init__(self, title_prefix: str = screen.TITLE_PREFIX, fps: int = CAPTURE_FPS, idle_s: float = IDLE_S):
        self.title_prefix, self.fps, self.idle_s = title_prefix, fps, idle_s
        self.lock = threading.Lock()
        self.control = None
        self.hwnd = None
        self.client = None                  # (x, y, w, h) of the client area inside a frame
        self.client_t = 0.0
        self.image = None                   # latest kept crop (h, w, 3) BGR, or None
        self.image_t = 0.0
        self.last_use = 0.0
        self.error = None
        self._stopper = None

    def latest(self, max_wait: float = 2.0):
        """(BGR image, age seconds) of the newest frame, starting the capture if
        needed; (None, error text) when there's nothing to show."""
        with self.lock:
            self.last_use = time.monotonic()
            if self.control is None:
                self._start()
        end = time.monotonic() + max_wait
        while self.image is None and self.error is None and time.monotonic() < end:
            time.sleep(0.05)
        img = self.image
        if img is None:
            return None, self.error or "no frame yet (is the game window minimized?)"
        return img, time.monotonic() - self.image_t

    def _start(self):
        from windows_capture import WindowsCapture
        win = screen.find_window(self.title_prefix)
        if win is None:
            self.error = f"no visible window titled {self.title_prefix!r}* (is the client running?)"
            return
        self.hwnd, self.error, self.image = win[0], None, None
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=self.hwnd,
                             minimum_update_interval=max(1, 1000 // self.fps))

        @cap.event
        def on_frame_arrived(frame, control):
            now = time.monotonic()
            if self.client is None or now - self.client_t > GEOMETRY_REFRESH_S:
                self.client, self.client_t = screen.client_offset(self.hwnd), now
            buf = frame.frame_buffer                  # zero-copy (h, w, 4) BGRA view: copy the crop only
            x0, y0, x1, y1 = keep_box(frame.width, frame.height, self.client)
            self.image = buf[y0:y1, x0:x1, :3].copy()
            self.image_t = now

        @cap.event
        def on_closed():
            with self.lock:
                self.control = None

        try:
            self.control = cap.start_free_threaded()
        except Exception as e:  # noqa: BLE001  (the native layer raises plain Exception)
            self.error, self.control = f"window capture failed: {e}", None
            return
        if self._stopper is None or not self._stopper.is_alive():
            self._stopper = threading.Thread(target=self._idle_stop, daemon=True)
            self._stopper.start()

    def _idle_stop(self):
        while True:
            time.sleep(1.0)
            with self.lock:
                if self.control is None:
                    return
                if time.monotonic() - self.last_use > self.idle_s:
                    self.stop_locked()
                    return

    def stop_locked(self):
        ctl, self.control = self.control, None
        self.image, self.client = None, None
        if ctl is not None:
            try:
                ctl.stop()
            except Exception:  # noqa: BLE001
                pass

    @property
    def running(self) -> bool:
        return self.control is not None


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
