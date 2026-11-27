"""Exercise the real CLI with generated sound and fictional STT text."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from scipy.io import wavfile

from src.common import ROOT, fingerprint, read_json, save_json


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "voice_app.py"), *map(str, arguments)],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf8", timeout=60)

    def test_synthetic_recording_to_native_files(self):
        self.synthetic_case(3)

    def test_four_microphone_cli(self):
        self.synthetic_case(4)

    def synthetic_case(self, count):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rate = 16000
            positions = np.array([[-.1, -.057735], [.1, -.057735], [0, .11547], [-.1, .11547]])[:count]
            # A source 45 degrees to the left, in Project A's native convention.
            vector = np.array([-np.sin(np.pi / 4), np.cos(np.pi / 4)])
            arrival = -(positions @ vector) / 343
            distances = 2 - positions @ vector
            expected_azimuth = 45
            geometry = dict(geometry_measured=True, positions_m=positions.tolist())
            if count == 4:
                points_mm = np.array([[500, -350, 800], [700, -350, 800],
                                      [700, -150, 800], [500, -150, 900]])
                calibration_source_mm = np.array([1250, -200, 1200])
                expected_azimuth = -32.4
                azimuth = np.deg2rad(expected_azimuth)
                elevation = np.deg2rad(45)
                vector = np.array([np.cos(azimuth) * np.cos(elevation),
                                   np.sin(azimuth) * np.cos(elevation), np.sin(elevation)])
                arrival = -((points_mm - points_mm.mean(axis=0)) / 1000 @ vector) / 343
                distances = np.linalg.norm((points_mm - calibration_source_mm) / 1000, axis=1)
                geometry = dict(geometry_measured=True, robot_base_positions_mm=points_mm.tolist(),
                                robot_base_calibration_source_mm=calibration_source_mm.tolist())
            source = np.random.default_rng(73).normal(0, .03, rate * 10)
            times = np.arange(rate * 8) / rate
            source_times = np.arange(len(source)) / rate - 1
            files = [folder / f"{label}.wav" for label in "ABCD"[:count]]
            for path, delay, distance in zip(files, arrival, distances):
                delays = np.where(times < 5, (distance - 2) / 343, delay)
                samples = np.interp(times - delays, source_times, source)
                wavfile.write(path, rate, samples.astype("float32"))
            config = folder / "array.json"
            save_json(config, geometry)
            clock = folder / "clock.json"
            calibration_args = ["--config", config] if count == 4 else ["--distances", *distances]
            calibrated = self.run_cli("calibrate", "--files", *files, "--start", 1, "--end", 5,
                                      *calibration_args, "--output", clock)
            self.assertEqual(calibrated.returncode, 0, calibrated.stderr)
            database = folder / "speakers.json"
            save_json(database, dict(schema=1, model_sha256=fingerprint(ROOT / "models" / "voxceleb_resnet34.onnx"),
                                    speakers={"test_user": dict(template=[1.] + [0.] * 255)}))
            stt = folder / "stt.json"
            save_json(stt, dict(utterance_id="synthetic-cli-001", text="這是一句測試文字"))
            analysis, identity, text_input = [folder / name for name in ("analysis.json", "identity.json", "input.json")]
            arguments = ["analyze", "--files", *files, "--config", config, "--calibration", clock,
                         "--start", 5, "--end", 7, "--database", database, "--threshold", 1,
                         "--utterance-id", "synthetic-cli-001", "--stt-file", stt,
                         "--observed-at", 1000, "--output", analysis,
                         "--identity-output", identity, "--input-output", text_input,
                         "--no-speaker-recognition"]
            analyzed = self.run_cli(*arguments)
            self.assertEqual(analyzed.returncode, 0, analyzed.stderr)
            observation = read_json(identity)
            self.assertAlmostEqual(observation["azimuth_deg"], expected_azimuth, delta=2)
            self.assertEqual(observation["source"], "voice_detect")
            self.assertEqual(observation["user_id"], "")
            self.assertEqual(set(observation), {"user_id", "location", "azimuth_deg", "timestamp", "source"})
            self.assertEqual(read_json(text_input), dict(utterance_id="synthetic-cli-001",
                                                         text="這是一句測試文字", user_id=""))
            direction = read_json(analysis)["diagnostics"]["direction"]
            self.assertEqual(direction["status"], "ok")
            if count == 4:
                self.assertAlmostEqual(direction["elevation_deg"], 45, delta=2)
                self.assertAlmostEqual(direction["robot_yaw_deg"], expected_azimuth, delta=2)
                np.testing.assert_allclose(direction["robot_ray_origin_m"], [.6, -.25, .825])
            save_json(stt, dict(utterance_id="different-event", text="另一段文字"))
            rejected = self.run_cli(*arguments)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("utterance_id must match", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
