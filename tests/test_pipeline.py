import tempfile
from pathlib import Path
import unittest

import numpy as np
from scipy.io import wavfile

from src.common import save_json
from src.localization import align_tracks, calibration, direction, locate, fit_clock
from src.speakers import SpeakerEngine, speech_audio, filterbank


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.rate = 48000
        self.positions = np.array([[-.1, -.057735], [.1, -.057735], [0, .11547]])

    def signals(self, angle, seconds=1, offsets=None, drifts=None, *, elevation=0):
        rate = self.rate
        times = np.arange(int(seconds * rate)) / rate
        source_times = np.arange(-rate, int((seconds + 1) * rate)) / rate
        noise = np.random.default_rng(42).normal(0, .05, len(source_times))
        azimuth, elevation = np.deg2rad([angle, elevation])
        vector = np.array([-np.sin(azimuth) * np.cos(elevation),
                           np.cos(azimuth) * np.cos(elevation), np.sin(elevation)])
        arrivals = -(self.positions @ vector[:self.positions.shape[1]]) / 343
        offsets = np.zeros(len(self.positions)) if offsets is None else offsets
        drifts = np.zeros(len(self.positions)) if drifts is None else drifts
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

    def test_level_array_reports_azimuth_with_elevation_and_mirror_ambiguity(self):
        self.positions = np.array([[-.1, -.1, 0], [.1, -.1, 0], [.1, .1, 0], [-.1, .1, 0]])
        for elevation in (25, 55, -55):
            with self.subTest(elevation=elevation):
                result = direction(self.signals(38.4, elevation=elevation), self.rate, self.positions)
                self.assertEqual(result["status"], "ok")
                self.assertAlmostEqual(result["azimuth_deg"], 38.4, delta=1)
                self.assertAlmostEqual(result["absolute_elevation_deg"], abs(elevation), delta=2)
                self.assertIsNone(result["elevation_deg"])

    def test_raised_microphone_resolves_signed_elevation(self):
        self.positions = np.array([[-.1, -.1, 0], [.1, -.1, 0], [.1, .1, 0], [-.1, .1, .1]])
        for elevation in (45, -45):
            with self.subTest(elevation=elevation):
                result = direction(self.signals(-127.4, elevation=elevation), self.rate, self.positions)
                self.assertEqual(result["status"], "ok")
                self.assertAlmostEqual(result["azimuth_deg"], -127.4, delta=1)
                self.assertAlmostEqual(result["elevation_deg"], elevation, delta=2)

    def test_no_horizontal_angle_for_a_source_overhead(self):
        self.positions = np.array([[-.1, -.1, 0], [.1, -.1, 0], [.1, .1, 0], [-.1, .1, .1]])
        result = direction(self.signals(38.4, elevation=90), self.rate, self.positions)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["azimuth_deg"])

    def test_tilted_plane_with_distinct_mirror_azimuths_is_ambiguous(self):
        self.positions = np.array([[-.1, -.1, -.05], [.1, -.1, .05], [.1, .1, .05], [-.1, .1, -.05]])
        result = direction(self.signals(38.4, elevation=45), self.rate, self.positions)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["azimuth_deg"])

    def test_four_microphone_clock_drift(self):
        self.rate = 16000
        self.positions = np.vstack([self.positions, [-.1, .11547]])
        offsets = [0, .013, -.008, .02]
        drifts = [0, 50e-6, -35e-6, 25e-6]
        tracks = self.signals(0, 8, offsets, drifts)
        result = fit_clock(tracks, self.rate, 1, 7, (2 - self.positions[:, 1]).tolist())
        self.assertEqual(len(result["channels"]), 4)
        self.assertAlmostEqual(result["channels"][3]["offset_seconds"], .02, delta=.00005)
        self.assertAlmostEqual(result["channels"][3]["drift"], 25e-6, delta=10e-6)

    def test_calibration_with_fast_usb_clock_drift(self):
        offsets = [0, .013, -.008]
        drifts = [0, -1130e-6, -520e-6]
        tracks = self.signals(0, 10, offsets, drifts)
        clock = fit_clock(tracks, self.rate, 1, 9, (2 - self.positions[:, 1]).tolist())
        for index in (1, 2):
            self.assertAlmostEqual(clock["channels"][index]["drift"], drifts[index], delta=10e-6)
            self.assertLess(clock["channels"][index]["residual_p95_seconds"], .0001)

    def test_recording_time_jump_is_not_reported_as_a_valid_direction(self):
        audio = self.signals(90)
        audio[0] = np.roll(audio[0], round(.0053 * self.rate))
        result = direction(audio, self.rate, self.positions)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["azimuth_deg"])
        self.assertIn("時間差", result["reason"])

    def test_packet_recovery_preserves_a_new_source_direction(self):
        self.positions = np.array([[-.1, -.1, 0], [.1, -.1, 0], [.1, .1, 0], [-.1, .1, .1]])
        clock = dict(channels=[dict(offset_seconds=0., drift=0.) for _ in range(4)])
        for angle, jumped_channel, removed in [(37, 0, True), (-64, 2, False)]:
            with self.subTest(angle=angle):
                tracks = self.signals(angle, seconds=8, elevation=20)
                channel = tracks[jumped_channel]
                cut, packet = 5 * self.rate, 256
                if removed:
                    tracks[jumped_channel] = np.r_[channel[:cut], channel[cut + packet:], np.zeros(packet)]
                else:
                    tracks[jumped_channel] = np.r_[channel[:cut], np.zeros(packet), channel[cut:-packet]]
                audio, sync = align_tracks(tracks, self.rate, clock, 6, 7,
                                           positions=self.positions, packet_samples=packet)
                result = direction(audio, self.rate, self.positions)
                self.assertEqual(result["status"], "ok")
                self.assertAlmostEqual(result["azimuth_deg"], angle, delta=2)
                self.assertEqual(sync["valid_frames"], sync["total_frames"])

    def test_packet_recovery_rejects_ambiguous_array_size(self):
        clock = dict(channels=[dict(offset_seconds=0., drift=0.) for _ in range(3)])
        with self.assertRaisesRegex(ValueError, "Array is too large"):
            align_tracks(self.signals(30, seconds=2), self.rate, clock, .5, 1.5,
                         positions=self.positions * 10, packet_samples=256)

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

    def test_appending_records_matches_fresh_enrollment_and_rejects_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            first, second, third = [folder / name for name in ("one", "two", "three")]
            embeddings = {first: np.array([1., 0.]), second: np.array([0., 1.]), third: np.array([1., 0.])}
            for path in embeddings:
                path.write_bytes(path.name.encode())
            engine = SpeakerEngine.__new__(SpeakerEngine)
            engine.model_hash = "test"
            engine.embedding = embeddings.__getitem__
            database = folder / "speakers.json"
            engine.enroll("A", [first, second], database)
            with self.assertRaisesRegex(ValueError, "已用於"):
                engine.append_samples("A", [first], database)
            self.assertEqual(engine.database(database)["speakers"]["A"]["recordings"], 2)

            engine.append_samples("A", [third], database)
            engine.enroll("B", [first, second, third], database)
            speakers = engine.database(database)["speakers"]
            self.assertEqual(speakers["A"]["recordings"], 3)
            np.testing.assert_allclose(speakers["A"]["template"], speakers["B"]["template"])

    def test_appending_to_legacy_template_keeps_prior_weight(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            database = folder / "speakers.json"
            sample = folder / "new.wav"
            sample.write_bytes(b"new")
            save_json(database, dict(schema=1, model_sha256="test", speakers={
                "legacy": dict(template=[1., 0.], recordings=2)}))
            engine = SpeakerEngine.__new__(SpeakerEngine)
            engine.model_hash = "test"
            engine.embedding = lambda path: np.array([0., 1.])
            engine.append_samples("legacy", [sample], database)
            row = engine.database(database)["speakers"]["legacy"]
            self.assertEqual(row["recordings"], 3)
            np.testing.assert_allclose(row["template"], np.array([2., 1.]) / np.sqrt(5))


if __name__ == "__main__":
    unittest.main()
