import sys

SRC = r"C:/Program Files (x86)/Ultima Online Outlands/ClassicUO/ClassicUO.exe"
OUT = r"C:/Users/chris/uo-harness/strings.txt"
MIN = 5

data = open(SRC, "rb").read()
n = len(data)
out = open(OUT, "w", encoding="utf-8", errors="replace")

# ASCII runs
i = 0
count_a = 0
while i < n:
    b = data[i]
    if 32 <= b < 127:
        j = i
        while j < n and 32 <= data[j] < 127:
            j += 1
        if j - i >= MIN:
            out.write(f"{i:08x} A {data[i:j].decode('ascii')}\n")
            count_a += 1
        i = j
    else:
        i += 1

# UTF-16LE runs (char, 00) pairs
i = 0
count_u = 0
while i < n - 1:
    if 32 <= data[i] < 127 and data[i+1] == 0:
        j = i
        while j < n - 1 and 32 <= data[j] < 127 and data[j+1] == 0:
            j += 2
        if (j - i) // 2 >= MIN:
            out.write(f"{i:08x} U {data[i:j:2].decode('ascii')}\n")
            count_u += 1
        i = j
    else:
        i += 2 if i % 2 == 0 else 1

out.close()
print(f"ascii={count_a} utf16={count_u}")
