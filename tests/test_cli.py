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
            source = np.random.default_rng(73).normal(0, .03, rate * 10)
            times = np.arange(rate * 8) / rate
            source_times = np.arange(len(source)) / rate - 1
            files = [folder / f"{label}.wav" for label in "ABCD"[:count]]
            for path, delay in zip(files, arrival):
                samples = np.interp(times - delay, source_times, source)
                wavfile.write(path, rate, samples.astype("float32"))
            config = folder / "array.json"
            save_json(config, dict(geometry_measured=True, positions_m=positions.tolist()))
            clock = folder / "clock.json"
            distances = 2 - positions @ vector
            calibrated = self.run_cli("calibrate", "--files", *files, "--start", 1, "--end", 5,
                                      "--distances", *distances, "--output", clock)
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
            self.assertLessEqual(abs(observation["azimuth_deg"] - 45), 2)
            self.assertEqual(observation["source"], "voice_detect")
            self.assertEqual(observation["user_id"], "")
            self.assertEqual(set(observation), {"user_id", "location", "azimuth_deg", "timestamp", "source"})
            self.assertEqual(read_json(text_input), dict(utterance_id="synthetic-cli-001",
                                                         text="這是一句測試文字", user_id=""))
            self.assertEqual(read_json(analysis)["diagnostics"]["direction"]["status"], "ok")
            save_json(stt, dict(utterance_id="different-event", text="另一段文字"))
            rejected = self.run_cli(*arguments)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("utterance_id must match", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
