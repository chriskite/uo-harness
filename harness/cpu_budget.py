"""CPU budget for tests and simulator batches: leave RESERVED logical CPUs to the game (user 2026-10-17: "We need
to reduce the number of cores used when testing by 2, it's lagging the game pretty badly").

`limit()` sets the calling process's CPU affinity to every logical CPU but the first RESERVED; child processes
(run_tests' test files, process pools, subprocess scenarios) inherit it on Windows and Linux, so the whole tree
stays off those cores whatever its pool sizes. `workers()` is the pool size to match: os.cpu_count() minus
RESERVED. Call `limit()` at the top of a test or batch entry point, never from runner code that plays live.
"""
import os

RESERVED = 2


def workers() -> int:
    """Logical CPUs a test run may use."""
    return max(1, (os.cpu_count() or 1) - RESERVED)


def limit() -> bool:
    """Keep this process (and its children) off the first RESERVED logical CPUs. True when the mask was set."""
    n = os.cpu_count() or 1
    if n <= RESERVED:
        return False
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, set(range(RESERVED, n)))
        return True
    if os.name == "nt" and n <= 64:          # one processor group: SetProcessAffinityMask covers it
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
        mask = ((1 << n) - 1) & ~((1 << RESERVED) - 1)
        return bool(kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(), mask))
    return False
