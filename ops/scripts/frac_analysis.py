#!/usr/bin/env python3
import subprocess, sys

def parse_tlv(h):
    b = bytes.fromhex(h.strip())
    out = {}
    i = 9
    while i < len(b) - 1:
        tag = b[i]
        if i+1 >= len(b): break
        length = b[i+1]
        if i+2+length > len(b): break
        payload = b[i+2:i+2+length]
        i += 2+length
        if not payload: continue
        subtype = payload[0]
        vb = payload[1:]
        if subtype == 0x01 and len(vb) == 1: out[tag] = vb[0]
        elif subtype == 0x02 and len(vb) == 2: out[tag] = int.from_bytes(vb,'little')
        elif subtype == 0x03 and len(vb) in (2,4): out[tag] = int.from_bytes(vb,'little')
    return out

since = sys.argv[1] if len(sys.argv) > 1 else "4 hours ago"
logs = subprocess.check_output(
    ["sudo","journalctl","-u","solix-monitor","--no-pager","--since",since], text=True)

readings = []
for line in logs.splitlines():
    if "INFO RAW" not in line: continue
    h = line.split()[-1].strip()
    if not h.startswith("ff"): continue
    try:
        w = parse_tlv(h).get(0xad)
        if w is not None: readings.append(w)
    except: pass

WIN = 9
avgs = [sum(readings[i-WIN:i])/WIN for i in range(WIN, len(readings)+1)]
fracs = [a % 1 for a in avgs]
buckets = [0]*10
for f in fracs:
    buckets[min(9, int(f*10))] += 1

print(f"packets={len(readings)}  windows={len(avgs)}")
print(f"avg mean={sum(avgs)/len(avgs):.4f}W  min={min(avgs):.2f}  max={max(avgs):.2f}")
print(f"\nFractional distribution (where within each watt does the 9-sample avg land):")
mx = max(buckets)
for i,c in enumerate(buckets):
    bar = "█" * (c * 50 // mx)
    print(f"  .{i}0–.{i}9  {bar:50s} {c:4d}")

# also: for runs of all-same-value, what's the dominant integer?
runs = {}
cur, cnt = readings[0], 0
for w in readings:
    if w == cur: cnt += 1
    else:
        runs.setdefault(cur, []).append(cnt)
        cur, cnt = w, 1
runs.setdefault(cur,[]).append(cnt)
print(f"\nRun-length stats (consecutive same-value sequences):")
for w in sorted(runs):
    lens = runs[w]
    print(f"  {w}W: {len(lens)} runs, mean run={sum(lens)/len(lens):.1f} packets, max={max(lens)}")
