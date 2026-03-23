#!/usr/bin/env python3
"""
analyze_ble.py — Solix BLE packet analysis

Usage: python3 analyze_ble.py ["N hours ago"]

Sections:
  1. Per-bucket stats (a3 vs total_output_w)
  2. Model comparison (linear, quadratic, cubic, exponential, piecewise, step-means)
  3. Inversion accuracy
  4. Leading-indicator test: does a3 predict W transitions before they happen?
     For every run of W packets of length >= MIN_RUN, compare:
       - mid-run a3   (packets far from any edge)
       - pre-up a3    (last N packets before W increases)
       - pre-down a3  (last N packets before W decreases)
     If a3 leads W, pre-up a3 < mid a3 < pre-down a3.
"""
import subprocess, sys, math

MIN_RUN = 4        # minimum run length to analyse
PRE_N   = 3        # how many packets before a transition to call "pre-transition"

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
        if subtype == 0x01 and len(vb) == 1:
            out[tag] = vb[0]
        elif subtype == 0x02 and len(vb) == 2:
            out[tag] = int.from_bytes(vb, 'little')
        elif subtype == 0x03 and len(vb) in (2, 4):
            out[tag] = int.from_bytes(vb, 'little')
    return out

since = sys.argv[1] if len(sys.argv) > 1 else "1 hour ago"
logs = subprocess.check_output(
    ["sudo", "journalctl", "-u", "solix-monitor", "--no-pager", "--since", since],
    text=True
)

# Each entry: (timestamp_str, watts, a3)
packets = []
for line in logs.splitlines():
    if "INFO RAW" not in line:
        continue
    # timestamp is first field: "Mar 23 02:26:01"
    ts_str = " ".join(line.split()[:3])
    idx = line.rfind(" ")
    h = line[idx+1:].strip()
    if not h.startswith("ff"):
        continue
    try:
        f = parse_tlv(h)
        w = f.get(0xad)
        a = f.get(0xa3)
        if w is not None and a is not None:
            packets.append((ts_str, w, a))
    except Exception as e:
        print(f"parse error: {e}", file=sys.stderr)

n = len(packets)
print(f"n={n} packets\n")
if n < 2:
    sys.exit(1)

watts_v = [float(p[1]) for p in packets]
a3_v    = [float(p[2]) for p in packets]

# ── helpers ──────────────────────────────────────────────────────────────────

def mean(v):
    return sum(v)/len(v) if v else float('nan')

def std(v):
    if len(v) < 2: return float('nan')
    m = mean(v)
    return (sum((x-m)**2 for x in v)/len(v))**0.5

def ss_tot(y):
    ym = mean(y)
    return sum((v-ym)**2 for v in y)

def r2(y, pred):
    return 1 - sum((y[i]-pred[i])**2 for i in range(len(y))) / ss_tot(y)

def polyfit(x, y, deg):
    d = deg + 1
    A = [[sum(x[k]**(i+j) for k in range(len(x))) for j in range(d)] for i in range(d)]
    b = [sum(y[k]*x[k]**i for k in range(len(x))) for i in range(d)]
    m = [A[i]+[b[i]] for i in range(d)]
    for col in range(d):
        piv = max(range(col,d), key=lambda r: abs(m[r][col]))
        m[col],m[piv] = m[piv],m[col]
        for row in range(d):
            if row!=col and m[col][col]:
                f = m[row][col]/m[col][col]
                m[row] = [m[row][j]-f*m[col][j] for j in range(d+1)]
    return [m[i][d]/m[i][i] for i in range(d)]

def polyeval(c, x):
    return sum(c[i]*x**i for i in range(len(c)))

def expfit(x, y):
    logy = [math.log(v) for v in y if v > 0]
    xf   = [x[i] for i in range(len(x)) if y[i] > 0]
    c = polyfit(xf, logy, 1)
    A, B = math.exp(c[0]), c[1]
    return A, B, [A * math.exp(B*xi) for xi in x]

# ── per-bucket stats ──────────────────────────────────────────────────────────

groups = {}
for _,w,a in packets: groups.setdefault(w,[]).append(a)
means = {w: mean(v) for w,v in groups.items()}

print(f"{'W':>4}  {'n':>4}  {'a3_mean':>8}  {'a3_std':>7}  {'a3_range':>12}")
for w in sorted(groups):
    a3s = groups[w]
    mn  = means[w]
    sd  = std(a3s)
    print(f"{w:>4}  {len(a3s):>4}  {mn:>8.1f}  {sd:>7.1f}  [{min(a3s):>4},{max(a3s):>4}]")

# ── models ────────────────────────────────────────────────────────────────────

print()
c1 = polyfit(watts_v, a3_v, 1)
p1 = [polyeval(c1,w) for w in watts_v]
print(f"Linear:     a3 = {c1[1]:.3f}·w + {c1[0]:.2f}  R²={r2(a3_v,p1):.4f}")

c2 = polyfit(watts_v, a3_v, 2)
p2 = [polyeval(c2,w) for w in watts_v]
print(f"Quadratic:  a3 = {c2[2]:.4f}·w² + {c2[1]:.3f}·w + {c2[0]:.2f}  R²={r2(a3_v,p2):.4f}")

c3 = polyfit(watts_v, a3_v, 3)
p3 = [polyeval(c3,w) for w in watts_v]
print(f"Cubic:      R²={r2(a3_v,p3):.4f}")

A, B, pe = expfit(watts_v, a3_v)
print(f"Exponential: a3 = {A:.2f}·exp({B:.4f}·w)  R²={r2(a3_v,pe):.4f}")

lo = [(w,a) for w,a in zip(watts_v,a3_v) if w<=8]
hi = [(w,a) for w,a in zip(watts_v,a3_v) if w>8]
clo = polyfit([x[0] for x in lo],[x[1] for x in lo],1)
chi = polyfit([x[0] for x in hi],[x[1] for x in hi],1)
pp  = [polyeval(clo,w) if w<=8 else polyeval(chi,w) for w in watts_v]
print(f"Piecewise:  ≤8W: {clo[1]:.3f}·w+{clo[0]:.2f}  >8W: {chi[1]:.3f}·w+{chi[0]:.2f}  R²={r2(a3_v,pp):.4f}")

pm = [means.get(w, polyeval(c1,w)) for w in watts_v]
print(f"Step means: R²={r2(a3_v,pm):.4f}  (upper bound)")

# ── inversion accuracy ────────────────────────────────────────────────────────

print("\nInversion accuracy (nearest-mean lookup):")
exact=off1=0
for _,w,a in packets:
    best_w = min(means, key=lambda x: abs(means[x]-a))
    err = abs(best_w - w)
    if err==0: exact+=1
    elif err<=1: off1+=1
print(f"  Exact:     {exact}/{n} ({100*exact/n:.1f}%)")
print(f"  ±1W:       {exact+off1}/{n} ({100*(exact+off1)/n:.1f}%)")
dominant_w = max(groups, key=lambda w: len(groups[w]))
print(f"  Baseline (always predict {dominant_w}W): {len(groups[dominant_w])}/{n} ({100*len(groups[dominant_w])/n:.1f}%)")

# ── leading-indicator test ────────────────────────────────────────────────────
#
# Build runs of consecutive same-W packets.
# For each run of length >= MIN_RUN, label each packet:
#   "mid"      if it's > PRE_N from both ends of the run
#   "pre-up"   if it's in the last PRE_N of a run that's followed by a higher W
#   "pre-down" if it's in the last PRE_N of a run that's followed by a lower W
#
# NOTE: run lengths are in packets, not seconds. Each packet represents a BLE
# notification which arrives every ~3.5s on average, but the interval is not
# guaranteed. A run of 2 packets could be 7s or 35s. This analysis describes
# the *packet-sequence* structure, not wall-clock duty cycle.

print(f"\n── Leading-indicator test (MIN_RUN={MIN_RUN}, PRE_N={PRE_N}) ──")
print(f"   Runs are measured in packets (not seconds; ~3.5s avg interval).")
print(f"   'pre-up' = last {PRE_N} packets before W increases")
print(f"   'pre-down' = last {PRE_N} packets before W decreases")
print(f"   'mid' = packets > {PRE_N} from either end of the run\n")

# Build runs
runs = []  # [(w, [a3, ...], next_w or None)]
i = 0
while i < len(packets):
    w = packets[i][1]
    j = i
    while j < len(packets) and packets[j][1] == w:
        j += 1
    run_a3 = [packets[k][2] for k in range(i, j)]
    next_w = packets[j][1] if j < len(packets) else None
    runs.append((w, run_a3, next_w))
    i = j

# Collect labelled samples per W level
# structure: {w: {"mid": [], "pre-up": [], "pre-down": []}}
labelled = {}

for w, run_a3, next_w in runs:
    if w not in labelled:
        labelled[w] = {"mid": [], "pre-up": [], "pre-down": []}
    L = len(run_a3)
    if L < MIN_RUN:
        continue
    # mid packets: those > PRE_N from the end (we only care about the trailing edge)
    mid_end = max(0, L - PRE_N)
    for k in range(mid_end):
        labelled[w]["mid"].append(run_a3[k])
    # trailing PRE_N packets
    trailing = run_a3[max(0, L-PRE_N):]
    if next_w is None:
        pass  # no transition, skip
    elif next_w > w:
        labelled[w]["pre-up"].extend(trailing)
    elif next_w < w:
        labelled[w]["pre-down"].extend(trailing)

# Report
print(f"{'W':>4}  {'category':>10}  {'n':>4}  {'mean':>7}  {'std':>6}")
for w in sorted(labelled):
    d = labelled[w]
    for cat in ("mid", "pre-up", "pre-down"):
        vals = d[cat]
        if not vals:
            print(f"{w:>4}  {cat:>10}  {'—':>4}")
        else:
            print(f"{w:>4}  {cat:>10}  {len(vals):>4}  {mean(vals):>7.1f}  {std(vals):>6.1f}")
    # Summary: does a3 lead?
    m   = mean(d["mid"])
    pu  = mean(d["pre-up"])
    pd  = mean(d["pre-down"])
    if d["mid"] and d["pre-up"] and d["pre-down"]:
        leads = (pu < m < pd)
        print(f"       -> pre-up={pu:.1f} mid={m:.1f} pre-down={pd:.1f}  "
              f"{'LEADS (pre-up < mid < pre-down)' if leads else 'NO LEAD'}")
    print()
