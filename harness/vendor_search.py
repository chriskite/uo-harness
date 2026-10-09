"""Vendor Search prices -> the memory store (docs/NOTES.md "Vendor Search capture"; decision:
docs/PLAN.md "Vendor Search prices from the user's own browsing").

The Firefox extension in harness/vendor_search_ext/ copies every Vendor Search API response the
portal (https://portal.uooutlands.com/vendor-search) loads while the user browses it, and hands
it to this script over Firefox native messaging. The extension never sends a request of its own:
the portal logs vendor-search requests per OutlandsID and shadow-bans abusers (ANTICHEAT.md).

Each response goes verbatim into `vendor_captures` of harness/data/harness.db; the listings,
sales and price statistics in it become `vendor_prices` rows (gp per item):

  kind     endpoints                                             price_gp
  listing  VendorSearch/Search, VendorItems, MyVendorItems,      `price` (per item)
           PlayerVendorItems, WindowShopping
  sale     VendorSearch/VendorSales, MyVendorSales,              `pricePerItem`, else `price`
           PlayerVendorSales, MyPurchases, PlayerPurchases
  stat     VendorSearch/PriceOverview, PriceHistory/Search       `currentPrice`, else `averagePrice`,
                                                                 else `medianPrice`

Field names come from the portal's Angular bundle (checked 2026-10-08), not a documented API;
`reparse` rebuilds every vendor_prices row from the stored responses after a parser change.

  python harness/vendor_search.py install      register the native messaging host (HKCU)
  python harness/vendor_search.py uninstall
  python harness/vendor_search.py stats
  python harness/vendor_search.py asks WORDS… [--days 7] [-n 20]   lowest current asks by name
  python harness/vendor_search.py reparse
  vendor_search.py host                         what Firefox runs (through the generated .bat)

The store path is --db, else $UO_VENDOR_SEARCH_DB, else harness/data/harness.db. Firefox starts
the host with its own arguments, so the environment variable is the only override there.
"""

import argparse
import datetime
import json
import os
import re
import struct
import sys
import time
import traceback
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory  # noqa: E402

ROOT = memory.ROOT
DATA = os.path.join(ROOT, "harness", "data")
LOG = os.path.join(ROOT, "logs", "vendor_search.log")
HOST_NAME = "uo_vendor_prices"
EXTENSION_ID = "vendor-prices@uo-harness"    # harness/vendor_search_ext/manifest.json
HOST_BAT = os.path.join(DATA, "vendor_search_host.bat")
HOST_MANIFEST = os.path.join(DATA, "vendor_search_host.json")
REG_KEY = r"Software\Mozilla\NativeMessagingHosts" + "\\" + HOST_NAME
DB_ENV = "UO_VENDOR_SEARCH_DB"

API_RE = re.compile(r"^/(?:prod-)?api/((?:VendorSearch|PriceHistory)/[A-Za-z]+)", re.I)
KINDS = {
    "VendorSearch/Search": "listing",
    "VendorSearch/VendorItems": "listing",
    "VendorSearch/MyVendorItems": "listing",
    "VendorSearch/PlayerVendorItems": "listing",
    "VendorSearch/WindowShopping": "listing",
    "VendorSearch/VendorSales": "sale",
    "VendorSearch/MyVendorSales": "sale",
    "VendorSearch/PlayerVendorSales": "sale",
    "VendorSearch/MyPurchases": "sale",
    "VendorSearch/PlayerPurchases": "sale",
    "VendorSearch/PriceOverview": "stat",
    "PriceHistory/Search": "stat",
}
KINDS_CI = {k.lower(): k for k in KINDS}
PRICE_KEYS = {
    "listing": ("price",),
    "sale": ("pricePerItem", "price"),
    "stat": ("currentPrice", "averagePrice", "medianPrice"),
}
NAME_KEYS = ("name", "itemName", "displayName")
TIME_KEYS = ("listedTime", "saleDate", "purchaseDate", "createdAt", "date")


def db_path(arg=None):
    return arg or os.environ.get(DB_ENV) or memory.DEFAULT_DB


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


# ----------------------------------------------------------------------- parse
def endpoint_of(url):
    """'VendorSearch/Search' for a portal API url (any letter case, /prod-api/ too), else None."""
    m = API_RE.match(urlparse(url).path)
    if not m:
        return None
    return KINDS_CI.get(m.group(1).lower(), m.group(1))


def epoch(v):
    """Portal timestamp -> epoch seconds. ISO 8601 without an offset is read as UTC [INFERENCE:
    .NET serialises UTC DateTimes of unspecified kind without 'Z']."""
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def _int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v, 0)
        except ValueError:
            return None
    return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _priced(node, price_keys):
    """Every dict under node with a name and a numeric price; a priced dict isn't searched further."""
    if isinstance(node, list):
        for v in node:
            yield from _priced(v, price_keys)
    elif isinstance(node, dict):
        name = next((node[k] for k in NAME_KEYS if isinstance(node.get(k), str) and node[k].strip()), None)
        price = next((_num(node[k]) for k in price_keys if _num(node.get(k)) is not None), None)
        if name is not None and price is not None:
            yield node, name.strip(), price
            return
        for v in node.values():
            yield from _priced(v, price_keys)


def rows_of(endpoint, url, body):
    """vendor_prices rows (without capture/t) from one parsed response body."""
    kind = KINDS.get(endpoint)
    if kind is None:
        return []
    url_vendor = _int((parse_qs(urlparse(url).query).get("vendorSerial") or [None])[0])
    out = []
    for d, name, price in _priced(body, PRICE_KEYS[kind]):
        at = next((epoch(d[k]) for k in TIME_KEYS if epoch(d.get(k)) is not None), None)
        vendor = _int(d.get("vendorSerial"))
        out.append({
            "kind": kind, "name": name, "price_gp": price,
            "amount": _int(d.get("amount")), "total_gp": _num(d.get("totalPrice")),
            "item_serial": _int(d.get("itemSerial")),
            "vendor_serial": vendor if vendor is not None else url_vendor,
            "vendor_name": d.get("vendorName") if isinstance(d.get("vendorName"), str) else None,
            "shop_name": d.get("shopName") if isinstance(d.get("shopName"), str) else None,
            "x": _int(d.get("vendorLocationX")), "y": _int(d.get("vendorLocationY")),
            "at": at, "data": json.dumps(d, separators=(",", ":"), ensure_ascii=False),
        })
    return out


COLS = ("kind", "name", "price_gp", "amount", "total_gp", "item_serial", "vendor_serial",
        "vendor_name", "shop_name", "x", "y", "at", "data")


def _insert_rows(con, capture, t, rows):
    con.executemany(
        f"INSERT INTO vendor_prices(capture, t, {', '.join(COLS)}) VALUES (?, ?, {', '.join('?' * len(COLS))})",
        [(capture, t, *(r[c] for c in COLS)) for r in rows])


def store(con, msg):
    """One extension message -> a vendor_captures row and its vendor_prices rows. Returns the reply."""
    url = msg.get("url")
    if not isinstance(url, str):
        raise ValueError("message has no url")
    endpoint = endpoint_of(url)
    if endpoint is None:
        raise ValueError(f"not a Vendor Search API url: {url}")
    text = msg.get("response")
    if not isinstance(text, str):
        raise ValueError("message has no response text")
    body = json.loads(text)                       # not JSON -> refused, nothing stored
    t = _num(msg.get("t")) or time.time()
    rows = rows_of(endpoint, url, body)
    with con:
        cur = con.execute(
            "INSERT INTO vendor_captures(t, endpoint, url, method, status, page, request, response) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (t, endpoint, url, msg.get("method"), _int(msg.get("status")), msg.get("page"),
             msg.get("request"), text))
        _insert_rows(con, cur.lastrowid, t, rows)
    return {"ok": True, "capture": cur.lastrowid, "endpoint": endpoint, "rows": len(rows)}


def reparse(con):
    """Rebuild every vendor_prices row from the stored responses."""
    n_caps = n_rows = 0
    with con:
        con.execute("DELETE FROM vendor_prices")
        for cid, t, endpoint, url, text in con.execute(
                "SELECT id, t, endpoint, url, response FROM vendor_captures ORDER BY id").fetchall():
            try:
                rows = rows_of(endpoint, url, json.loads(text))
            except ValueError:
                continue
            _insert_rows(con, cid, t, rows)
            n_caps, n_rows = n_caps + 1, n_rows + len(rows)
    return {"captures": n_caps, "rows": n_rows}


# --------------------------------------------------------------- native messaging
def read_message(stream):
    """One native-messaging message (u32 length in native byte order + UTF-8 JSON); None at EOF."""
    head = stream.read(4)
    if len(head) < 4:
        return None
    n = struct.unpack("=I", head)[0]
    data = stream.read(n)
    if len(data) < n:
        return None
    return json.loads(data.decode("utf-8"))


def write_message(stream, obj):
    data = json.dumps(obj).encode("utf-8")
    stream.write(struct.pack("=I", len(data)))
    stream.write(data)
    stream.flush()


def host(db):
    """Serve the extension: one reply per message until Firefox closes stdin. The store is opened per
    message, so nothing holds it between captures (dbhandoff needs it free)."""
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            msg = read_message(stdin)
        except (ValueError, UnicodeDecodeError) as e:
            write_message(stdout, {"ok": False, "error": f"bad message: {e}"})
            return 1
        if msg is None:
            return 0
        try:
            if not isinstance(msg, dict):
                raise ValueError("message is not a JSON object")
            if msg.get("type") == "ping":
                reply = {"ok": True, "db": db}
            else:
                con = memory.connect(db)
                try:
                    reply = store(con, msg)
                finally:
                    con.close()
        except ValueError as e:  # this capture can never be stored (not JSON, not a Vendor Search url)
            log(f"refused {msg.get('url') if isinstance(msg, dict) else msg!r}: {e!r}")
            reply = {"ok": False, "retry": False, "error": f"{type(e).__name__}: {e}"}
        except Exception as e:   # the store failed (locked, disk): the extension keeps it queued
            log(f"failed {msg.get('url')}: {e!r}\n{traceback.format_exc()}")
            reply = {"ok": False, "retry": True, "error": f"{type(e).__name__}: {e}"}
        write_message(stdout, reply)


# ----------------------------------------------------------------------- install
def install():
    import winreg
    os.makedirs(DATA, exist_ok=True)
    # Firefox runs `path` directly; a .bat lets it start this script under this interpreter
    with open(HOST_BAT, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(f'@echo off\n"{sys.executable}" -u "{os.path.abspath(__file__)}" host %*\n')
    manifest = {"name": HOST_NAME, "description": "Vendor Search prices into the harness memory store",
                "path": HOST_BAT, "type": "stdio", "allowed_extensions": [EXTENSION_ID]}
    with open(HOST_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, HOST_MANIFEST)
    return {"registry": "HKCU\\" + REG_KEY, "manifest": HOST_MANIFEST, "bat": HOST_BAT, "python": sys.executable}


def uninstall():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except FileNotFoundError:
        pass
    for p in (HOST_BAT, HOST_MANIFEST):
        if os.path.exists(p):
            os.remove(p)
    return {"removed": "HKCU\\" + REG_KEY}


# ----------------------------------------------------------------------- reports
def stats(con):
    q = con.execute
    return {
        "captures": dict(q("SELECT endpoint, count(*) FROM vendor_captures GROUP BY 1")),
        "rows": dict(q("SELECT kind, count(*) FROM vendor_prices GROUP BY 1")),
        "newest": q("SELECT datetime(max(t), 'unixepoch', 'localtime') FROM vendor_captures").fetchone()[0],
    }


def asks(con, words, days=7.0, n=20):
    """Lowest listings whose name holds every word, seen in the last `days`: each item (by serial,
    else vendor + name + price) at its newest sighting."""
    where = " AND ".join("name LIKE ?" for _ in words) or "1"
    rows = con.execute(
        f"""SELECT name, price_gp, amount, vendor_name, x, y, max(t) AS seen
            FROM vendor_prices WHERE kind = 'listing' AND t >= ? AND {where}
            GROUP BY coalesce(item_serial, vendor_serial || '|' || name || '|' || price_gp)
            ORDER BY price_gp, seen DESC LIMIT ?""",
        (time.time() - days * 86400, *(f"%{w}%" for w in words), n)).fetchall()
    return [{"name": a, "gp": b, "amount": c, "vendor": d, "at": [x, y],
             "seen": datetime.datetime.fromtimestamp(s).isoformat(timespec="minutes")}
            for a, b, c, d, x, y, s in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("host", "stats", "reparse"):
        sub.add_parser(name).add_argument("--db")
    a_asks = sub.add_parser("asks")
    a_asks.add_argument("words", nargs="*")
    a_asks.add_argument("--days", type=float, default=7.0)
    a_asks.add_argument("-n", type=int, default=20)
    a_asks.add_argument("--db")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    # Firefox appends the host manifest path and the extension id to `host`
    a, _extra = ap.parse_known_args(argv)
    if a.cmd == "host":
        return host(db_path(a.db))
    if a.cmd == "install":
        print(json.dumps(install(), indent=1))
        return 0
    if a.cmd == "uninstall":
        print(json.dumps(uninstall(), indent=1))
        return 0
    con = memory.connect(db_path(a.db))
    try:
        if a.cmd == "stats":
            out = stats(con)
        elif a.cmd == "reparse":
            out = reparse(con)
        else:
            out = asks(con, a.words, a.days, a.n)
    finally:
        con.close()
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
