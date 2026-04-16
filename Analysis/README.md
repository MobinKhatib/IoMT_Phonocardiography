# PCG Analysis Pipeline (`pcg_pipeline.py`)

This document explains the full phonocardiogram (PCG) analysis pipeline, its output schema, and how to integrate it into a WebUI (especially charts/plots).

---

## 1) What this pipeline does

`pcg_pipeline.py` converts a WAV recording into structured analysis data.

### Processing modules

1. **Signal preprocessing**
   - Notch filter (powerline removal)
   - Bandpass filter
   - Wavelet denoising
   - Savitzky-Golay smoothing

2. **Envelope + peak detection**
   - Shannon energy envelope
   - Adaptive peak detection
   - S1/S2 classification via interval logic

3. **Segmentation**
   - Sample-level state labels: `S1`, `Systole`, `S2`, `Diastole`
   - Segment list with start/end sample + time

4. **Cycle feature extraction + classification**
   - Per-cycle temporal/spectral/statistical features
   - Rule-based physiological checks
   - Isolation Forest anomaly detection

5. **Murmur analysis**
   - Systolic/diastolic murmur flags
   - Ratio-based grade approximation
   - Summary assessment text

6. **Optional export**
   - Save filtered waveform as `<input>_filtered.wav`

---

## 2) Main function and signature

```python
from pcg_pipeline import run_pcg_pipeline, PCGConfig

result = run_pcg_pipeline(
    filename="/path/to/input.wav",
    config=None,                 # optional PCGConfig
    save_filtered_wav=False,     # True to save filtered WAV
    output_filename=None,        # optional explicit output path
    include_signals=False        # True to include raw arrays for plotting
)
```

Alias (same behavior):

```python
from pcg_pipeline import run_pipeline
result = run_pipeline("/path/to/input.wav", include_signals=True)
```

---

## 3) Configuration (`PCGConfig`)

Default values are notebook-equivalent.

| Field | Default | Meaning |
|---|---:|---|
| `lowcut` | 25.0 | Bandpass low cutoff (Hz) |
| `highcut` | 200.0 | Bandpass high cutoff (Hz), clamped to Nyquist-1 |
| `notch_freq` | 50.0 | Powerline notch (50/60 Hz region) |
| `filter_order` | 4 | Butterworth order |
| `wavelet` | `db6` | Wavelet family for denoise |
| `wavelet_level` | 4 | Decomposition level |
| `envelope_cutoff` | 8.0 | Envelope smoothing lowpass cutoff |
| `min_peak_dist` | 0.25 | Min distance between peaks (seconds) |
| `bpm_min` | 40 | Heart-rate bound (reserved context) |
| `bpm_max` | 200 | Heart-rate bound (reserved context) |
| `normal_ranges` | dict | Rule-based normal limits |
| `murmur_grade_thresholds` | list | Ratio thresholds for grading |

---

## 4) Output schema (top-level)

`run_pcg_pipeline(...)` returns a dictionary with these keys:

- `file_info`
- `config`
- `peaks`
- `segmentation`
- `classification`
- `murmur`
- `exports`
- `medical_notice`
- `signals` *(only when `include_signals=True`)*

### 4.1 `file_info`

```json
{
  "filename": "...",
  "sample_rate_hz": 500,
  "nyquist_hz": 250.0,
  "duration_s": 24.9,
  "samples": 12460,
  "highcut_clamped": false,
  "effective_highcut_hz": 200.0
}
```

### 4.2 `peaks`

Contains counts, thresholds, and indices/times:
- `total_peaks`
- `s1_count`, `s2_count`
- `peak_indices`, `s1_indices`, `s2_indices`
- `peak_times_s`, `s1_times_s`, `s2_times_s`
- `threshold`

### 4.3 `segmentation`

- `state_names`: `['S1','Systole','S2','Diastole']`
- `states`: per-sample label array
- `segments`: list of objects:
  - `state`
  - `start_index`, `end_index`
  - `start_s`, `end_s`
- `stats`: per-state duration summary

### 4.4 `classification`

- `cycles`: per-cycle feature list (durations, RMS, MFCC means, intervals, etc.)
- `rule_based`:
  - `normal_cycles`
  - `flagged_cycles`
  - `flagged_details` (cycle index + violated limits)
- `isolation_forest`:
  - `feature_keys`
  - `labels` (`1=normal`, `-1=anomaly`)
  - `scores`
  - summary counts and score range
- `per_cycle_stats`
- `hrv_metrics` (or `null` if insufficient RR data)

### 4.5 `murmur`

- `analysis_range_hz`
- `systolic_murmur_cycles`
- `diastolic_murmur_cycles`
- `total_cycles`
- `systolic_pct`, `diastolic_pct`
- `assessment`
- `cycle_results` (per-cycle murmur detail)

### 4.6 `signals` (optional)

Available only when `include_signals=True`:

- `time_axis_s`
- `raw`
- `filtered`
- `filtered_normalized`
- `envelope`

### 4.7 `exports`

- `saved_filtered_wav`: output path or `null`

---

## 5) CLI usage

You can run pipeline directly:

```bash
cd Analysis
python pcg_pipeline.py /absolute/path/to/recording.wav --include-signals --save-filtered
```

---

## 6) WebUI integration: recommended architecture

### Backend

1. Receive WAV upload
2. Save temp file
3. Run pipeline
4. Return JSON to frontend

Recommended modes:

- **Quick summary endpoint**: `include_signals=False`
- **Detailed chart endpoint**: `include_signals=True`

### Frontend

Use returned arrays/events to draw layers:

1. **Waveform trace**: `signals.filtered`
2. **Envelope trace**: `signals.envelope`
3. **S1/S2 markers**: `peaks.s1_times_s`, `peaks.s2_times_s`
4. **Segmentation spans**: `segmentation.segments`
5. **Anomaly overlays**: `classification.cycles` + `classification.isolation_forest.labels`
6. **Murmur overlays**: `classification.cycles` + `murmur.cycle_results`

---

## 7) Plot mapping (field-by-field)

### Plot A — Signal + Envelope + S1/S2

- x-axis: `signals.time_axis_s`
- y1 line: `signals.filtered`
- y2/overlay line: `signals.envelope`
- marker series:
  - S1 points at `peaks.s1_times_s`
  - S2 points at `peaks.s2_times_s`

### Plot B — Segmentation timeline

Render translucent spans from `segmentation.segments`:

- `S1` color: red
- `Systole` color: orange
- `S2` color: blue
- `Diastole` color: green

Use each segment’s `start_s` / `end_s` as the span boundaries.

### Plot C — Normal vs Anomaly overlays

For each cycle `i`:

- interval start = `cycles[i]["_s1_start"] / sample_rate_hz`
- interval end = `cycles[i]["_dia_end"] / sample_rate_hz`
- color by `labels[i]`:
  - `1` => green (normal)
  - `-1` => red (anomaly)

### Plot D — Murmur overlays

For each cycle:

- if `cycle_results[i]["systolic_murmur"]`:
  - shade [`_sys_start`, `_sys_end`]
- if `cycle_results[i]["diastolic_murmur"]`:
  - shade [`_dia_start`, `_dia_end`]

Convert sample index to seconds with:

\[
 t = \frac{\text{sample_index}}{\text{sample_rate_hz}}
\]

---

## 8) Performance guidance (important for WebUI)

Long recordings can produce large JSON payloads when `include_signals=True`.

### Best practice

- Keep full precision for events (`segments`, `peaks`, cycle labels)
- Downsample only waveform arrays for display

Example decimation helper:

```python
import numpy as np

def decimate_xy(x, y, max_points=3000):
    n = len(x)
    if n <= max_points:
        return x, y
    idx = np.linspace(0, n - 1, max_points, dtype=int)
    return [x[i] for i in idx], [y[i] for i in idx]
```

Suggested frontend target:
- 2k–5k points per line chart layer

---

## 9) Suggested API response shape for frontend

A practical response partition:

```json
{
  "summary": {
    "sample_rate_hz": 500,
    "duration_s": 24.9,
    "total_cycles": 22,
    "anomaly_cycles": 3,
    "murmur_assessment": "Some murmur-like activity detected (may be recording noise)"
  },
  "events": {
    "segments": [...],
    "s1_times_s": [...],
    "s2_times_s": [...],
    "labels": [...],
    "murmur_cycle_results": [...]
  },
  "signals": {
    "time_axis_s": [...],
    "filtered": [...],
    "envelope": [...]
  }
}
```

---

## 10) Reliability notes

- The implementation converts NumPy values to built-in Python types for JSON compatibility.
- If MFCC extraction fails due dependency mismatch (`librosa`/`numba`/`numpy`), the pipeline falls back to zero MFCC vector and continues (schema remains stable).

---

## 11) Test usage

Current automated tests are in:

- `Analysis/test_pcg_pipeline.py`

Run:

```bash
cd Analysis
python -m unittest -v test_pcg_pipeline.py
```

---

## 12) Medical disclaimer

This pipeline is a **signal analysis** tool and **not** a medical diagnostic device.
