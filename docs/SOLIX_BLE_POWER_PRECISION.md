# Solix BLE Power Precision: A Reverse Engineering Investigation

> A battery-powered edge inference node reports power draw in whole watts over BLE. You
> suspect the device rounds to the nearest integer. You have access to the raw packet stream.
> How would you determine whether the reported integer is the best precision available, or
> whether sub-integer information can be recovered — either from undocumented fields in the
> protocol, or from the statistical structure of the rounding itself? What does signal
> processing theory say about averaging quantized measurements? What does the HCI and
> science-and-technology-studies literature say about displaying a number with more decimal
> places than the instrument natively provides — is it dishonest, or is it more truthful?
> Investigate empirically, then make a concrete design recommendation.

---

## Academic Context

Three bodies of literature converge on this problem.

**Dithering and temporal averaging.** In ADC signal processing, deliberately adding noise
before quantization (dithering) and then averaging the results recovers sub-LSB precision
because the quantization error becomes uncorrelated with the signal. Wannamaker (2003)
formalises this: effective resolution improves as √n for n independent samples. Texas
Instruments AN-804 gives the engineering practice. The mechanism applies here without
deliberate dither — the Solix's rounding error is already uncorrelated with the Jetson's
actual draw, so natural dither is present *if* the load is genuinely continuous across integer
boundaries. Whether that condition holds is an empirical question, not a theoretical guarantee.

**Ergodicity and time averages.** Peters (2019) argues that economics conflates ensemble
averages with time averages, and that for a single system evolving over time the time average
is the quantity of physical interest. The same distinction applies here: a single instantaneous
integer reading is an ensemble snapshot; the time-average of many readings is what the
system actually drew over that window. If the process is stationary (short-term power draw
has no trend), the time average converges to the true value. This is not an approximation
— it is the ergodic identity. But the time average must be computed with actual elapsed time
as weights, not with packet counts. A packet lasting 10 seconds contributes ten times as much
to true energy consumption as one lasting 1 second, regardless of both appearing once in the
sequence.

**Uncertainty visualisation.** Hullman et al. (CHI 2016, IEEE TVCG 2018) document that
static aggregate numbers are systematically misread as direct measurements. Hypothetical
Outcome Plots animate draws from a distribution to make uncertainty legible without
requiring statistical literacy. Padilla, Kay and Hullman (2020) survey the broader field.
The practical design tension: showing "7.3W" is more accurate than "7W" (ergodic truth)
but implies instrumental precision the sensor cannot claim (epistemically misleading).

---

## Background

The Anker Solix C300X DC battery reports power telemetry over BLE using a proprietary TLV
(tag-length-value) protocol. The monitor service (`solix_monitor.py`) connects via Bleak,
subscribes to passive push notifications on characteristic
`8c850003-0302-41c5-b46e-cf057c562025`, and parses the stream.

The device pushes packets approximately every **3.5 seconds** (observed: min 0.13s,
max 38.93s, mean 3.41s, median 3.46s over 948 packets). All power-relevant fields are
encoded as **16-bit little-endian unsigned integers** — whole watts, no fractional precision.
The question this investigation asked: can we do better?

---

## TLV Protocol: What's Actually in the Packets

Confirmed tag map (reverse-engineered from 948 live packets):

| Tag | Subtype | Bytes | Field |
|-----|---------|-------|-------|
| 0xa3 | 0x02 | 2 | **unknown** (investigated below) |
| 0xa4 | 0x02 | 2 | USB-C1 output W |
| 0xa5 | 0x02 | 2 | USB-C2 output W |
| 0xa6 | 0x02 | 2 | USB-C3 output W |
| 0xad | 0x02 | 2 | total output W |
| 0xab | 0x02 | 2 | solar input W |
| 0xac | 0x02 | 2 | total input W |
| 0xaf | 0x02 | 2 | battery voltage mV |
| 0xb5 | 0x01 | 1 | temperature °C |
| 0xb7 | 0x01 | 1 | state of charge % |
| 0xb8 | 0x01 | 1 | charge limit % |
| 0xa2 | 0x03 | 4 | **unknown** — always 0 in all captured packets |
| 0xc3 | 0x00 | 16 | device serial number (ASCII: "AZVZF80E51400468") |
| 0xf8 | 0x04 | 20 | unknown blob — last byte varies each packet (rolling counter or CRC) |

The Jetson is connected to **USB-C3** (0xa6), not a DC barrel connector as originally assumed.
`total_output_w` (0xad) equals 0xa6 in all observed packets, confirming single-port draw.

The parser previously handled subtype 0x03 only when `len(vb) == 2`. Tag 0xa2 has
subtype 0x03 with **4 bytes** and was silently dropped. After fixing the parser to handle
2- and 4-byte subtype 0x03 entries as uint32, 0xa2 decoded to 0 in every packet across the
full capture window under all load conditions.

---

## The 0xa3 Investigation

Tag 0xa3 caught our attention because it varies continuously — unlike the other fields which
are stable integers. It sits in the range 194–300 across observed loads of 6–11W.

### Raw Data (948 packets, ~4 hours; time-weighted)

| W | time_s | % time | tw_a3_mean | uw_a3_mean |
|---|--------|--------|------------|------------|
| 6 | 630.2 | 19.5% | 283.6 | 283.6 |
| 7 | 2195.9 | 68.0% | 280.8 | 280.7 |
| 8 | 279.5 | 8.7% | 270.1 | 269.8 |
| 9 | 24.1 | 0.7% | 223.9 | 223.7 |
| 10 | 70.5 | 2.2% | 203.6 | 203.7 |
| 11 | 27.6 | 0.9% | 209.9 | 209.6 |

Time-weighted and unweighted a3 means are nearly identical (max difference 0.3 units),
confirming the uniform ~3.5s inter-packet interval makes packet-count statistics a valid
proxy for time-weighted statistics in this dataset.

### Model Comparison (unweighted, n=948)

| Model | R² |
|-------|----|
| Linear | 0.502 |
| Exponential | 0.466 |
| Quadratic | ~0.52 |
| Cubic | ~0.56 |
| Piecewise linear (split at 8.5W) | ~0.58 |
| Per-integer step means (upper bound) | ~0.60 |

R² with larger sample (948 vs 401 packets) is substantially lower than earlier estimates,
indicating the earlier sample was unrepresentative. Even the step-means ceiling explains
only ~60% of variance.

The piecewise structure is still visible:
- **≤8W**: slope ~−9/W — weak signal
- **>8W**: slope ~−3/W — nearly flat, saturated

The 8→9W transition drops ~52 units. Above ~10W, 0xa3 saturates and the 10→11W step inverts.

### Why 0xa3 Cannot Provide Sub-Integer Precision

Adjacent bucket ranges overlap massively (33–73 units) relative to the ~9-unit/W slope.
The trivial baseline — always predict 7W (68% of time) — outperforms a nearest-mean
classifier on exact match because the 6W and 7W means differ by only ~3 units against a
within-bucket std of ~10–13.

**Conclusion:** 0xa3 is a BMS-internal signal, likely switching regulator bus current or
internal power delivery activity. Non-linear, saturates above 9W, far too noisy to use as
a sub-integer power proxy.

### Does 0xa3 Lead W Transitions?

A final test: does 0xa3 fall *before* an upward W transition and rise before a downward one?
If so it could serve as a leading indicator even without sub-integer precision.

For every run of same-W packets of length ≥ 4, we compared:
- **mid-run a3** — packets far from any transition
- **pre-up a3** — last 3 packets before W increases
- **pre-down a3** — last 3 packets before W decreases

Results (1055 packets, 4 hours):

| W | category | n | mean | std |
|---|----------|---|------|-----|
| 7 | mid | 306 | 280.0 | 9.0 |
| 7 | pre-up | 69 | 278.5 | 10.3 |
| 7 | pre-down | 132 | 281.2 | 9.2 |

Pre-up (278.5) < mid (280.0) < pre-down (281.2) — the correct ordering for a leading
indicator. However the effect size is 1.5 units against a within-group std of ~9–10 units.
t-test: t ≈ 1.13, p ≈ 0.26. **Not statistically significant.**

All other W levels (6W, 8W, 9W, 10W, 11W) had insufficient qualifying runs to analyse —
every high-watt excursion is too short to generate mid-run packets under the MIN_RUN=4
threshold.

**0xa3 has no usable predictive signal for W transitions.**

---

## Temporal Averaging: Theory, Limits, and Empirical Reality

### The Dithering Argument

The standard DSP argument: if quantization error is uncorrelated with the signal, averaging
n samples reduces noise by √n, recovering sub-LSB precision. If the Solix **rounds to nearest**
and the true load is 7.3W, it would report 7W ~70% of the time and 8W ~30% of the time.
Their time-weighted mean converges to 7.3W.

### The Truncation Complication

"Quantization" in DSP usually means truncation (floor), not rounding. Truncation error is
always negative and biased: E[error] = −0.5 LSB. The temporal average then converges to
`true_value − bias` where bias ∈ [0, 0.5W]. More samples reduce random noise but cannot
remove this systematic offset. We cannot determine from the BLE stream alone whether the
Solix truncates or rounds; no higher-precision ground truth is available. The irreducible
systematic uncertainty on any moving average is therefore ±0.5W, direction unknown.

### The Packet-Count vs Time-Weighted Problem

The moving average implemented in the frontend weights each BLE packet equally — a packet
that lasted 10 seconds counts the same as one that lasted 2 seconds. For a true
time-weighted energy estimate, packets should be weighted by their duration.

Empirically, this turns out not to matter much for this device: inter-packet intervals are
tightly clustered around 3.5s (mean 3.41s, median 3.46s). The measured mean divergence
between packet-count and time-weighted 9-sample moving averages is **0.017W**, maximum
**0.125W** (in one window containing a 38.93s reconnect gap). Under normal operation
the packet-count average is a valid proxy for the time-weighted average.

### Empirical Test: What Do the Integer States Look Like in Time?

Run-length analysis with actual timestamps (947 packets with known duration):

| W | runs | total_s | mean run | max run |
|---|------|---------|----------|---------|
| 6 | 129 | 630.2s | **4.9s** | 17.2s |
| 7 | 175 | 2195.9s | **12.5s** | 106.7s |
| 8 | 61 | 279.5s | **4.6s** | 42.4s |
| 9 | 6 | 24.1s | 4.0s | 6.7s |
| 10 | 13 | 70.5s | 5.4s | 8.4s |
| 11 | 7 | 27.6s | 3.9s | 6.5s |

The device spends 68% of observed time at 7W (mean stable run: 12.5s, max: 106.7s) and
19.5% at 6W (mean run: 4.9s). These are not brief transient touches — they are genuine
stable states. The dithering model (load continuously hovering between integers) does not
describe this workload. The load discretises cleanly into integer states and stays there.

An earlier draft of this document claimed 6W appeared in "brief single-packet dips" based
on run-length analysis in packet counts. That was incorrect: packet counts say nothing about
duration. A "1-packet run" at 6W could be 2 seconds or 10 seconds. The time-based analysis
shows 6W runs average 4.9 seconds — real states, not noise.

The dithering mechanism would apply during genuine between-integer draws. The fractional
distribution of 9-sample moving averages is strongly bimodal at 0.00 and ~0.80, with a
gap at 0.90–0.99 that is zero by construction (no combination of adjacent integers with
n=9 produces a fractional part there). This structure is entirely explained by the
integer-state dynamics and is not evidence of a continuous underlying signal.

### Display Philosophy

The moving average is a temporal aggregate of discrete integer states, not a recovered
continuous value. Its relationship to true instantaneous power depends on an unknowable
assumption (truncation vs rounding, ±0.5W systematic uncertainty) and on whether the load
is genuinely continuous (it is not, for this workload).

The resolution adopted:
- The **main display** shows the raw integer — honest about the sensor's native resolution
- The **debug panel** shows moving averages with sample counts (e.g. `7.34W (n=4)`) making
  the statistical basis explicit

The `n=` count signals that the number is a temporal estimate requiring interpretation.
This does not resolve the epistemological tension but does not conceal it.

---

## Codebase

**solix-monitor service** (`ops/services/solix-monitor/solix_monitor.py`,
installed at `/opt/bartleby/solix-monitor/solix_monitor.py`): BLE monitor that parses
the TLV stream, maintains STATE, and serves `/sensor/power`. The `parse_tlv()` function
was updated during this investigation to handle subtype 0x03 with 4-byte payloads (uint32),
correcting a silent drop of tag 0xa2. The `on_notify()` callback logs the full raw hex to
journalctl (`INFO RAW <hex>`) to enable offline analysis.

**`analyze_ble.py`** (`ops/scripts/analyze_ble.py`): pulls raw BLE packets and connection
events from journalctl, assigns per-packet durations from timestamps, computes time-weighted
and unweighted per-watt stats, measures packet-count vs time-weighted moving average
divergence, fits regression models of 0xa3 vs total output watts. Run as:
```
python3 ops/scripts/analyze_ble.py "4 hours ago"
```

**Frontend moving average** (`docs/app/power.js`): `wattsBuffer` maintains a rolling 30s
deque of unique BLE readings (deduplicated by `solix_reading_ts`). `wattsAvg(secs)` returns
the mean over a window. The debug table shows 5s/10s/15s/30s averages with sample counts,
updated via direct DOM writes to bypass the `shouldPreserveDom` chart-preservation logic.

---

## References

- Wannamaker, R.A. (2003). *The Theory of Dithered Quantization*. PhD thesis.
  https://web.archive.org/web/20240423010933/http://www.robertwannamaker.com/writings/rw_phd.pdf
- Texas Instruments (2012). *AN-804: Improving A/D Converter Performance Using Dither*.
  https://www.ti.com/lit/an/snoa232/snoa232.pdf
- Peters, O. (2019). The ergodicity problem in economics. *Nature Physics* 15, 1216–1221.
  https://www.semanticscholar.org/paper/The-ergodicity-problem-in-economics-Peters/0c607d3cd8bd47b4c34a5d99f5de09a51ccafed0
- Hullman, J. et al. (2018). Hypothetical Outcome Plots Help Untrained Observers Judge
  Trends in Ambiguous Data. *IEEE TVCG*.
  https://users.eecs.northwestern.edu/~jhullman/hops_jobs_pfs.pdf
- Padilla, L., Kay, M., Hullman, J. (2020). Uncertainty Visualization.
  https://friendly.github.io/6135/papers/Uncertainty_Visualization_Padilla_Kay_Hullman_2020.pdf
