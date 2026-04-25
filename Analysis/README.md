# PCG Analysis Pipeline (`pcg_pipeline.py`)

A Python pipeline that converts a raw WAV phonocardiogram (PCG) recording into structured, JSON-serialisable analysis data — covering signal cleaning, heart sound segmentation, cycle-level feature extraction, anomaly detection, and murmur grading.

---

## Table of contents

1. [What this pipeline does](#1-what-this-pipeline-does)
2. [Quick start](#2-quick-start)
3. [Dependencies](#3-dependencies)
4. [Configuration (`PCGConfig`)](#4-configuration-pcgconfig)
5. [Processing stages explained](#5-processing-stages-explained)
6. [Heart metrics explained](#6-heart-metrics-explained)
7. [Output schema](#7-output-schema)
8. [CLI usage](#8-cli-usage)
9. [WebUI integration](#9-webui-integration)
10. [Performance guidance](#10-performance-guidance)
11. [Running the tests](#11-running-the-tests)
12. [Medical disclaimer](#12-medical-disclaimer)

---

## 1) What this pipeline does

`pcg_pipeline.py` takes a mono WAV file (typically 500 Hz, recorded from an ESP32 microphone) and runs it through five modules:

| Module | Output |
| --- | --- |
| Signal preprocessing | Clean, noise-reduced waveform |
| Envelope + peak detection | S1 and S2 heart sound locations |
| Segmentation | Sample-level labels: S1 / Systole / S2 / Diastole |
| Feature extraction + classification | Per-cycle metrics, rule-based checks, Isolation Forest anomaly scores |
| Murmur analysis | Per-cycle murmur flags and a summary assessment |

All outputs are plain Python types (no NumPy) so the returned dict can be passed directly to `json.dumps`.

---

## 2) Quick start

```python
from pcg_pipeline import run_pcg_pipeline, PCGConfig

result = run_pcg_pipeline(
    filename="/path/to/recording.wav",
    config=None,               # optional PCGConfig — defaults are fine for 500 Hz recordings
    save_filtered_wav=False,   # True → write <input>_filtered.wav to disk
    output_filename=None,      # custom path for the filtered WAV
    include_signals=False,     # True → add raw/filtered/envelope arrays to the result
)
```

Alias (identical behaviour):

```python
from pcg_pipeline import run_pipeline
result = run_pipeline("/path/to/recording.wav", include_signals=True)
```

---

## 3) Dependencies

```text
numpy
scipy
PyWavelets
librosa
scikit-learn
```

Install:

```bash
pip install numpy scipy PyWavelets librosa scikit-learn
```

---

## 4) Configuration (`PCGConfig`)

Pass a `PCGConfig` instance to override any default. Defaults are tuned for ESP32 recordings at 500 Hz.

| Field | Default | Meaning |
| --- | --- | --- |
| `lowcut` | `25.0` | Bandpass lower cutoff (Hz) — captures the full S1 fundamental |
| `highcut` | `200.0` | Bandpass upper cutoff (Hz) — auto-clamped to `Nyquist - 1` |
| `notch_freqs` | `(50.0, 100.0, 150.0)` | Powerline hum frequencies to notch out (Hz) |
| `notch_q` | `35.0` | Q-factor for each notch (higher = narrower notch) |
| `filter_order` | `4` | Butterworth bandpass order |
| `wavelet` | `"db6"` | Wavelet family for DWT denoising |
| `wavelet_level` | `4` | Decomposition depth |
| `envelope_cutoff` | `8.0` | Lowpass cutoff for Shannon envelope smoothing (Hz) |
| `min_peak_dist` | `0.25` | Minimum time between detected peaks (seconds) |
| `bpm_min` | `40` | Physiological HR lower bound (reserved for future gating) |
| `bpm_max` | `200` | Physiological HR upper bound (reserved for future gating) |
| `normal_ranges` | see below | Rule-based physiological limits per metric |
| `murmur_grade_thresholds` | `[0.15, 0.30, 0.50, 0.70, 0.90]` | Energy-ratio thresholds for murmur grading |

Default `normal_ranges`:

```python
{
    "s1_duration_ms":  (50, 200),
    "s2_duration_ms":  (40, 150),
    "systolic_ms":     (150, 450),
    "diastolic_ms":    (150, 1200),
    "s1_s2_amp_ratio": (0.5, 3.0),
    "heart_rate_bpm":  (45, 180),
}
```

---

## 5) Processing stages explained

### Stage 1 — DC offset removal

```
data = data - mean(data)
```

Removes any constant voltage offset introduced by the ADC or BLE transmission path. Must happen before filtering to avoid distortion.

### Stage 2 — Multi-notch filter

Three narrow IIR notch filters are applied sequentially at 50 Hz, 100 Hz, and 150 Hz (fundamental powerline frequency plus its two lowest harmonics). Q = 35 keeps each notch very narrow so heart-sound energy at adjacent frequencies is untouched. Any notch above Nyquist is skipped automatically.

### Stage 3 — Bandpass filter (25–200 Hz)

A zero-phase 4th-order Butterworth bandpass keeps only the frequency range that contains S1 and S2 energy and rejects low-frequency motion artefacts and high-frequency noise above the heart-sound band. The upper cutoff is clamped to `Nyquist - 1` automatically.

### Stage 4 — Wavelet denoising

Daubechies-6 (db6) discrete wavelet transform, 4 levels. A soft threshold is applied to all detail coefficients using a scaled Donoho-Johnstone universal threshold:

```
uthresh = 0.6 × σ × sqrt(2 × log(N))
```

The scale factor 0.6 (versus the classical value of 1.0) is intentional: it preserves more fine structure in the heart sounds while still suppressing broadband noise. σ is estimated from the finest-level coefficients via MAD.

### Stage 5 — Savitzky-Golay smoothing

A window-11, polynomial-order-3 SG filter removes any residual sample-level jitter while preserving peak sharpness. Final output is `filtered`.

### Stage 6 — Shannon energy envelope

```
E[n] = -x[n]² · log(x[n]² + ε)
```

Shannon energy suppresses low-amplitude noise more aggressively than simple squaring and gives better S1/S2 contrast. The result is lowpass-filtered at 8 Hz to produce a smooth envelope.

### Stage 7 — Peak detection and S1/S2 classification

`scipy.signal.find_peaks` is applied to the envelope with an adaptive threshold at the 75th percentile. Peaks are then classified as S1 or S2 using the interval-ratio rule: systole (S1→S2) is always shorter than diastole (S2→next S1), so consecutive triplets of peaks are labelled by which gap is shorter.

### Stage 8 — Segmentation

Sample-level state labels (0=S1, 1=Systole, 2=S2, 3=Diastole) are assigned by:
- Growing S1/S2 boundaries outward from each peak to 40% of peak height
- Filling the gap between S1-end and the next S2-start as Systole (if < 400 ms)
- Filling the gap between S2-end and the next S1-start as Diastole

---

## 6) Heart metrics explained

This section describes every metric the pipeline produces and what it means clinically.

### 6.1 Timing intervals

| Metric | Key | Normal range | Meaning |
| --- | --- | --- | --- |
| S1 duration | `s1_duration_ms` | 50–200 ms | Width of the first heart sound. S1 is caused by closure of the mitral and tricuspid valves at the start of ventricular contraction. Too short can indicate a sharp, pathological snap; too long may suggest a prolonged closure sequence. |
| S2 duration | `s2_duration_ms` | 40–150 ms | Width of the second heart sound. S2 is caused by closure of the aortic and pulmonary valves at the end of systole. A split S2 (audible doubling) can appear as an abnormally long or double-peaked S2. |
| Systolic interval | `systolic_ms` | 150–450 ms | Time from S1 start to S2 start. This is the period of ventricular ejection. Normal adult systole at rest is ~300 ms. Prolonged systole may suggest impaired ventricular function. |
| Diastolic interval | `diastolic_ms` | 150–1200 ms | Time from S2 start to next S1 start. Diastole is the filling phase. It is longer than systole at normal resting heart rates and shortens as rate increases. Very short diastole at high heart rates may limit filling time. |
| Cycle duration | `cycle_duration_ms` | derived | Full S1-to-S1 period in ms. Reciprocal gives heart rate. |
| Heart rate | `heart_rate_bpm` | 45–180 bpm | Beats per minute derived from cycle duration. Normal adult resting range is 60–100 bpm. |
| S:D ratio | `sd_ratio` | ~0.4–0.7 | Systolic interval divided by diastolic interval. At 60 bpm this is roughly 0.5. Rising ratio (diastole shrinking relative to systole) occurs at high heart rates or with prolonged ventricular contraction. |

### 6.2 Amplitude features

| Metric | Key | Meaning |
| --- | --- | --- |
| S1 RMS | `s1_rms` | Root-mean-square amplitude of the S1 segment. Reflects how loud the mitral/tricuspid closure is in the recording. |
| S2 RMS | `s2_rms` | Root-mean-square amplitude of the S2 segment. |
| S1/S2 amplitude ratio | `s1_s2_amp_ratio` | S1 RMS divided by S2 RMS. Normal range 0.5–3.0. A ratio < 0.5 (S2 louder than S1) at the apex may indicate aortic regurgitation. A very high ratio may reflect a soft S2. |
| Energy concentration | `energy_concentration` | Fraction of the full-cycle energy contained in the S1 and S2 segments. A healthy heart concentrates energy in the valve closures; a murmur spreads energy into systole or diastole. |
| Systolic noise ratio | `sys_noise_ratio` | RMS of the systolic segment divided by S1 RMS. Low values (< 0.15) indicate a quiet systole. Elevated values suggest systolic murmur energy. |
| Diastolic noise ratio | `dia_noise_ratio` | RMS of the diastolic segment divided by S1 RMS. Same interpretation for the diastolic phase. |

### 6.3 Spectral and waveform shape features

| Metric | Key | Meaning |
| --- | --- | --- |
| S1/S2 zero-crossing rate | `s1_zcr`, `s2_zcr` | Rate of sign changes per sample. A high ZCR indicates a noisy or multi-component sound; a low ZCR indicates a smoother, more tonal segment. |
| S1/S2 excess kurtosis | `s1_kurtosis`, `s2_kurtosis` | Statistical "peakedness" of the amplitude distribution. A sharp impulsive sound (like a clean valve closure) has high kurtosis. Broad or noisy segments have lower kurtosis. |
| S1/S2 spectral centroid | `s1_centroid`, `s2_centroid` | Frequency-weighted centre of mass of the FFT magnitude spectrum (Hz). S1 typically centres around 50–100 Hz; S2 slightly higher. An elevated centroid may indicate high-frequency murmur contamination of the sound. |
| MFCC means | `mfcc_0` … `mfcc_7` | Mean of 8 Mel-frequency cepstral coefficients computed over the full cycle. MFCCs capture the spectral envelope shape and are used as input features for the Isolation Forest classifier. They do not have a direct single-value clinical interpretation but collectively encode the timbral character of the cycle. |

### 6.4 Heart rate variability (HRV)

Computed from the sequence of RR intervals (cycle durations) across the whole recording. Only cycles with durations between 300 ms and 1500 ms are included.

| Metric | Key | Meaning |
| --- | --- | --- |
| Mean heart rate | `heart_rate_mean_bpm` | Average BPM across valid cycles. |
| HR standard deviation | `heart_rate_std_bpm` | Beat-to-beat variability of heart rate (BPM). |
| SDNN | `sdnn_ms` | Standard deviation of all NN (normal-to-normal) intervals. Captures overall HRV. Values < 20 ms suggest reduced autonomic modulation. |
| RMSSD | `rmssd_ms` | Root mean square of successive RR differences. Reflects short-term, predominantly parasympathetic (vagal) variability. Healthy adults at rest typically show 20–60 ms. |
| pNN50 | `pnn50_pct` | Percentage of consecutive RR differences > 50 ms. A higher pNN50 indicates greater parasympathetic tone. |

### 6.5 Murmur grading

Each cardiac cycle is independently assessed for murmur energy.

**Detection**: the RMS of the systolic (or diastolic) interval is compared to S1 RMS. If the ratio exceeds the first threshold (0.15 by default) the cycle is flagged.

**Grading** uses an energy-ratio approximation of the Levine scale:

| Grade | Ratio range | Clinical description |
| --- | --- | --- |
| 0 | < 0.15 | No murmur |
| 1 | 0.15–0.30 | Barely audible, heard only in quiet conditions |
| 2 | 0.30–0.50 | Faint but clearly heard |
| 3 | 0.50–0.70 | Moderately loud |
| 4 | 0.70–0.90 | Loud |
| 5–6 | > 0.90 | Very loud, may be audible with stethoscope partially off chest |

**Murmur shape features** (computed when the segment is long enough):

- `sys_diamond`: `True` if the envelope of the systolic segment peaks in the middle quarter of the interval. A diamond (crescendo-decrescendo) shape is characteristic of aortic stenosis and other ejection murmurs.
- `dia_decrescendo`: `True` if the first quarter of the diastolic envelope is more than 1.5× louder than the last quarter. A decrescendo shape is characteristic of aortic regurgitation.

**Summary assessment** (from `murmur.assessment`):

| Condition | Text |
| --- | --- |
| > 50% cycles flagged in either phase | "Significant murmur activity …" |
| 20–50% cycles flagged | "Some murmur-like activity detected (may be recording noise)" |
| < 20% cycles flagged | "No significant murmur activity" |

### 6.6 Anomaly detection (Isolation Forest)

All per-cycle features (excluding the private `_start`/`_end` indices) are z-scored and fed to an Isolation Forest with 200 trees and a 15% contamination prior. Each cycle receives:

- `label`: `1` = normal, `-1` = anomaly
- `score`: decision function value (more negative = more anomalous)

An anomalous cycle does not necessarily mean disease — it may reflect a position shift during recording, a deep breath, or an ectopic beat. Use it as a flag for human review rather than a diagnosis.

---

## 7) Output schema

`run_pcg_pipeline(...)` returns a dict with these top-level keys.

### 7.1 `file_info`

```json
{
  "filename": "/path/to/recording.wav",
  "sample_rate_hz": 500,
  "nyquist_hz": 250.0,
  "duration_s": 23.0,
  "samples": 11510,
  "highcut_clamped": false,
  "effective_highcut_hz": 200.0
}
```

### 7.2 `config`

The full `PCGConfig` serialised as a dict, so the consumer knows exactly what parameters were used.

### 7.3 `peaks`

```json
{
  "total_peaks": 54,
  "s1_count": 31,
  "s2_count": 31,
  "peak_indices": [...],
  "s1_indices": [...],
  "s2_indices": [...],
  "peak_times_s": [...],
  "s1_times_s": [...],
  "s2_times_s": [...],
  "threshold": 0.042
}
```

### 7.4 `segmentation`

```json
{
  "state_names": ["S1", "Systole", "S2", "Diastole"],
  "states": [3, 3, 0, 0, 1, 1, ...],
  "segments": [
    {"state": "S1", "start_index": 120, "end_index": 164, "start_s": 0.24, "end_s": 0.328},
    ...
  ],
  "stats": {
    "S1":       {"count": 20, "mean_ms": 88.4, "std_ms": 26.2, "min_ms": 46, "max_ms": 128},
    "Systole":  {"count": 22, "mean_ms": 231.8, ...},
    "S2":       {"count": 28, "mean_ms": 85.3, ...},
    "Diastole": {"count": 29, "mean_ms": 474.6, ...}
  }
}
```

### 7.5 `classification`

```json
{
  "cycles": [
    {
      "s1_duration_ms": 98.0,
      "s2_duration_ms": 82.0,
      "systolic_ms": 302.0,
      "diastolic_ms": 520.0,
      "cycle_duration_ms": 820.0,
      "heart_rate_bpm": 73.2,
      "sd_ratio": 0.58,
      "s1_rms": 0.045,
      "s2_rms": 0.068,
      "s1_s2_amp_ratio": 0.66,
      "energy_concentration": 0.71,
      "s1_zcr": 0.32,
      "s2_zcr": 0.29,
      "s1_kurtosis": 2.1,
      "s2_kurtosis": 1.8,
      "s1_centroid": 78.4,
      "s2_centroid": 91.2,
      "sys_noise_ratio": 0.09,
      "dia_noise_ratio": 0.07,
      "mfcc_0": -12.4,
      "mfcc_1": 3.1,
      "...": "...",
      "_s1_start": 120, "_s1_end": 164,
      "_sys_start": 164, "_sys_end": 315,
      "_s2_start": 315, "_s2_end": 356,
      "_dia_start": 356, "_dia_end": 616
    }
  ],
  "rule_based": {
    "normal_cycles": 20,
    "flagged_cycles": 8,
    "flagged_details": [
      {"cycle_index": 0, "violations": ["s1_s2_amp_ratio=0.5 < 0.5"]}
    ]
  },
  "isolation_forest": {
    "feature_keys": ["s1_duration_ms", "s2_duration_ms", ...],
    "labels": [1, 1, -1, 1, ...],
    "scores": [0.08, 0.06, -0.05, ...],
    "normal_cycles": 23,
    "anomaly_cycles": 5,
    "score_min": -0.053,
    "score_max": 0.100
  },
  "per_cycle_stats": {
    "heart_rate_bpm": {"mean": 73.2, "std": 4.3, "min": 55.7, "max": 78.7},
    "...": "..."
  },
  "hrv_metrics": {
    "heart_rate_mean_bpm": 73.2,
    "heart_rate_std_bpm": 4.3,
    "sdnn_ms": 57.5,
    "rmssd_ms": 88.6,
    "pnn50_pct": 35.7
  }
}
```

`hrv_metrics` is `null` if fewer than 3 valid RR intervals are found.

Private keys (`_s1_start`, etc.) in each cycle dict hold sample indices for span rendering. They are not included in the Isolation Forest feature matrix.

### 7.6 `murmur`

```json
{
  "analysis_range_hz": [0.0, 250.0],
  "systolic_murmur_cycles": 7,
  "diastolic_murmur_cycles": 8,
  "total_cycles": 28,
  "systolic_pct": 25.0,
  "diastolic_pct": 28.6,
  "assessment": "Some murmur-like activity detected (may be recording noise)",
  "cycle_results": [
    {
      "systolic_murmur": true,
      "diastolic_murmur": false,
      "systolic_grade": 1,
      "diastolic_grade": 0,
      "systolic_ratio": 0.19,
      "diastolic_ratio": 0.08,
      "sys_diamond": false
    }
  ]
}
```

`sys_diamond` and `dia_decrescendo` only appear when the respective segment is long enough to compute them.

### 7.7 `exports`

```json
{
  "saved_filtered_wav": "/path/to/recording_filtered.wav"
}
```

`null` when `save_filtered_wav=False`.

### 7.8 `signals` (optional, `include_signals=True` only)

```json
{
  "time_axis_s": [...],
  "raw": [...],
  "filtered": [...],
  "filtered_normalized": [...],
  "envelope": [...]
}
```

---

## 8) CLI usage

```bash
cd Analysis
python pcg_pipeline.py /path/to/recording.wav
python pcg_pipeline.py /path/to/recording.wav --save-filtered
python pcg_pipeline.py /path/to/recording.wav --include-signals
```

Output is printed as indented JSON to stdout.

---

## 9) WebUI integration

### Backend

1. Receive WAV upload, save to a temp file.
2. Call `run_pcg_pipeline(filename, include_signals=True)`.
3. Return JSON to frontend.

Use `include_signals=False` for a lightweight summary endpoint and `include_signals=True` for a full chart endpoint.

### Frontend chart layers

| Plot | x-axis | y / spans |
| --- | --- | --- |
| Waveform + envelope | `signals.time_axis_s` | `signals.filtered` and `signals.envelope` |
| S1/S2 markers | `peaks.s1_times_s`, `peaks.s2_times_s` | points on envelope |
| Segmentation | `segmentation.segments[i].start_s` → `end_s` | coloured spans: S1=red, Systole=orange, S2=blue, Diastole=green |
| Anomaly overlay | `cycles[i]._s1_start / sample_rate` → `_dia_end / sample_rate` | green (label=1) or red (label=-1) |
| Murmur overlay | `cycles[i]._sys_start / sample_rate` → `_sys_end / sample_rate` | red when `systolic_murmur=true` |
| Murmur overlay | `cycles[i]._dia_start / sample_rate` → `_dia_end / sample_rate` | purple when `diastolic_murmur=true` |

---

## 10) Performance guidance

Long recordings produce large signal arrays. Downsample only the waveform for display; keep all event data at full precision.

```python
import numpy as np

def decimate_xy(x, y, max_points=3000):
    n = len(x)
    if n <= max_points:
        return x, y
    idx = np.linspace(0, n - 1, max_points, dtype=int)
    return [x[i] for i in idx], [y[i] for i in idx]
```

Target 2 000–5 000 points per chart line. All segment/peak/label arrays can remain at full length since they are small.

---

## 11) Running the tests

```bash
cd Analysis
python -m unittest -v test_pcg_pipeline.py
```

---

## 12) Medical disclaimer

This pipeline is a **signal analysis** tool and is **not** a medical diagnostic device. Outputs must not be used as a substitute for clinical examination or professional medical advice.
