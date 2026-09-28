import re

lines = open(r"C:/Users/chris/uo-harness/strings.txt", encoding="utf-8", errors="replace").read().splitlines()
urls = set()
hosts = set()
paths = set()
uas = set()
host_re = re.compile(r"\b(?:[a-z0-9-]+\.)*(?:uooutlands\.com|classicuo\.eu|amazonaws\.com|cloudfront\.net|azure\.[a-z.]+|googleapis\.com|sentry\.io)\b", re.I)
path_re = re.compile(r"(?<![\w/])/(?:api|v\d+|account|auth|login|client|patch|portal|game|device|verify|token|identity)[\w./{}-]*", re.I)
for ln in lines:
    s = ln.split(" ", 2)[-1]
    for m in re.finditer(r"https?://[^\s\"'<>)\]]+", s):
        urls.add(m.group(0)[:160])
    for m in host_re.finditer(s):
        hosts.add(m.group(0).lower())
    for m in path_re.finditer(s):
        p = m.group(0)
        if 3 < len(p) < 100:
            paths.add(p)
    if "user-agent" in s.lower() or "UserAgent" in s:
        uas.add(s[:160])

with open(r"C:/Users/chris/uo-harness/endpoints.txt", "w") as f:
    f.write("=== URLS ===\n" + "\n".join(sorted(urls)) + "\n\n=== HOSTS ===\n" + "\n".join(sorted(hosts))
            + "\n\n=== PATHS ===\n" + "\n".join(sorted(paths)) + "\n\n=== UA-RELATED ===\n" + "\n".join(sorted(uas)))
print(f"urls={len(urls)} hosts={len(hosts)} paths={len(paths)} ua={len(uas)}")
print("\n".join(sorted(hosts)))
