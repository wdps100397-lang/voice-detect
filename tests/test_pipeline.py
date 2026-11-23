import tempfile
from pathlib import Path
import unittest

import numpy as np
from scipy.io import wavfile

from src.common import save_json
from src.localization import calibration, direction, locate
from src.speakers import SpeakerEngine, speech_audio, filterbank


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.rate = 48000
        self.positions = np.array([[-.1, -.057735], [.1, -.057735], [0, .11547]])

    def signals(self, angle, seconds=1, offsets=None, drifts=None):
        rate = self.rate
        times = np.arange(int(seconds * rate)) / rate
        source_times = np.arange(-rate, int((seconds + 1) * rate)) / rate
        noise = np.random.default_rng(42).normal(0, .05, len(source_times))
        vector = np.array([-np.sin(np.deg2rad(angle)), np.cos(np.deg2rad(angle))])
        arrivals = -(self.positions @ vector) / 343
        offsets = np.zeros(3) if offsets is None else offsets
        drifts = np.zeros(3) if drifts is None else drifts
        return [np.interp(times - delay - offset - drift * times, source_times, noise)
                for delay, offset, drift in zip(arrivals, offsets, drifts)]

    def test_direction_all_quadrants(self):
        for angle in (0, 37, 90, 155, -180, -109, -90, -1):
            with self.subTest(angle=angle):
                result = direction(self.signals(angle), self.rate, self.positions)
                self.assertEqual(result["status"], "ok")
                self.assertLessEqual(abs((result["azimuth_deg"] - angle + 180) % 360 - 180), 2)

    def test_silence_has_no_direction(self):
        result = direction([np.zeros(self.rate)] * 3, self.rate, self.positions)
        self.assertIsNone(result["azimuth_deg"])

    def test_calibration_through_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            files = [folder / f"{label}.wav" for label in "ABC"]
            offsets = [0, .013, -.008]
            # source at forward direction, known unequal path lengths
            drifts = [0, 50e-6, -35e-6]
            for path, audio in zip(files, self.signals(0, 10, offsets, drifts)):
                wavfile.write(path, self.rate, audio.astype(np.float32))
            distances = 2 - self.positions[:, 1]
            clock = folder / "clock.json"
            result = calibration(files, 1, 7, distances.tolist(), clock)
            self.assertAlmostEqual(result["channels"][1]["offset_seconds"], .013, delta=.00005)
            self.assertAlmostEqual(result["channels"][1]["drift"], 50e-6, delta=10e-6)
            config = folder / "array.json"
            save_json(config, dict(geometry_measured=True, positions_m=self.positions.tolist()))
            prediction = locate(files, config, clock, 7, 9)
            self.assertEqual(prediction["status"], "ok")
            self.assertLessEqual(abs((prediction["azimuth_deg"] + 180) % 360 - 180), 2)
            files[0].write_bytes(files[0].read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "another recording"):
                locate(files, config, clock, 7, 9)

    def test_vad_rejects_silence(self):
        with self.assertRaises(ValueError):
            speech_audio(np.zeros(16000 * 3))

    def test_actual_onnx_runtime_with_synthetic_features(self):
        engine = SpeakerEngine()
        noise = np.random.default_rng(17).normal(0, .03, 16000 * 3)
        features = filterbank(noise)
        self.assertEqual(features.shape[1], 80)
        output = engine.session.run(None, {engine.session.get_inputs()[0].name: features[None]})[0]
        self.assertEqual(output.shape, (1, 256))
        self.assertTrue(np.isfinite(output).all())
        self.assertGreater(np.linalg.norm(output), 0)

    def test_speaker_matching_and_unknown(self):
        # Test database/threshold behavior independently from speech accuracy.
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "speakers.json"
            engine = SpeakerEngine.__new__(SpeakerEngine)
            engine.model_hash = "test"
            engine.embedding = lambda path: np.array([1., 0.])
            save_json(database, dict(schema=1, model_sha256="test", speakers={
                "A": dict(template=[1., 0.]), "B": dict(template=[0., 1.])}))
            self.assertEqual(engine.identify("test", database, .65, .08)["user_id"], "A")
            engine.embedding = lambda path: np.array([-.8, -.6])
            self.assertEqual(engine.identify("test", database, .65, .08)["status"], "unknown")
            engine.embedding = lambda path: np.array([.7071, .7071])
            self.assertEqual(engine.identify("test", database, .65, .08)["status"], "unknown")

    def test_silent_file_rejected_without_enrollment(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            first, second = folder / "first.wav", folder / "second.wav"
            wavfile.write(first, 48000, np.zeros(48000 * 3, dtype=np.int16))
            wavfile.write(second, 48000, np.zeros(48000 * 4, dtype=np.int16))
            database = folder / "speakers.json"
            engine = SpeakerEngine()
            with self.assertRaisesRegex(ValueError, "detected speech"):
                engine.enroll("silence", [first, second], database)
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
