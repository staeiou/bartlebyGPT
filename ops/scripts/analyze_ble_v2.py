#!/usr/bin/env python3
"""
analyze_ble_v2.py — Solix BLE packet analysis, time-aware and quantization-aware

What this script improves over the earlier version:
- Uses real timestamps from journalctl (ISO output), not packet index
- Computes time-weighted mean power / energy
- Treats packet-count analyses and wall-clock analyses as different things
- Evaluates whether a3 adds predictive value beyond integer watts
- Uses run-level transition summaries instead of packet-level t-tests
- Adds a simple block bootstrap for transition effects
- Keeps dependencies to the Python standard library only

Usage examples:
  python3 analyze_ble_v2.py --since "6 hours ago"
  python3 analyze_ble_v2.py --since "2026-03-21 00:00:00" --json
  python3 analyze_ble_v2.py --stdin < sample.log

Notes:
- This assumes logs contain lines with "INFO RAW" and a trailing hex payload.
- It requests journalctl in short-iso format so timestamps are parseable.
- Energy is estimated by right-hold integration:
      E ~= sum_i W_i * dt_i
  over intervals between successive packets.
- Run analyses are done both in packets and in elapsed seconds.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import random
import re
import statistics
import subprocess
import sys
from typing import Dict, List, Optional, Tuple, Iterable

# ----------------------------- configuration ---------------------------------

DEFAULT_SINCE = "1 hour ago"
DEFAULT_MIN_RUN_PACKETS = 4
DEFAULT_PRE_PACKETS = 3
DEFAULT_BLOCK_SIZE = 8
DEFAULT_BOOTSTRAP_REPS = 2000

RAW_RE = re.compile(r"^(?P<ts>\S+\s+\S+)\s+.*?\bINFO RAW\b.*?\s(?P<hex>[0-9a-fA-F]+)\s*$")

# ------------------------------ TLV parsing ----------------------------------

def parse_tlv(hex_payload: str) -> Dict[int, int]:
    """
    Parse Solix TLV-ish payload after the 9-byte prefix used in the original script.
    Keeps the original subtype handling, but is slightly stricter.
    """
    b = bytes.fromhex(hex_payload.strip())
    out: Dict[int, int] = {}

    if len(b) < 10:
        return out

    i = 9
    while i + 1 < len(b):
        tag = b[i]
        length = b[i + 1]
        start = i + 2
        end = start + length
        if end > len(b):
            break

        payload = b[start:end]
        i = end

        if not payload:
            continue

        subtype = payload[0]
        vb = payload[1:]

        if subtype == 0x01 and len(vb) == 1:
            out[tag] = vb[0]
        elif subtype == 0x02 and len(vb) == 2:
            out[tag] = int.from_bytes(vb, "little")
        elif subtype == 0x03 and len(vb) in (2, 4):
            out[tag] = int.from_bytes(vb, "little")
        else:
            # Unknown or malformed subtype/payload length: ignore.
            pass

    return out

# --------------------------- data structures ---------------------------------

class Packet:
    __slots__ = ("ts", "w", "a3", "fields", "hex_payload")

    def __init__(self, ts: dt.datetime, w: int, a3: Optional[int], fields: Dict[int, int], hex_payload: str):
        self.ts = ts
        self.w = w
        self.a3 = a3
        self.fields = fields
        self.hex_payload = hex_payload

class Run:
    __slots__ = ("w", "packets", "start_ts", "end_ts", "next_w")

    def __init__(self, w: int, packets: List[Packet], next_w: Optional[int]):
        self.w = w
        self.packets = packets
        self.start_ts = packets[0].ts
        self.end_ts = packets[-1].ts
        self.next_w = next_w

    @property
    def n_packets(self) -> int:
        return len(self.packets)

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_ts - self.start_ts).total_seconds())

# ------------------------------ utilities ------------------------------------

def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")

def pstdev(xs: Iterable[float]) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))

def percentile(xs: List[float], q: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    pos = q * (len(ys) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    w = pos - lo
    return ys[lo] * (1 - w) + ys[hi] * w

def r2(y: List[float], pred: List[float]) -> float:
    if len(y) != len(pred) or not y:
        return float("nan")
    ym = mean(y)
    sst = sum((v - ym) ** 2 for v in y)
    if sst == 0:
        return 1.0
    sse = sum((y[i] - pred[i]) ** 2 for i in range(len(y)))
    return 1.0 - sse / sst

def solve_linear_system(A: List[List[float]], b: List[float]) -> List[float]:
    """
    Gaussian elimination with partial pivoting.
    """
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            raise ValueError("Singular matrix")
        if pivot != col:
            M[col], M[pivot] = M[pivot], M[col]

        piv = M[col][col]
        for j in range(col, n + 1):
            M[col][j] /= piv

        for row in range(n):
            if row == col:
                continue
            factor = M[row][col]
            if factor == 0:
                continue
            for j in range(col, n + 1):
                M[row][j] -= factor * M[col][j]

    return [M[i][n] for i in range(n)]

def polyfit(x: List[float], y: List[float], deg: int) -> List[float]:
    d = deg + 1
    A = [[sum((x[k] ** (i + j)) for k in range(len(x))) for j in range(d)] for i in range(d)]
    b = [sum(y[k] * (x[k] ** i) for k in range(len(x))) for i in range(d)]
    return solve_linear_system(A, b)

def polyeval(c: List[float], x: float) -> float:
    return sum(c[i] * (x ** i) for i in range(len(c)))

def expfit(x: List[float], y: List[float]) -> Tuple[float, float, List[float]]:
    xf, logy = [], []
    for xi, yi in zip(x, y):
        if yi > 0:
            xf.append(xi)
            logy.append(math.log(yi))
    if len(xf) < 2:
        return float("nan"), float("nan"), [float("nan")] * len(x)
    c = polyfit(xf, logy, 1)
    A, B = math.exp(c[0]), c[1]
    pred = [A * math.exp(B * xi) for xi in x]
    return A, B, pred

def parse_iso_ts(ts_str: str) -> dt.datetime:
    """
    Parse journalctl short-iso timestamp, e.g.:
      2026-03-22T14:31:20-0700
      2026-03-22T14:31:20-07:00
    """
    ts_str = ts_str.strip()
    try:
        return dt.datetime.fromisoformat(ts_str)
    except ValueError:
        # Fallback for timezone without colon, e.g. -0700
        if len(ts_str) >= 5 and (ts_str[-5] in "+-") and ts_str[-3] != ":":
            ts_str = ts_str[:-5] + ts_str[-5:-2] + ":" + ts_str[-2:]
            return dt.datetime.fromisoformat(ts_str)
        raise

# ----------------------------- log ingestion ---------------------------------

def read_log_text_from_journalctl(since: str, unit: str) -> str:
    cmd = [
        "sudo", "journalctl",
        "-u", unit,
        "--no-pager",
        "--output=short-iso",
        "--since", since,
    ]
    return subprocess.check_output(cmd, text=True)

def parse_packets_from_text(text: str, require_a3: bool = False) -> List[Packet]:
    packets: List[Packet] = []

    for line in text.splitlines():
        if "INFO RAW" not in line:
            continue

        try:
            msg = line.split(": ", 1)[1]
            ts_raw, _, hex_payload = msg.partition(" INFO RAW ")
            hex_payload = hex_payload.strip().lower()

            if not hex_payload.startswith("ff"):
                continue

            ts = dt.datetime.strptime(ts_raw.strip(), "%Y-%m-%d %H:%M:%S,%f")
            fields = parse_tlv(hex_payload)
        except Exception:
            continue

        w = fields.get(0xAD)
        a3 = fields.get(0xA3)

        if w is None:
            continue
        if require_a3 and a3 is None:
            continue

        packets.append(Packet(ts=ts, w=w, a3=a3, fields=fields, hex_payload=hex_payload))

    packets.sort(key=lambda p: p.ts)
    return packets
# ----------------------------- core analyses ---------------------------------

def summarize_intervals(packets: List[Packet]) -> Dict[str, float]:
    if len(packets) < 2:
        return {
            "n_packets": len(packets),
            "span_s": 0.0,
            "mean_dt_s": float("nan"),
            "median_dt_s": float("nan"),
            "min_dt_s": float("nan"),
            "max_dt_s": float("nan"),
        }

    dts = [(packets[i + 1].ts - packets[i].ts).total_seconds() for i in range(len(packets) - 1)]
    return {
        "n_packets": len(packets),
        "span_s": (packets[-1].ts - packets[0].ts).total_seconds(),
        "mean_dt_s": mean(dts),
        "median_dt_s": statistics.median(dts),
        "min_dt_s": min(dts),
        "max_dt_s": max(dts),
    }

def time_weighted_power_and_energy(packets: List[Packet]) -> Dict[str, float]:
    """
    Right-hold estimate over observed intervals:
      P_i applies from t_i to t_{i+1}
    """
    if len(packets) < 2:
        return {
            "time_weighted_mean_w": float("nan"),
            "energy_ws": 0.0,
            "energy_wh": 0.0,
        }

    energy_ws = 0.0
    total_s = 0.0

    for i in range(len(packets) - 1):
        dt_s = (packets[i + 1].ts - packets[i].ts).total_seconds()
        if dt_s <= 0:
            continue
        energy_ws += packets[i].w * dt_s
        total_s += dt_s

    return {
        "time_weighted_mean_w": (energy_ws / total_s) if total_s > 0 else float("nan"),
        "energy_ws": energy_ws,
        "energy_wh": energy_ws / 3600.0,
    }

def packet_weighted_mean_w(packets: List[Packet]) -> float:
    return mean([p.w for p in packets]) if packets else float("nan")

def build_runs(packets: List[Packet]) -> List[Run]:
    if not packets:
        return []

    runs: List[Run] = []
    start = 0

    while start < len(packets):
        w = packets[start].w
        end = start + 1
        while end < len(packets) and packets[end].w == w:
            end += 1
        next_w = packets[end].w if end < len(packets) else None
        runs.append(Run(w=w, packets=packets[start:end], next_w=next_w))
        start = end

    return runs

def grouped_a3_stats(packets: List[Packet]) -> Dict[int, Dict[str, float]]:
    groups: Dict[int, List[int]] = collections.defaultdict(list)
    for p in packets:
        if p.a3 is not None:
            groups[p.w].append(p.a3)

    out: Dict[int, Dict[str, float]] = {}
    for w in sorted(groups):
        vals = groups[w]
        out[w] = {
            "n": len(vals),
            "a3_mean": mean(vals),
            "a3_std": pstdev(vals),
            "a3_min": min(vals),
            "a3_max": max(vals),
        }
    return out

def fit_models_w_to_a3(packets: List[Packet]) -> Dict[str, Dict[str, object]]:
    xs = [float(p.w) for p in packets if p.a3 is not None]
    ys = [float(p.a3) for p in packets if p.a3 is not None]
    if len(xs) < 3:
        return {}

    models: Dict[str, Dict[str, object]] = {}

    c1 = polyfit(xs, ys, 1)
    p1 = [polyeval(c1, x) for x in xs]
    models["linear"] = {"coef": c1, "r2": r2(ys, p1)}

    c2 = polyfit(xs, ys, 2)
    p2 = [polyeval(c2, x) for x in xs]
    models["quadratic"] = {"coef": c2, "r2": r2(ys, p2)}

    c3 = polyfit(xs, ys, 3)
    p3 = [polyeval(c3, x) for x in xs]
    models["cubic"] = {"coef": c3, "r2": r2(ys, p3)}

    A, B, pe = expfit(xs, ys)
    models["exponential"] = {"coef": [A, B], "r2": r2(ys, pe)}

    step_means = {}
    for p in packets:
        if p.a3 is not None:
            step_means.setdefault(p.w, []).append(p.a3)
    step_means = {w: mean(vals) for w, vals in step_means.items()}
    pm = [step_means[int(x)] for x in xs]
    models["step_means"] = {
        "coef": step_means,
        "r2": r2(ys, pm),
        "note": "In-sample ceiling for predictors that only use the integer watt state",
    }

    return models

def inversion_accuracy_from_a3(packets: List[Packet]) -> Dict[str, object]:
    groups: Dict[int, List[int]] = collections.defaultdict(list)
    for p in packets:
        if p.a3 is not None:
            groups[p.w].append(p.a3)

    if not groups:
        return {}

    means_by_w = {w: mean(vals) for w, vals in groups.items()}
    eligible = [p for p in packets if p.a3 is not None]

    exact = 0
    off1 = 0

    for p in eligible:
        pred_w = min(means_by_w, key=lambda w: abs(means_by_w[w] - p.a3))
        err = abs(pred_w - p.w)
        if err == 0:
            exact += 1
        if err <= 1:
            off1 += 1

    dominant_w = max(groups, key=lambda w: len(groups[w]))
    baseline = sum(1 for p in eligible if p.w == dominant_w)

    return {
        "n": len(eligible),
        "exact_n": exact,
        "exact_pct": 100.0 * exact / len(eligible),
        "pm1_n": off1,
        "pm1_pct": 100.0 * off1 / len(eligible),
        "baseline_w": dominant_w,
        "baseline_n": baseline,
        "baseline_pct": 100.0 * baseline / len(eligible),
    }

def transition_windows(
    runs: List[Run],
    min_run_packets: int,
    pre_packets: int,
) -> Dict[int, Dict[str, List[float]]]:
    """
    Run-level labeling, but preserving packet values for descriptive summaries.
    Categories:
      mid       = packets before the trailing pre-transition block
      pre_up    = last pre_packets before an upward transition
      pre_down  = last pre_packets before a downward transition
    """
    out: Dict[int, Dict[str, List[float]]] = collections.defaultdict(lambda: {
        "mid": [],
        "pre_up": [],
        "pre_down": [],
    })

    for run in runs:
        if run.n_packets < min_run_packets:
            continue

        vals = [p.a3 for p in run.packets if p.a3 is not None]
        if len(vals) < min_run_packets:
            continue

        w = run.w
        split = max(0, len(vals) - pre_packets)
        out[w]["mid"].extend(vals[:split])

        trailing = vals[split:]
        if run.next_w is None:
            continue
        if run.next_w > w:
            out[w]["pre_up"].extend(trailing)
        elif run.next_w < w:
            out[w]["pre_down"].extend(trailing)

    return out

def run_level_transition_effects(
    runs: List[Run],
    min_run_packets: int,
    pre_packets: int,
) -> Dict[int, Dict[str, List[float]]]:
    """
    One summary number per run to avoid pretending packets are independent.
    For each eligible run, compute:
      mid_mean
      trailing_mean
    then store trailing_mean - mid_mean separately for upward and downward transitions.
    """
    out: Dict[int, Dict[str, List[float]]] = collections.defaultdict(lambda: {
        "up_delta": [],
        "down_delta": [],
        "mid_mean": [],
        "pre_up_mean": [],
        "pre_down_mean": [],
    })

    for run in runs:
        vals = [p.a3 for p in run.packets if p.a3 is not None]
        if len(vals) < min_run_packets:
            continue

        split = max(0, len(vals) - pre_packets)
        mid = vals[:split]
        trailing = vals[split:]

        if not mid or not trailing or run.next_w is None:
            continue

        mid_m = mean(mid)
        tr_m = mean(trailing)
        rec = out[run.w]

        if run.next_w > run.w:
            rec["up_delta"].append(tr_m - mid_m)
            rec["mid_mean"].append(mid_m)
            rec["pre_up_mean"].append(tr_m)
        elif run.next_w < run.w:
            rec["down_delta"].append(tr_m - mid_m)
            rec["mid_mean"].append(mid_m)
            rec["pre_down_mean"].append(tr_m)

    return out

def block_bootstrap_mean(xs: List[float], block_size: int, reps: int, seed: int = 0) -> Dict[str, float]:
    """
    Simple moving-block bootstrap for a mean.
    This is mainly useful when xs is a time-ordered sequence.
    Included here as a lightweight robustness check.
    """
    if not xs:
        return {"mean": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan")}
    if len(xs) == 1:
        return {"mean": xs[0], "ci_lo": xs[0], "ci_hi": xs[0]}

    rng = random.Random(seed)
    n = len(xs)
    b = max(1, min(block_size, n))
    means = []

    starts = list(range(0, n - b + 1))
    for _ in range(reps):
        sample = []
        while len(sample) < n:
            s = rng.choice(starts)
            sample.extend(xs[s:s + b])
        sample = sample[:n]
        means.append(mean(sample))

    return {
        "mean": mean(xs),
        "ci_lo": percentile(means, 0.025),
        "ci_hi": percentile(means, 0.975),
    }

# ------------------------------- reporting -----------------------------------

def print_header(title: str) -> None:
    print("\n" + title)
    print("-" * len(title))

def report_basic(packets: List[Packet]) -> None:
    print_header("Basic summary")
    ints = summarize_intervals(packets)
    tw = time_weighted_power_and_energy(packets)

    print(f"Packets:                 {ints['n_packets']}")
    print(f"Observation span:        {ints['span_s']:.1f} s")
    print(f"Mean inter-packet dt:    {ints['mean_dt_s']:.3f} s")
    print(f"Median inter-packet dt:  {ints['median_dt_s']:.3f} s")
    print(f"Min / max dt:            {ints['min_dt_s']:.3f} / {ints['max_dt_s']:.3f} s")
    print(f"Packet-weighted mean W:  {packet_weighted_mean_w(packets):.4f}")
    print(f"Time-weighted mean W:    {tw['time_weighted_mean_w']:.4f}")
    print(f"Estimated energy:        {tw['energy_wh']:.6f} Wh")

def report_grouped_a3(packets: List[Packet]) -> None:
    stats = grouped_a3_stats(packets)
    if not stats:
        print_header("Per-watt a3 stats")
        print("No packets with both watt and a3 fields.")
        return

    print_header("Per-watt a3 stats")
    print(f"{'W':>5} {'n':>6} {'a3_mean':>10} {'a3_std':>10} {'a3_range':>16}")
    for w in sorted(stats):
        s = stats[w]
        print(f"{w:>5} {s['n']:>6} {s['a3_mean']:>10.2f} {s['a3_std']:>10.2f} "
              f"[{int(s['a3_min'])},{int(s['a3_max'])}]".rjust(16))

def report_models(packets: List[Packet]) -> None:
    models = fit_models_w_to_a3(packets)
    if not models:
        print_header("Models of a3 from integer watts")
        print("Not enough packets with a3 to fit models.")
        return

    print_header("Models of a3 from integer watts")
    for name in ("linear", "quadratic", "cubic", "exponential", "step_means"):
        m = models[name]
        if name == "linear":
            c = m["coef"]
            print(f"Linear:       a3 = {c[1]:.6f}*w + {c[0]:.6f}    R²={m['r2']:.6f}")
        elif name == "quadratic":
            c = m["coef"]
            print(f"Quadratic:    a3 = {c[2]:.6f}*w² + {c[1]:.6f}*w + {c[0]:.6f}    R²={m['r2']:.6f}")
        elif name == "cubic":
            print(f"Cubic:        R²={m['r2']:.6f}")
        elif name == "exponential":
            c = m["coef"]
            print(f"Exponential:  a3 = {c[0]:.6f}*exp({c[1]:.6f}*w)    R²={m['r2']:.6f}")
        elif name == "step_means":
            print(f"Step means:   R²={m['r2']:.6f}")
            print(f"              note: {m['note']}")

def report_inversion(packets: List[Packet]) -> None:
    inv = inversion_accuracy_from_a3(packets)
    print_header("Inversion accuracy from a3")
    if not inv:
        print("No packets with a3.")
        return
    print(f"n eligible:               {inv['n']}")
    print(f"Exact:                    {inv['exact_n']} ({inv['exact_pct']:.2f}%)")
    print(f"Within ±1 W:              {inv['pm1_n']} ({inv['pm1_pct']:.2f}%)")
    print(f"Baseline always predict:  {inv['baseline_w']} W")
    print(f"Baseline accuracy:        {inv['baseline_n']} ({inv['baseline_pct']:.2f}%)")

def report_runs(runs: List[Run]) -> None:
    print_header("Run structure")
    if not runs:
        print("No runs.")
        return

    n_packets = [r.n_packets for r in runs]
    durations = [r.duration_s for r in runs]

    print(f"Runs:                     {len(runs)}")
    print(f"Mean packets per run:     {mean(n_packets):.3f}")
    print(f"Median packets per run:   {statistics.median(n_packets):.3f}")
    print(f"Mean run duration:        {mean(durations):.3f} s")
    print(f"Median run duration:      {statistics.median(durations):.3f} s")

def report_transition_descriptives(
    runs: List[Run],
    min_run_packets: int,
    pre_packets: int,
    block_size: int,
    bootstrap_reps: int,
) -> None:
    print_header("Transition descriptives")
    labelled = transition_windows(runs, min_run_packets=min_run_packets, pre_packets=pre_packets)
    effects = run_level_transition_effects(runs, min_run_packets=min_run_packets, pre_packets=pre_packets)

    if not labelled:
        print("No eligible transitions.")
        return

    print(f"(min_run_packets={min_run_packets}, pre_packets={pre_packets})")
    print()

    print(f"{'W':>5} {'category':>10} {'n':>6} {'mean':>10} {'std':>10}")
    for w in sorted(labelled):
        for cat in ("mid", "pre_up", "pre_down"):
            vals = labelled[w][cat]
            if vals:
                print(f"{w:>5} {cat:>10} {len(vals):>6} {mean(vals):>10.3f} {pstdev(vals):>10.3f}")
            else:
                print(f"{w:>5} {cat:>10} {'—':>6}")

        eff = effects.get(w, {})
        up = eff.get("up_delta", [])
        down = eff.get("down_delta", [])

        if up:
            bs = block_bootstrap_mean(up, block_size=block_size, reps=bootstrap_reps, seed=1000 + w)
            print(f"      up_delta   mean={bs['mean']:.3f}   95% CI [{bs['ci_lo']:.3f}, {bs['ci_hi']:.3f}]")
        if down:
            bs = block_bootstrap_mean(down, block_size=block_size, reps=bootstrap_reps, seed=2000 + w)
            print(f"      down_delta mean={bs['mean']:.3f}   95% CI [{bs['ci_lo']:.3f}, {bs['ci_hi']:.3f}]")

        if up and down:
            print("      heuristic: if a3 truly led upward transitions, up_delta should tend > 0")
            print("                 if it led downward transitions, down_delta should tend < 0")
        print()

def json_report(
    packets: List[Packet],
    runs: List[Run],
    min_run_packets: int,
    pre_packets: int,
    block_size: int,
    bootstrap_reps: int,
) -> Dict[str, object]:
    effects = run_level_transition_effects(runs, min_run_packets=min_run_packets, pre_packets=pre_packets)
    effect_ci = {}
    for w, rec in effects.items():
        effect_ci[w] = {}
        for key, seed_base in (("up_delta", 1000), ("down_delta", 2000)):
            vals = rec.get(key, [])
            if vals:
                effect_ci[w][key] = block_bootstrap_mean(vals, block_size, bootstrap_reps, seed_base + w)

    return {
        "basic": {
            **summarize_intervals(packets),
            "packet_weighted_mean_w": packet_weighted_mean_w(packets),
            **time_weighted_power_and_energy(packets),
        },
        "grouped_a3_stats": grouped_a3_stats(packets),
        "models_w_to_a3": fit_models_w_to_a3(packets),
        "inversion_from_a3": inversion_accuracy_from_a3(packets),
        "runs": {
            "n_runs": len(runs),
            "mean_packets_per_run": mean([r.n_packets for r in runs]) if runs else float("nan"),
            "median_packets_per_run": statistics.median([r.n_packets for r in runs]) if runs else float("nan"),
            "mean_run_duration_s": mean([r.duration_s for r in runs]) if runs else float("nan"),
            "median_run_duration_s": statistics.median([r.duration_s for r in runs]) if runs else float("nan"),
        },
        "transition_windows": transition_windows(runs, min_run_packets, pre_packets),
        "run_level_transition_effects": effects,
        "run_level_transition_effects_bootstrap": effect_ci,
    }

# --------------------------------- main --------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE, help="journalctl --since argument")
    ap.add_argument("--unit", default="solix-monitor", help="systemd unit name")
    ap.add_argument("--stdin", action="store_true", help="read log text from stdin instead of journalctl")
    ap.add_argument("--require-a3", action="store_true", help="drop packets missing a3")
    ap.add_argument("--min-run-packets", type=int, default=DEFAULT_MIN_RUN_PACKETS)
    ap.add_argument("--pre-packets", type=int, default=DEFAULT_PRE_PACKETS)
    ap.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    ap.add_argument("--bootstrap-reps", type=int, default=DEFAULT_BOOTSTRAP_REPS)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    else:
        try:
            text = read_log_text_from_journalctl(args.since, args.unit)
        except subprocess.CalledProcessError as e:
            print(f"journalctl failed: {e}", file=sys.stderr)
            return 2

    packets = parse_packets_from_text(text, require_a3=args.require_a3)

    if len(packets) < 2:
        print("Not enough packets parsed.", file=sys.stderr)
        return 1

    runs = build_runs(packets)

    if args.json:
        print(json.dumps(
            json_report(
                packets,
                runs,
                min_run_packets=args.min_run_packets,
                pre_packets=args.pre_packets,
                block_size=args.block_size,
                bootstrap_reps=args.bootstrap_reps,
            ),
            indent=2,
            sort_keys=True,
            default=str,
        ))
        return 0

    report_basic(packets)
    report_grouped_a3(packets)
    report_models(packets)
    report_inversion(packets)
    report_runs(runs)
    report_transition_descriptives(
        runs,
        min_run_packets=args.min_run_packets,
        pre_packets=args.pre_packets,
        block_size=args.block_size,
        bootstrap_reps=args.bootstrap_reps,
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
