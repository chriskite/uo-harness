"""Vendor Search capture tests (harness/vendor_search.py, docs/NOTES.md "Vendor Search capture").

  1. The native host over real native-messaging framing, started the way Firefox starts it
     (extra arguments): a search page becomes listing rows (per-item price, vendor, tile,
     listed time), a vendor's sales take the vendor serial from the URL, /prod-api/ and other
     letter case map to the canonical endpoint, a non-JSON body is refused for good
     (retry false) and stores nothing, a ping answers, EOF ends the host cleanly.
  2. reparse rebuilds the same rows from the stored responses.
  3. asks: each item at its newest sighting (a repriced item shows its new price once),
     every word must match, sightings older than the window are left out.
"""

import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory  # noqa: E402
import vendor_search as vs  # noqa: E402

FAILURES = []
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_search.py")
PORTAL = "https://portal.uooutlands.com"


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def frame(obj):
    data = json.dumps(obj).encode("utf-8")
    return struct.pack("=I", len(data)) + data


def replies(raw):
    out, i = [], 0
    while i < len(raw):
        n = struct.unpack("=I", raw[i:i + 4])[0]
        out.append(json.loads(raw[i + 4:i + 4 + n]))
        i += 4 + n
    return out


def item(serial, name, price, amount=1, vendor=0x1111, x=1500, y=1600, listed="2026-10-08T12:00:00Z"):
    return {"id": serial, "itemSerial": serial, "name": name, "price": price, "amount": amount,
            "totalPrice": price * amount, "vendorName": "Bob", "vendorSerial": vendor,
            "vendorLocationX": x, "vendorLocationY": y, "listedTime": listed,
            "properties": [{"name": "Durability", "value": "40/40"}]}


def search_body(items):
    return {"items": items, "totalCount": len(items), "page": 0, "pageSize": 20}


def test_host(db):
    msgs = [
        {"type": "ping"},
        {"type": "capture", "t": 1000.0, "url": f"{PORTAL}/api/VendorSearch/Search", "method": "POST",
         "status": 200, "page": f"{PORTAL}/vendor-search?searchTerm=board",
         "request": json.dumps({"page": 0, "filterParams": {"name": "board"}}),
         "response": json.dumps(search_body([
             item(0x40000001, "board commodity deed", 135000.0, listed="2026-10-08T12:00:00Z"),
             item(0x40000002, "copperwood board", 20.0, amount=500, listed="2026-10-08T12:00:00")]))},
        {"type": "capture", "t": 1001.0, "url": f"{PORTAL}/prod-api/vendorsearch/VendorSales?vendorSerial=4660",
         "method": "POST", "status": 200,
         "response": json.dumps({"items": [{"itemName": "copperwood board", "amount": 100, "pricePerItem": 19.0,
                                            "totalPrice": 1900.0, "saleDate": "2026-10-07T08:00:00Z"}],
                                 "totalCount": 1})},
        {"type": "capture", "t": 1002.0, "url": f"{PORTAL}/api/VendorSearch/Search", "status": 200,
         "response": "<html>Cloudflare</html>"},
    ]
    p = subprocess.run([sys.executable, SCRIPT, "host", "--db", db, "C:\\x\\host.json", vs.EXTENSION_ID],
                       input=b"".join(frame(m) for m in msgs), capture_output=True, timeout=60)
    got = replies(p.stdout)
    check(p.returncode == 0, f"host exits 0 at EOF (rc {p.returncode}, stderr {p.stderr[-300:]!r})")
    check(len(got) == 4, f"one reply per message: {got}")
    if len(got) != 4:
        return
    check(got[0] == {"ok": True, "db": db}, f"ping: {got[0]}")
    check(got[1]["ok"] and got[1]["endpoint"] == "VendorSearch/Search" and got[1]["rows"] == 2,
          f"search page: 2 listing rows {got[1]}")
    check(got[2]["ok"] and got[2]["endpoint"] == "VendorSearch/VendorSales" and got[2]["rows"] == 1,
          f"/prod-api/ + lower case -> VendorSearch/VendorSales: {got[2]}")
    check(not got[3]["ok"] and got[3]["retry"] is False, f"non-JSON refused for good: {got[3]}")

    con = sqlite3.connect(db)
    caps = con.execute("SELECT endpoint, page, request FROM vendor_captures ORDER BY id").fetchall()
    check([c[0] for c in caps] == ["VendorSearch/Search", "VendorSearch/VendorSales"],
          f"two captures stored, the refused one not: {caps}")
    check(json.loads(caps[0][2])["filterParams"]["name"] == "board" and "searchTerm=board" in caps[0][1],
          "the search request body and the page url are kept")
    rows = con.execute("SELECT kind, name, price_gp, amount, total_gp, item_serial, vendor_serial, vendor_name, "
                       "x, y, at, t FROM vendor_prices ORDER BY id").fetchall()
    con.close()
    want_at = 1791460800.0   # 2026-10-08T12:00:00Z
    check(rows[0] == ("listing", "board commodity deed", 135000.0, 1, 135000.0, 0x40000001, 0x1111, "Bob",
                      1500, 1600, want_at, 1000.0), f"listing row: {rows[0]}")
    check(rows[1][2:4] == (20.0, 500) and rows[1][10] == want_at,
          f"per-item price, amount; listed time without an offset read as UTC: {rows[1]}")
    check(rows[2][:4] == ("sale", "copperwood board", 19.0, 100) and rows[2][6] == 4660
          and rows[2][10] == 1791360000.0,
          f"sale: pricePerItem, vendor serial from the URL, sale date: {rows[2]}")
    check(len(rows) == 3, "a priced item's nested properties don't become rows")


def test_reparse(db):
    con = memory.connect(db)
    before = con.execute("SELECT capture, t, kind, name, price_gp, at, data FROM vendor_prices ORDER BY id").fetchall()
    out = vs.reparse(con)
    after = con.execute("SELECT capture, t, kind, name, price_gp, at, data FROM vendor_prices ORDER BY id").fetchall()
    con.close()
    check(out == {"captures": 2, "rows": 3} and after == before, f"reparse rebuilds the same rows: {out}")


def test_asks():
    with tempfile.TemporaryDirectory() as td:
        con = memory.connect(os.path.join(td, "a.db"))
        now = time.time()

        def cap(t, items):
            vs.store(con, {"t": t, "url": f"{PORTAL}/api/VendorSearch/Search",
                           "response": json.dumps(search_body(items))})

        cap(now - 20 * 86400, [item(1, "goldenwood board", 5.0)])             # outside 7 days
        cap(now - 3600, [item(2, "goldenwood board", 80.0), item(3, "goldenwood board", 70.0),
                         item(4, "goldenwood bow", 900.0)])
        cap(now - 60, [item(2, "goldenwood board", 65.0)])                     # #2 repriced
        got = vs.asks(con, ["goldenwood", "board"], days=7, n=10)
        con.close()
        check([(r["name"], r["gp"]) for r in got] == [("goldenwood board", 65.0), ("goldenwood board", 70.0)],
              f"newest sighting per item, all words, 7-day window: {got}")


def main():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "harness.db")
        test_host(db)
        test_reparse(db)
    test_asks()
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
