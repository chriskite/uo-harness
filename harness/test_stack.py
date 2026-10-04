"""Stack supervisor tests (harness/stack.py, docs/NOTES.md "Stack supervisor"), with stand-in
services: small Python listeners on free ports, in a temp folder and a temp memory store.

  1. A service starts and counts as up once its port listens.
  2. Killed hard, it is restarted after the backoff, posting one service_down juncture and a
     service_up when it's back.
  3. A stop reaches the child's Ctrl-C path (CTRL_BREAK -> KeyboardInterrupt via the bootstrap),
     so its cleanup runs (the proxy's store flush depends on this).
  4. An instance running before `up` is watched as external, not duplicated; once it's gone the
     supervisor starts its own.
  5. A wrapper that dies while its child still holds the port (laya-serve.exe under
     triage.py serve): the port owner is killed with it and the service comes back.
  6. restart-<name> and stop request files; status reads the state file.

Run from a console (CTRL_BREAK needs one): python harness/test_stack.py
"""
import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory  # noqa: E402
import stack  # noqa: E402

FAILURES = []

FAKE = r'''
import os, socket, subprocess, sys, time
port, marker, mode = int(sys.argv[1]), sys.argv[2], sys.argv[3]
if mode == "wrapper":   # like triage.py serve: a child holds the port, this process only waits
    child = subprocess.Popen([sys.executable, os.path.abspath(__file__), str(port), marker, "plain"])
    open(marker + ".child", "w").write(str(child.pid))
    child.wait()
    sys.exit(0)
s = socket.socket()
s.bind(("127.0.0.1", port))
s.listen()
try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    with open(marker, "w") as f:
        f.write("clean")
'''


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def until(st, cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        with contextlib.redirect_stdout(io.StringIO()):
            st.step()
        if cond():
            return True
        time.sleep(0.2)
    return False


def kinds(db):
    m = memory.Memory(db)
    try:
        return [(j["kind"], j["data"].get("service")) for j in m.junctures()]
    finally:
        m.con.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        stack.DIR = os.path.join(td, "stack")
        stack.BACKOFF_S = (0.5, 1.0)
        os.makedirs(stack.DIR)
        db = os.path.join(td, "harness.db")
        script = os.path.join(td, "fake_svc.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(FAKE)

        def svc(name, port, mode="plain"):
            return stack.Service(name, [script, str(port), os.path.join(td, f"{name}.marker"), mode],
                                 ports=(port,), ready_s=15, grace_s=10)

        # 1-3: start, crash, clean stop
        pa = free_port()
        a = svc("a", pa)
        st = stack.Stack([a], nat=False, memory_db=db)
        st.adopt(stack.listening())
        check(until(st, lambda: a.status == "up"), f"a starts and is up once port {pa} listens: {a.status}")
        first = a.proc.pid
        stack.kill_tree(first)
        check(until(st, lambda: a.status == "backoff"), "a hard kill is seen as an exit")
        check(until(st, lambda: a.status == "up" and a.proc.pid != first), "it is restarted after the backoff")
        check(a.restarts == 1 and kinds(db) == [("service_down", "a"), ("service_up", "a")],
              f"one service_down, then service_up: {kinds(db)}")
        with contextlib.redirect_stdout(io.StringIO()):
            st.shutdown()
        marker = os.path.join(td, "a.marker")
        check(os.path.exists(marker) and open(marker).read() == "clean",
              "the stop ran the child's KeyboardInterrupt cleanup (CTRL_BREAK mapped by the bootstrap)")
        check(pa not in stack.listening(), "its port is free after the stop")

        # 4: external instance adopted, replaced when gone
        pb = free_port()
        ext = subprocess.Popen([sys.executable, script, str(pb), os.path.join(td, "ext.marker"), "plain"])
        end = time.time() + 10
        while pb not in stack.listening() and time.time() < end:
            time.sleep(0.2)
        b = svc("b", pb)
        st = stack.Stack([b], nat=False, memory_db=db)
        with contextlib.redirect_stdout(io.StringIO()):
            st.adopt(stack.listening())
            st.step()
        check(b.status == "external" and b.proc is None and b.external_pid == ext.pid,
              f"an instance already on the port is watched, not duplicated: {b.status} {b.external_pid}")
        stack.kill_tree(ext.pid)
        check(until(st, lambda: b.status == "up" and b.proc is not None), "once it is gone, ours starts")

        # 6: restart request, state file, status, stop request
        before = b.proc.pid
        open(os.path.join(stack.DIR, "restart-b"), "w").close()
        check(until(st, lambda: b.status == "up" and b.proc.pid != before), "restart-b restarts it")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = stack.status()
        check(rc == 0 and "b       up" in out.getvalue(), f"status reads the state file: {out.getvalue()!r}")
        open(stack.stop_path(), "w").close()
        with contextlib.redirect_stdout(io.StringIO()):
            stopping = st.step() is False
            st.shutdown()
        check(stopping, "a stop request ends the loop")
        os.remove(stack.stop_path())   # run() clears it at start; these tests drive step() directly

        # 5: the wrapper dies, its child keeps the port
        pc = free_port()
        c = svc("c", pc, mode="wrapper")
        st = stack.Stack([c], nat=False, memory_db=db)
        check(until(st, lambda: c.status == "up"), "the wrapper's child brings the port up")
        with open(os.path.join(td, "c.marker.child")) as f:
            child = int(f.read())
        check(child in c.owned, f"the port owner (pid {child}) is recorded as ours: {sorted(c.owned)}")
        wrapper = c.proc.pid
        subprocess.run(["taskkill", "/PID", str(wrapper), "/F"], capture_output=True)   # the wrapper only
        check(until(st, lambda: c.status == "backoff"), "the wrapper's exit is seen")
        check(not stack.pid_alive(child), "the orphaned port owner was killed with it")
        check(until(st, lambda: c.status == "up" and c.proc.pid != wrapper), "the service comes back on the port")
        with contextlib.redirect_stdout(io.StringIO()):
            st.shutdown()

    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
