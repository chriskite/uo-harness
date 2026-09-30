"""Tests for harness/liveview.py and the viz_server live routes.

Offline: the crop geometry (centred on the character, clamped to the client
area), JPEG sizes per zoom, and /api/live.jpg + /api/live.mjpeg against a
fake capture source (and the 503 when there's no game window). The real
capture (Windows Graphics Capture of the game window) is checked by hand.

Run: python harness/test_liveview.py   (needs numpy + opencv from windows-capture)
"""
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import liveview  # noqa: E402
import viz_server  # noqa: E402

PORT = 12780
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def test_geometry():
    print("== crop geometry ==")
    # a 2560x1494 client at (8, 31) inside the captured frame (title bar above)
    client = (8, 31, 2560, 1494)
    box = liveview.keep_box(2576, 1533, client, keep=(1600, 1200), offset=(0, -40))
    cx, cy = 8 + 1280, 31 + 747 - 40
    check("kept box centred on the character (client centre, 40 px up), full size",
          box == (cx - 800, cy - 600, cx + 800, cy + 600), str(box))
    small = liveview.keep_box(900, 700, (0, 20, 800, 600), keep=(1600, 1200), offset=(0, -40))
    check("a client smaller than the box keeps the whole client area", small == (0, 20, 800, 620), str(small))
    edge = liveview.keep_box(2000, 1500, (0, 0, 2000, 1500), keep=(1600, 1200), offset=(0, -500))
    check("clamped inside the client area (never above its top)", edge == (200, 0, 1800, 1200), str(edge))
    check("zoom boxes are centred in the kept image",
          liveview.zoom_box(1600, 1200, 3) == (480, 360, 1120, 840)
          and liveview.zoom_box(1600, 1200, 1) == (0, 0, 1600, 1200)
          and liveview.zoom_box(800, 600, 2) == (0, 0, 800, 600))
    img = np.zeros((1200, 1600, 3), np.uint8)
    img[590:610, 790:810] = (0, 0, 255)                   # a red mark on the character
    for z in (1, 2, 3):
        dec = cv2.imdecode(np.frombuffer(liveview.render_jpeg(img, z), np.uint8), cv2.IMREAD_COLOR)
        h, w = dec.shape[:2]
        centre = dec[h // 2, w // 2]
        check(f"zoom {z}: 640x480 JPEG with the character in the middle",
              (w, h) == (640, 480) and centre[2] > 150 and centre[0] < 100, f"{(w, h)} {centre}")


class FakeLive:
    def __init__(self, img):
        self.img, self.calls = img, 0

    def latest(self, max_wait=2.0):
        self.calls += 1
        return (self.img, 0.0) if self.img is not None else (None, "no visible window titled 'UO - '*")

    def stop_locked(self):
        pass


class FakeFeed:
    subscribers = []

    def state_body(self):
        return "{}"


def test_routes():
    print("== viz_server live routes ==")
    tmp = tempfile.mkdtemp()
    img = np.full((1200, 1600, 3), 40, np.uint8)
    fake = FakeLive(img)
    srv = viz_server.VizServer(("127.0.0.1", PORT), FakeFeed(), tmp, os.path.join(tmp, "h.db"),
                               live_factory=lambda: fake)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"
    try:
        r = urllib.request.urlopen(f"{base}/api/live.jpg?zoom=3")
        body = r.read()
        check("/api/live.jpg: a JPEG", r.headers["Content-Type"] == "image/jpeg" and body[:2] == b"\xff\xd8")
        r = urllib.request.urlopen(f"{base}/api/live.mjpeg?zoom=2&fps=10", timeout=5)
        ctype = r.headers["Content-Type"]
        chunk = b""
        while chunk.count(b"--uoframe") < 3:
            chunk += r.read1(65536)
        r.close()
        check("/api/live.mjpeg: a multipart stream of JPEG parts",
              ctype == "multipart/x-mixed-replace; boundary=uoframe"
              and chunk.count(b"Content-Type: image/jpeg") >= 2, ctype)
        fake.img = None
        try:
            urllib.request.urlopen(f"{base}/api/live.jpg")
            code, err = 200, ""
        except urllib.error.HTTPError as e:
            code, err = e.code, e.read().decode()
        check("no game window: 503 with the reason", code == 503 and "no visible window" in err, f"{code} {err}")
        srv.live, srv.live_factory = None, None
        try:
            urllib.request.urlopen(f"{base}/api/live.mjpeg")
            code, err = 200, ""
        except urllib.error.HTTPError as e:
            code, err = e.code, e.read().decode()
        check("--no-live: 503 'disabled'", code == 503 and "disabled" in err, f"{code} {err}")
    finally:
        srv.shutdown()
        srv.server_close()


def main():
    test_geometry()
    test_routes()
    print(f"\nliveview: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
