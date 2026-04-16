from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io.wavfile import write as wav_write

sys.path.append(str(Path(__file__).resolve().parent))
from pcg_pipeline import PCGConfig, run_pcg_pipeline


def _write_wav(path: Path, data: np.ndarray, sr: int = 500) -> Path:
    """Helper to save float signal as int16 WAV."""
    data = np.asarray(data, dtype=np.float64)
    scale = np.max(np.abs(data)) + 1e-10
    wav_i16 = (data / scale * 32767).astype(np.int16)
    wav_write(str(path), sr, wav_i16)
    return path


def _synthetic_pcg(sr: int = 500, duration_s: float = 10.0) -> np.ndarray:
    """Generate a simple synthetic PCG-like signal with repeated S1/S2 bursts."""
    t = np.linspace(0.0, duration_s, int(sr * duration_s), endpoint=False)
    sig = np.zeros_like(t)

    for c in np.arange(0.0, duration_s, 1.0):
        s1_center = c + 0.10
        s2_center = c + 0.45

        s1_env = np.exp(-0.5 * ((t - s1_center) / 0.018) ** 2)
        s1 = 0.9 * s1_env * np.sin(2 * np.pi * 60 * (t - s1_center))

        s2_env = np.exp(-0.5 * ((t - s2_center) / 0.014) ** 2)
        s2 = 0.7 * s2_env * np.sin(2 * np.pi * 90 * (t - s2_center))

        sig += s1 + s2

    rng = np.random.default_rng(42)
    sig += 0.02 * rng.standard_normal(len(t))
    return sig


class TestPCGPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_pipeline_returns_expected_top_level_structure(self) -> None:
        wav_path = _write_wav(self.tmp_path / "synthetic.wav", _synthetic_pcg())

        out = run_pcg_pipeline(filename=str(wav_path), include_signals=False)

        for key in [
            "file_info",
            "config",
            "peaks",
            "segmentation",
            "classification",
            "murmur",
            "exports",
            "medical_notice",
        ]:
            self.assertIn(key, out)

        self.assertEqual(out["file_info"]["sample_rate_hz"], 500)
        self.assertGreater(out["file_info"]["samples"], 0)
        self.assertIsInstance(out["classification"]["cycles"], list)
        self.assertEqual(out["murmur"]["total_cycles"], len(out["classification"]["cycles"]))

    def test_pipeline_can_export_filtered_wav_and_signals(self) -> None:
        wav_path = _write_wav(self.tmp_path / "synthetic_export.wav", _synthetic_pcg(duration_s=6.0))
        filtered_path = self.tmp_path / "synthetic_export_filtered.wav"

        out = run_pcg_pipeline(
            filename=str(wav_path),
            save_filtered_wav=True,
            output_filename=str(filtered_path),
            include_signals=True,
        )

        self.assertEqual(out["exports"]["saved_filtered_wav"], str(filtered_path))
        self.assertTrue(filtered_path.exists())

        signals = out["signals"]
        n = out["file_info"]["samples"]
        self.assertEqual(len(signals["time_axis_s"]), n)
        self.assertEqual(len(signals["raw"]), n)
        self.assertEqual(len(signals["filtered"]), n)
        self.assertEqual(len(signals["filtered_normalized"]), n)
        self.assertEqual(len(signals["envelope"]), n)

    def test_pipeline_handles_low_information_signal_without_crashing(self) -> None:
        sr = 500
        dur_s = 4.0
        silent = np.zeros(int(sr * dur_s), dtype=np.float64)
        wav_path = _write_wav(self.tmp_path / "silent.wav", silent, sr=sr)

        out = run_pcg_pipeline(filename=str(wav_path), include_signals=False)

        self.assertEqual(out["file_info"]["sample_rate_hz"], sr)
        self.assertEqual(out["classification"]["cycles"], [])
        self.assertEqual(out["murmur"]["total_cycles"], 0)
        self.assertIn("assessment", out["murmur"])

    def test_pipeline_respects_config_override(self) -> None:
        wav_path = _write_wav(self.tmp_path / "config_override.wav", _synthetic_pcg(duration_s=5.0))
        cfg = PCGConfig(lowcut=30.0, highcut=180.0, notch_freq=50.0)

        out = run_pcg_pipeline(filename=str(wav_path), config=cfg, include_signals=False)

        self.assertEqual(out["config"]["lowcut"], 30.0)
        self.assertEqual(out["config"]["highcut"], 180.0)
        self.assertEqual(out["config"]["notch_freq"], 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
