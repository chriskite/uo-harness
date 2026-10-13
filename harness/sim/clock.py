"""Virtual time for running the agent runners faster than real time (docs/SIM.md "Clock").

The runners call `time.monotonic()`, `time.time()` and `time.sleep()` directly (loop_hunt, tamer,
agent_link, humanize, crawl, stationary, escape, ...). `install(clock)` points every loaded harness
module's `time` name at a `SimTime` view of one `SimClock`, so their code runs unchanged:

- `monotonic()` / `perf_counter()`: the simulated seconds since the run started (+ MONO_BASE);
- `time()`: the simulated epoch (the run's start epoch + elapsed);
- `sleep(dt)`: advances the clock by dt, letting the world run every tick it covers (`on_advance`);
- `strftime`, `localtime`, `gmtime`: on the simulated epoch (log lines show game time); every other
  `time` attribute is the real module's.

Nothing ever waits on the wall clock, so a run is as fast as the code. A loop that polls without
sleeping still moves time: SimLink charges each state read READ_COST_S.
"""
import sys
import time as _real

MONO_BASE = 10_000.0     # monotonic() starts here (like a machine up for a while; never 0)


class SimClock:
    def __init__(self, epoch: float | None = None, on_advance=None):
        self.epoch0 = _real.time() if epoch is None else float(epoch)
        self.elapsed = 0.0
        self.on_advance = on_advance     # f(now_elapsed): the world runs up to this time
        self.slept = 0.0                 # total of sleep() calls (the runner's own waiting)

    def monotonic(self) -> float:
        return MONO_BASE + self.elapsed

    def time(self) -> float:
        return self.epoch0 + self.elapsed

    def advance(self, dt: float):
        if dt <= 0:
            return
        self.elapsed += dt
        if self.on_advance is not None:
            self.on_advance(self.elapsed)

    def sleep(self, dt: float):
        dt = max(0.0, float(dt))
        self.slept += dt
        self.advance(dt)


class SimTime:
    """A stand-in for the `time` module bound to one SimClock."""

    def __init__(self, clock: SimClock):
        self._c = clock

    def monotonic(self):
        return self._c.monotonic()

    perf_counter = monotonic

    def time(self):
        return self._c.time()

    def sleep(self, dt):
        self._c.sleep(dt)

    def localtime(self, t=None):
        return _real.localtime(self._c.time() if t is None else t)

    def gmtime(self, t=None):
        return _real.gmtime(self._c.time() if t is None else t)

    def strftime(self, fmt, t=None):
        return _real.strftime(fmt, self.localtime() if t is None else t)

    def __getattr__(self, name):
        return getattr(_real, name)


def install(clock: SimClock, prefix_dirs=None) -> list:
    """Point `time` at the clock in every loaded module that imported the real `time` and lives in
    the harness (or under one of `prefix_dirs`). Returns the modules patched (uninstall() restores).
    Call it after importing the runner's modules."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [os.path.normcase(os.path.abspath(p)) for p in (prefix_dirs or [here])]
    shim = SimTime(clock)
    patched = []
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f or getattr(mod, "time", None) is not _real:
            continue
        if not any(os.path.normcase(os.path.abspath(f)).startswith(r) for r in roots):
            continue
        if mod.__name__ == __name__:
            continue
        mod.time = shim
        patched.append(mod)
    return patched


def uninstall(modules):
    for mod in modules:
        mod.time = _real
