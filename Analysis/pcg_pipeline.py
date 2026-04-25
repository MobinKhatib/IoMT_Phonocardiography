from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pywt
import librosa
from scipy.io import wavfile
from scipy.io.wavfile import write as wav_write
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, filtfilt, find_peaks, hilbert, iirnotch, savgol_filter
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


@dataclass
class PCGConfig:
    lowcut: float = 25.0
    highcut: float = 200.0
    notch_freqs: Tuple[float, ...] = (50.0, 100.0, 150.0)
    notch_q: float = 35.0
    filter_order: int = 4
    wavelet: str = "db6"
    wavelet_level: int = 4
    envelope_cutoff: float = 8.0
    min_peak_dist: float = 0.25
    bpm_min: int = 40
    bpm_max: int = 200

    normal_ranges: Dict[str, Tuple[float, float]] = None
    murmur_grade_thresholds: List[float] = None

    def __post_init__(self) -> None:
        if self.normal_ranges is None:
            self.normal_ranges = {
                "s1_duration_ms": (50, 200),
                "s2_duration_ms": (40, 150),
                "systolic_ms": (150, 450),
                "diastolic_ms": (150, 1200),
                "s1_s2_amp_ratio": (0.5, 3.0),
                "heart_rate_bpm": (45, 180),
            }
        if self.murmur_grade_thresholds is None:
            self.murmur_grade_thresholds = [0.15, 0.30, 0.50, 0.70, 0.90]


def multi_notch_filter(data: np.ndarray, fs: float, freqs: Tuple[float, ...] = (50.0, 100.0, 150.0), q: float = 35.0) -> np.ndarray:
    """Apply narrow notch filters at each frequency to remove powerline hum and harmonics."""
    filtered = data.copy().astype(float)
    nyq = 0.5 * fs
    for f in freqs:
        if f < nyq - 1.0:
            b, a = iirnotch(f / nyq, q)
            filtered = filtfilt(b, a, filtered)
    return filtered


def bandpass_filter(data: np.ndarray, lowcut: float, highcut: float, fs: float, order: int = 4) -> np.ndarray:
    """Zero-phase Butterworth bandpass."""
    nyq = 0.5 * fs
    highcut = min(highcut, nyq - 1.0)
    b, a = butter(order, [lowcut / nyq, highcut / nyq], btype="band")
    return filtfilt(b, a, data)


def lowpass_filter(data: np.ndarray, cutoff: float, fs: float, order: int = 2) -> np.ndarray:
    """Zero-phase Butterworth lowpass."""
    nyq = 0.5 * fs
    b, a = butter(order, cutoff / nyq, btype="low")
    return filtfilt(b, a, data)


def wavelet_denoise(data: np.ndarray, wavelet: str = "db6", level: int = 4, thresh_scale: float = 0.6) -> np.ndarray:
    """DWT soft-thresholding; thresh_scale < 1 preserves more signal detail."""
    coeffs = pywt.wavedec(data, wavelet, level=level)
    sigma = np.median(np.abs(coeffs[-1])) / 0.6745
    uthresh = thresh_scale * sigma * np.sqrt(2 * np.log(len(data)))
    denoised = [coeffs[0]] + [pywt.threshold(c, value=uthresh, mode="soft") for c in coeffs[1:]]
    return pywt.waverec(denoised, wavelet)


def shannon_envelope(signal: np.ndarray, sr: int, cutoff: float = 8.0) -> np.ndarray:
    """
    Shannon energy envelope: -x² · log(x²)
    Better S1/S2 contrast than simple squaring.
    """
    norm = signal / (np.max(np.abs(signal)) + 1e-10)
    eps = 1e-10
    se = -norm**2 * np.log(norm**2 + eps)
    env = lowpass_filter(se, cutoff, sr)
    return np.maximum(env, 0)


def estimate_sound_width(envelope: np.ndarray, peak_idx: int, sr: int, max_width_s: float = 0.15) -> Tuple[int, int]:
    """Find heart sound boundaries at 40% of peak height."""
    half_height = envelope[peak_idx] * 0.4
    max_w = int(sr * max_width_s)

    left = peak_idx
    for j in range(peak_idx, max(0, peak_idx - max_w), -1):
        if envelope[j] < half_height:
            left = j
            break

    right = peak_idx
    for j in range(peak_idx, min(len(envelope), peak_idx + max_w)):
        if envelope[j] < half_height:
            right = j
            break

    return left, right


def segment_heart_sounds(
    filtered: np.ndarray,
    envelope: np.ndarray,
    s1_peaks: np.ndarray,
    s2_peaks: np.ndarray,
    sr: int,
) -> Tuple[np.ndarray, List[Tuple[str, int, int]]]:
    """Create per-sample state labels: 0=S1, 1=Systole, 2=S2, 3=Diastole."""
    n = len(filtered)
    states = np.full(n, 3, dtype=int)

    s1_bounds = []
    for pk in s1_peaks:
        left, right = estimate_sound_width(envelope, int(pk), sr, 0.08)
        states[left:right] = 0
        s1_bounds.append((left, right))

    s2_bounds = []
    for pk in s2_peaks:
        left, right = estimate_sound_width(envelope, int(pk), sr, 0.07)
        states[left:right] = 2
        s2_bounds.append((left, right))

    for _, s1_end in s1_bounds:
        next_s2 = [s2_l for s2_l, _ in s2_bounds if s2_l > s1_end]
        if next_s2 and (next_s2[0] - s1_end) / sr < 0.40:
            states[s1_end:next_s2[0]] = 1

    for _, s2_end in s2_bounds:
        next_s1 = [s1_l for s1_l, _ in s1_bounds if s1_l > s2_end]
        if next_s1:
            states[s2_end:next_s1[0]] = 3

    state_names = ["S1", "Systole", "S2", "Diastole"]
    segments = []
    i = 0
    while i < n:
        j = i
        while j < n and states[j] == states[i]:
            j += 1
        segments.append((state_names[int(states[i])], i, j))
        i = j

    return states, segments


def spectral_centroid(x: np.ndarray, sr: int) -> float:
    if len(x) < 8:
        return 0.0
    mag = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    return float(np.sum(freqs * mag) / (np.sum(mag) + 1e-10))


def zero_crossing_rate(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    return float(np.sum(np.diff(np.sign(x)) != 0) / len(x))


def excess_kurtosis(x: np.ndarray) -> float:
    if len(x) < 4:
        return 0.0
    m, s = np.mean(x), np.std(x)
    if s < 1e-10:
        return 0.0
    return float(np.mean(((x - m) / s) ** 4) - 3)


def extract_cycle_features(filtered: np.ndarray, segments: List[Tuple[str, int, int]], sr: int) -> List[Dict[str, float]]:
    """Extract features for each complete S1→Systole→S2→Diastole cycle."""
    cycles = []
    i = 0
    while i + 3 < len(segments):
        if (
            segments[i][0] == "S1"
            and segments[i + 1][0] == "Systole"
            and segments[i + 2][0] == "S2"
            and segments[i + 3][0] == "Diastole"
        ):
            s1_start, s1_end = segments[i][1], segments[i][2]
            sys_start, sys_end = segments[i + 1][1], segments[i + 1][2]
            s2_start, s2_end = segments[i + 2][1], segments[i + 2][2]
            dia_start, dia_end = segments[i + 3][1], segments[i + 3][2]

            s1_sig = filtered[s1_start:s1_end]
            sys_sig = filtered[sys_start:sys_end]
            s2_sig = filtered[s2_start:s2_end]
            dia_sig = filtered[dia_start:dia_end]
            full = filtered[s1_start:dia_end]

            if len(s1_sig) < 3 or len(s2_sig) < 3 or len(full) < 10:
                i += 1
                continue

            s1_rms = np.sqrt(np.mean(s1_sig**2))
            s2_rms = np.sqrt(np.mean(s2_sig**2))
            sys_rms = np.sqrt(np.mean(sys_sig**2)) if len(sys_sig) > 0 else 0
            dia_rms = np.sqrt(np.mean(dia_sig**2)) if len(dia_sig) > 0 else 0

            s1_dur = len(s1_sig) / sr * 1000
            s2_dur = len(s2_sig) / sr * 1000
            systolic = (s2_start - s1_start) / sr * 1000
            diastolic = (dia_end - s2_start) / sr * 1000
            cycle_dur = (dia_end - s1_start) / sr * 1000
            hr = 60000.0 / cycle_dur if cycle_dur > 0 else 0

            nfft = min(64, len(full))
            if nfft >= 16:
                try:
                    mfccs = librosa.feature.mfcc(y=full, sr=sr, n_mfcc=8, n_fft=nfft)
                    mfcc_means = np.mean(mfccs, axis=1)
                except Exception:
                    # Graceful fallback for environments with librosa/numba incompatibilities.
                    # Keeps the rest of the pipeline and output schema intact.
                    mfcc_means = np.zeros(8)
            else:
                mfcc_means = np.zeros(8)

            cycle = {
                "s1_duration_ms": s1_dur,
                "s2_duration_ms": s2_dur,
                "systolic_ms": systolic,
                "diastolic_ms": diastolic,
                "cycle_duration_ms": cycle_dur,
                "heart_rate_bpm": hr,
                "sd_ratio": systolic / (diastolic + 1e-10),
                "s1_rms": s1_rms,
                "s2_rms": s2_rms,
                "s1_s2_amp_ratio": s1_rms / (s2_rms + 1e-10),
                "energy_concentration": (np.sum(s1_sig**2) + np.sum(s2_sig**2)) / (np.sum(full**2) + 1e-10),
                "s1_zcr": zero_crossing_rate(s1_sig),
                "s2_zcr": zero_crossing_rate(s2_sig),
                "s1_kurtosis": excess_kurtosis(s1_sig),
                "s2_kurtosis": excess_kurtosis(s2_sig),
                "s1_centroid": spectral_centroid(s1_sig, sr),
                "s2_centroid": spectral_centroid(s2_sig, sr),
                "sys_noise_ratio": sys_rms / (s1_rms + 1e-10),
                "dia_noise_ratio": dia_rms / (s1_rms + 1e-10),
                **{f"mfcc_{j}": v for j, v in enumerate(mfcc_means)},
                "_s1_start": s1_start,
                "_s1_end": s1_end,
                "_sys_start": sys_start,
                "_sys_end": sys_end,
                "_s2_start": s2_start,
                "_s2_end": s2_end,
                "_dia_start": dia_start,
                "_dia_end": dia_end,
            }
            cycles.append(cycle)
            i += 4
        else:
            i += 1
    return cycles


def murmur_grade(ratio: float, thresholds: List[float]) -> int:
    """Convert energy ratio to Levine-style grade (0–6)."""
    for i, th in enumerate(thresholds):
        if ratio < th:
            return i
    return 6


def detect_murmur(
    filtered: np.ndarray,
    cycle: Dict[str, float],
    sr: int,
    thresholds: List[float],
) -> Dict[str, Any]:
    """Analyze one cardiac cycle for murmur presence."""
    sys_sig = filtered[int(cycle["_sys_start"]):int(cycle["_sys_end"])]
    dia_sig = filtered[int(cycle["_dia_start"]):int(cycle["_dia_end"])]
    s1_rms = cycle["s1_rms"]

    result: Dict[str, Any] = {
        "systolic_murmur": False,
        "diastolic_murmur": False,
        "systolic_grade": 0,
        "diastolic_grade": 0,
        "systolic_ratio": 0.0,
        "diastolic_ratio": 0.0,
    }

    if len(sys_sig) > 4:
        r = np.sqrt(np.mean(sys_sig**2)) / (s1_rms + 1e-10)
        result["systolic_ratio"] = r
        result["systolic_grade"] = murmur_grade(r, thresholds)
        result["systolic_murmur"] = r > thresholds[0]
        if len(sys_sig) > 15:
            env = uniform_filter1d(np.abs(hilbert(sys_sig)), max(3, len(sys_sig) // 5))
            pk_pos = np.argmax(env) / len(env)
            result["sys_diamond"] = 0.25 < pk_pos < 0.75

    if len(dia_sig) > 4:
        r = np.sqrt(np.mean(dia_sig**2)) / (s1_rms + 1e-10)
        result["diastolic_ratio"] = r
        result["diastolic_grade"] = murmur_grade(r, thresholds)
        result["diastolic_murmur"] = r > thresholds[0]
        if len(dia_sig) > 15:
            env = uniform_filter1d(np.abs(hilbert(dia_sig)), max(3, len(dia_sig) // 5))
            q1 = np.mean(env[: len(env) // 4])
            q4 = np.mean(env[3 * len(env) // 4 :])
            result["dia_decrescendo"] = q1 > 1.5 * q4 if q4 > 0 else False

    return result


def _to_builtin(value: Any) -> Any:
    """Convert numpy-heavy structures into plain Python for JSON/web responses."""
    if isinstance(value, dict):
        return {k: _to_builtin(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_builtin(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_to_builtin(v) for v in value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.float32, np.float64)):
        return float(value)
    if isinstance(value, (np.integer, np.int32, np.int64)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def run_pcg_pipeline(
    filename: str,
    config: Optional[PCGConfig] = None,
    save_filtered_wav: bool = False,
    output_filename: Optional[str] = None,
    include_signals: bool = False,
) -> Dict[str, Any]:
    """
    Full pipeline as one function for backend/web UI use.

    Returns a dictionary with extracted features, segmentation, classification,
    murmur analysis, and summary metrics.
    """
    cfg = config or PCGConfig()

    sample_rate, raw_data = wavfile.read(filename)
    if len(raw_data.shape) > 1:
        raw_data = raw_data[:, 0]

    data = raw_data.astype(np.float64)
    data = data - np.mean(data)
    n_samples = len(data)
    duration = n_samples / sample_rate
    nyq = sample_rate / 2.0
    time_axis = np.linspace(0, duration, n_samples, endpoint=False)

    data_notched = multi_notch_filter(data, sample_rate, cfg.notch_freqs, cfg.notch_q)
    data_bp = bandpass_filter(data_notched, cfg.lowcut, cfg.highcut, sample_rate, cfg.filter_order)
    data_denoised = wavelet_denoise(data_bp, cfg.wavelet, cfg.wavelet_level, thresh_scale=0.6)[:n_samples]
    filtered = savgol_filter(data_denoised, window_length=11, polyorder=3)

    envelope = shannon_envelope(filtered, sample_rate, cfg.envelope_cutoff)

    threshold = np.percentile(envelope, 75)
    peaks, _ = find_peaks(
        envelope,
        height=threshold,
        distance=int(sample_rate * cfg.min_peak_dist),
        prominence=threshold * 0.3,
    )

    s1_peaks: List[int] = []
    s2_peaks: List[int] = []
    if len(peaks) >= 3:
        pt = peaks / sample_rate
        for i in range(len(peaks) - 2):
            i1 = pt[i + 1] - pt[i]
            i2 = pt[i + 2] - pt[i + 1]
            if i1 < i2:
                if int(peaks[i]) not in s1_peaks:
                    s1_peaks.append(int(peaks[i]))
                if int(peaks[i + 1]) not in s2_peaks:
                    s2_peaks.append(int(peaks[i + 1]))
            else:
                if int(peaks[i]) not in s2_peaks:
                    s2_peaks.append(int(peaks[i]))
                if int(peaks[i + 1]) not in s1_peaks:
                    s1_peaks.append(int(peaks[i + 1]))

    s1_peaks_np = np.array(sorted(set(s1_peaks)))
    s2_peaks_np = np.array(sorted(set(s2_peaks)))

    states, segments = segment_heart_sounds(filtered, envelope, s1_peaks_np, s2_peaks_np, sample_rate)

    state_names = ["S1", "Systole", "S2", "Diastole"]
    segmentation_stats: Dict[str, Dict[str, float]] = {}
    for sn in state_names:
        durs = [(e - s) / sample_rate * 1000 for name, s, e in segments if name == sn]
        if durs:
            segmentation_stats[sn] = {
                "count": len(durs),
                "mean_ms": float(np.mean(durs)),
                "std_ms": float(np.std(durs)),
                "min_ms": float(np.min(durs)),
                "max_ms": float(np.max(durs)),
            }

    cycles = extract_cycle_features(filtered, segments, sample_rate)

    flagged: List[Dict[str, Any]] = []
    for ci, c in enumerate(cycles):
        violations: List[str] = []
        for key, (lo, hi) in cfg.normal_ranges.items():
            if key in c:
                v = c[key]
                if v < lo:
                    violations.append(f"{key}={v:.1f} < {lo}")
                elif v > hi:
                    violations.append(f"{key}={v:.1f} > {hi}")
        if violations:
            flagged.append({"cycle_index": ci, "violations": violations})

    feat_keys: List[str] = []
    labels: np.ndarray = np.array([])
    scores: np.ndarray = np.array([])
    anomaly_summary = {
        "normal_cycles": 0,
        "anomaly_cycles": 0,
        "score_min": None,
        "score_max": None,
    }

    if len(cycles) > 0:
        feat_keys = [k for k in cycles[0] if not k.startswith("_")]
        x = np.array([[c[k] for k in feat_keys] for c in cycles])
        x = np.nan_to_num(x)

        scaler = StandardScaler()
        x_scaled = scaler.fit_transform(x)

        iso_forest = IsolationForest(contamination=0.15, random_state=42, n_estimators=200)
        labels = iso_forest.fit_predict(x_scaled)
        scores = iso_forest.decision_function(x_scaled)

        anomaly_summary = {
            "normal_cycles": int(np.sum(labels == 1)),
            "anomaly_cycles": int(np.sum(labels == -1)),
            "score_min": float(scores.min()),
            "score_max": float(scores.max()),
        }

    per_cycle_stats: Dict[str, Dict[str, float]] = {}
    stat_keys = [
        "heart_rate_bpm",
        "s1_duration_ms",
        "s2_duration_ms",
        "systolic_ms",
        "diastolic_ms",
        "s1_s2_amp_ratio",
        "sd_ratio",
    ]
    if len(cycles) > 0:
        for key in stat_keys:
            vals = np.array([c[key] for c in cycles])
            per_cycle_stats[key] = {
                "mean": float(np.mean(vals)),
                "std": float(np.std(vals)),
                "min": float(np.min(vals)),
                "max": float(np.max(vals)),
            }

    hrv_metrics: Optional[Dict[str, float]] = None
    if len(cycles) > 0:
        rr = np.array([c["cycle_duration_ms"] for c in cycles])
        valid_rr = rr[(rr > 300) & (rr < 1500)]
        if len(valid_rr) > 2:
            sdnn = np.std(valid_rr)
            rmssd = np.sqrt(np.mean(np.diff(valid_rr) ** 2))
            nn50 = np.sum(np.abs(np.diff(valid_rr)) > 50)
            pnn50 = 100 * nn50 / len(valid_rr)
            hr_vals = 60000 / valid_rr
            hrv_metrics = {
                "heart_rate_mean_bpm": float(np.mean(hr_vals)),
                "heart_rate_std_bpm": float(np.std(hr_vals)),
                "sdnn_ms": float(sdnn),
                "rmssd_ms": float(rmssd),
                "pnn50_pct": float(pnn50),
            }

    murmur_results = [detect_murmur(filtered, c, sample_rate, cfg.murmur_grade_thresholds) for c in cycles]
    sys_m = sum(1 for r in murmur_results if r["systolic_murmur"])
    dia_m = sum(1 for r in murmur_results if r["diastolic_murmur"])
    total = len(murmur_results)

    sys_pct = (100 * sys_m / total) if total > 0 else 0.0
    dia_pct = (100 * dia_m / total) if total > 0 else 0.0

    if total == 0:
        murmur_assessment = "No complete cycles available for murmur assessment"
    elif sys_pct > 50 or dia_pct > 50:
        murmur_assessment = f"Significant murmur activity (Sys: {sys_pct:.0f}%, Dia: {dia_pct:.0f}%)"
    elif sys_pct > 20 or dia_pct > 20:
        murmur_assessment = "Some murmur-like activity detected (may be recording noise)"
    else:
        murmur_assessment = "No significant murmur activity"

    saved_filtered_wav: Optional[str] = None
    f_norm = filtered / (np.max(np.abs(filtered)) + 1e-10)
    if save_filtered_wav:
        if output_filename is None:
            output_filename = str(Path(filename).with_name(Path(filename).stem + "_filtered.wav"))
        wav_write(output_filename, sample_rate, (f_norm * 32767).astype(np.int16))
        saved_filtered_wav = output_filename

    result: Dict[str, Any] = {
        "file_info": {
            "filename": filename,
            "sample_rate_hz": sample_rate,
            "nyquist_hz": nyq,
            "duration_s": duration,
            "samples": n_samples,
            "highcut_clamped": bool(cfg.highcut >= nyq),
            "effective_highcut_hz": min(cfg.highcut, nyq - 1.0),
        },
        "config": asdict(cfg),
        "peaks": {
            "total_peaks": int(len(peaks)),
            "s1_count": int(len(s1_peaks_np)),
            "s2_count": int(len(s2_peaks_np)),
            "peak_indices": peaks,
            "s1_indices": s1_peaks_np,
            "s2_indices": s2_peaks_np,
            "peak_times_s": peaks / sample_rate,
            "s1_times_s": s1_peaks_np / sample_rate,
            "s2_times_s": s2_peaks_np / sample_rate,
            "threshold": float(threshold),
        },
        "segmentation": {
            "state_names": state_names,
            "states": states,
            "segments": [
                {
                    "state": sn,
                    "start_index": s,
                    "end_index": e,
                    "start_s": s / sample_rate,
                    "end_s": e / sample_rate,
                }
                for sn, s, e in segments
            ],
            "stats": segmentation_stats,
        },
        "classification": {
            "cycles": cycles,
            "rule_based": {
                "normal_cycles": len(cycles) - len(flagged),
                "flagged_cycles": len(flagged),
                "flagged_details": flagged,
            },
            "isolation_forest": {
                "feature_keys": feat_keys,
                "labels": labels,
                "scores": scores,
                **anomaly_summary,
            },
            "per_cycle_stats": per_cycle_stats,
            "hrv_metrics": hrv_metrics,
        },
        "murmur": {
            "analysis_range_hz": [0.0, nyq],
            "systolic_murmur_cycles": sys_m,
            "diastolic_murmur_cycles": dia_m,
            "total_cycles": total,
            "systolic_pct": sys_pct,
            "diastolic_pct": dia_pct,
            "assessment": murmur_assessment,
            "cycle_results": murmur_results,
        },
        "exports": {
            "saved_filtered_wav": saved_filtered_wav,
        },
    }

    if include_signals:
        result["signals"] = {
            "time_axis_s": time_axis,
            "raw": raw_data,
            "filtered": filtered,
            "filtered_normalized": f_norm,
            "envelope": envelope,
        }

    return _to_builtin(result)


def run_pipeline(filename: str, **kwargs: Any) -> Dict[str, Any]:
    """Alias for easier integration."""
    return run_pcg_pipeline(filename=filename, **kwargs)


if __name__ == "__main__":
    import json
    import argparse

    parser = argparse.ArgumentParser(description="Run PCG analysis pipeline")
    parser.add_argument("filename", help="Path to input WAV file")
    parser.add_argument("--save-filtered", action="store_true", help="Export filtered WAV")
    parser.add_argument("--include-signals", action="store_true", help="Include raw/filtered arrays in output JSON")
    args = parser.parse_args()

    output = run_pcg_pipeline(
        filename=args.filename,
        save_filtered_wav=args.save_filtered,
        include_signals=args.include_signals,
    )
    print(json.dumps(output, indent=2))
