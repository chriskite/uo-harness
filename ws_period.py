"""Period analysis of the world-state record stream."""
import sys

def periods(stream, lo, hi, min_period=4, max_period=200):
    """Find periods p such that stream[i] == stream[i+p] for a long run starting near lo."""
    best = []
    for p in range(min_period, max_period+1):
        run = 0
        for i in range(lo, hi - p):
            if stream[i] == stream[i+p]:
                run += 1
        score = run / (hi - lo - p)
        if score > 0.9:
            best.append((p, round(score, 3)))
    return best

def repeat_runs(stream, min_len=12):
    """Find all maximal exact repeats (a, b, len) with b>a."""
    runs = []
    n = len(stream)
    # simple O(n^2/w) via suffix comparison on sampled anchors
    seen = {}
    step = 1
    for i in range(0, n - min_len, step):
        key = bytes(stream[i:i+8])
        if key in seen:
            for j in seen[key]:
                # extend
                L = 0
                while i+L < n and stream[j+L] == stream[i+L]:
                    L += 1
                if L >= min_len:
                    runs.append((j, i, L))
            seen.setdefault(key, []).append(i)
        else:
            seen[key] = [i]
    return runs

if __name__ == "__main__":
    stream = open(r"C:/Users/chris/uo-harness/ws_stream_164548.bin", "rb").read()
    lo, hi = 30900, len(stream)
    print("high-similarity periods in [30900, end]:", periods(stream, lo, hi))
    print("high-similarity periods in [25000, 30624]:", periods(stream, 25000, 30624))
    print("high-similarity periods in [0, 30624] (coarse):",
          [(p, s) for p, s in periods(stream, 0, 30624, 4, 400) if s > 0.95])
