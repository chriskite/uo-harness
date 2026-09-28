"""Offline end-to-end test: replay the captured session through the proxy.

Fake upstream server replays s2c_s2.bin; fake client replays c2s_s2.bin.
Asserts byte-exact relay both ways and correct passive decoding in the log.
"""
import asyncio
import collections
import json
import os
import subprocess
import sys

ROOT = r"C:/Users/chris/uo-harness"
PY = r"C:/Users/chris/AppData/Local/Programs/Python/Python313/python.exe"
C2S = open(f"{ROOT}/logs/session_20260928_141253.c2s.raw", "rb").read()
S2C = open(f"{ROOT}/logs/session_20260928_141253.s2c.raw", "rb").read()
PROXY_PORT = 12593
UPSTREAM_PORT = 12594
LOGDIR = f"{ROOT}/logs_test"

GOT_C2S = bytearray()  # what the fake server received


async def fake_server():
    async def handle(reader, writer):
        GOT_C2S.extend(await reader.readexactly(5))        # client preamble
        writer.write(S2C[:19])                           # server prelude
        await writer.drain()

        async def pump():
            await asyncio.sleep(0.05)
            writer.write(S2C[19:])
            await writer.drain()

        async def sink():
            while True:
                d = await reader.read(65536)
                if not d:
                    break
                GOT_C2S.extend(d)
            writer.close()

        await asyncio.gather(pump(), sink())

    server = await asyncio.start_server(handle, "127.0.0.1", UPSTREAM_PORT)
    async with server:
        await server.serve_forever()


async def main():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))

    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py",
         "--listen-port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
         "--logdir", LOGDIR],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    await asyncio.sleep(1.0)

    server_task = asyncio.create_task(fake_server())
    await asyncio.sleep(0.3)

    got_s2c = bytearray()
    reader, writer = await asyncio.open_connection("127.0.0.1", PROXY_PORT)
    writer.write(C2S[:5])
    await writer.drain()

    async def read_all():
        while True:
            data = await reader.read(65536)
            if not data:
                break
            got_s2c.extend(data)

    rt = asyncio.create_task(read_all())
    await asyncio.sleep(0.2)
    writer.write(C2S[5:1200])
    await writer.drain()
    await asyncio.sleep(0.1)
    writer.write(C2S[1200:])
    await writer.drain()
    writer.write_eof()
    try:
        await asyncio.wait_for(rt, timeout=10)
    except asyncio.TimeoutError:
        print("  [FAIL] read_all timed out")
    proxy.terminate()
    server_task.cancel()
    print("--- proxy output ---")
    print(proxy.stdout.read() if proxy.stdout else "(none)")

    ok = True
    def check(name, cond, extra=""):
        nonlocal ok
        print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
        ok = ok and bool(cond)

    check("byte-exact c2s relay", bytes(GOT_C2S) == C2S, f"({len(GOT_C2S)}/{len(C2S)} bytes)")
    check("byte-exact s2c relay", bytes(got_s2c) == S2C, f"({len(got_s2c)}/{len(S2C)} bytes)")

    logs = [f for f in os.listdir(LOGDIR) if f.endswith(".jsonl")]
    check("one jsonl session log", len(logs) == 1, str(logs))
    events = [json.loads(l) for l in open(os.path.join(LOGDIR, logs[0]), encoding="utf-8")]
    pre = [e for e in events if e.get("ev") == "s2c_prelude"]
    check("prelude parsed, key 0x07", pre and pre[0].get("session_key") == "0x07", str(pre[:1]))
    preamb = [e for e in events if e.get("ev") == "c2s_preamble"]
    check("c2s preamble logged", preamb and preamb[0].get("hex") == "ef0000000c", str(preamb[:1]))

    c2s_ids = collections.Counter(e["id"] for e in events if e.get("dir") == "c2s")
    s2c_pkts = [e for e in events if e.get("dir") == "s2c"]
    expect_min = {"0x91": 1, "0x5D": 1, "0x02": 1, "0xAD": 1, "0xFF": 30}
    for pid, n in expect_min.items():
        check(f"c2s {pid} >= {n}", c2s_ids.get(pid, 0) >= n, f"(got {c2s_ids.get(pid, 0)})")
    check("c2s no desyncs", not [e for e in events if e.get("ev") == "c2s_desync"])
    check("s2c framed >= 55 packets", len(s2c_pkts) >= 55, f"({len(s2c_pkts)})")
    check("s2c <= 1 desync (tail)", len([e for e in events if e.get("ev") == "s2c_desync"]) <= 1,
          f"({len([e for e in events if e.get('ev') == 's2c_desync'])})")
    print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
    sys.exit(0 if ok else 1)


asyncio.run(main())
