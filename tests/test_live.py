import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from src.common import ROOT
from src.live import AudioRing, Recognizer, aligned_window, observation, run_live

spec = importlib.util.spec_from_file_location(
    "ros_speaker_node", ROOT / "ros2/voice_detect_node/voice_detect_node/node.py")
ros_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ros_module)


class LiveTests(unittest.TestCase):
    def test_ring_keeps_absolute_sample_positions(self):
        ring = AudioRing(5)
        ring.append(np.array([1, 2, 3], dtype="<i2").tobytes())
        ring.append(np.array([4, 5, 6, 7], dtype="<i2").tobytes())
        start, audio = ring.snapshot()
        self.assertEqual(start, 2)
        np.testing.assert_allclose(audio * 32768, [3, 4, 5, 6, 7])

    def test_discontinuity_invalidates_clock(self):
        ring = AudioRing(10)
        ring.append(np.zeros(3, dtype="<i2").tobytes(), "input overflow")
        with self.assertRaises(RuntimeError):
            ring.snapshot()

    def test_clock_corrected_live_window(self):
        rate = 16000
        times = np.arange(rate * 5) / rate
        source_times = np.arange(-rate, rate * 6) / rate
        noise = np.random.default_rng(16).normal(0, .03, len(source_times))
        channels = [dict(offset_seconds=0., drift=0.),
                    dict(offset_seconds=.01, drift=40e-6),
                    dict(offset_seconds=-.005, drift=-20e-6)]
        snapshots = []
        for row in channels:
            audio = np.interp((times - row["offset_seconds"]) / (1 + row["drift"]),
                              source_times, noise)
            snapshots.append((rate * 2, audio[rate * 2:]))
        result = aligned_window(snapshots, dict(channels=channels), rate, 2.5)
        self.assertIsNotNone(result)
        _, aligned = result
        self.assertLess(np.mean((aligned[0] - aligned[1]) ** 2), .0002)

    def test_real_pipeline_to_ros_mailbox(self):
        rate = 16000
        positions = np.array([[-.1, -.057735], [.1, -.057735], [0, .11547]])
        vector = np.array([-np.sin(np.pi / 4), np.cos(np.pi / 4)])
        times = np.arange(rate * 3) / rate
        source_times = np.arange(-rate, rate * 4) / rate
        source = np.random.default_rng(18).normal(0, .03, len(source_times))
        audio = [np.interp(times + point @ vector / 343, source_times, source) for point in positions]
        recognizer = Mock()
        recognizer.identify.return_value = dict(status="recognized", user_id="U_A")
        with patch("src.live.time.time", return_value=100.):
            payload, diagnostics = observation(audio, rate, positions, recognizer, 100., 100.)
        self.assertEqual(diagnostics["status"], "observed")
        self.assertLessEqual(abs(payload["azimuth_deg"] - 45), 2)
        mailbox = ros_module.FeatureMailbox()
        features = dict(timestamp=100., source="voice_detect", recognition=diagnostics["recognition"],
                        direction=diagnostics["direction"])
        mailbox.update(features)
        messages = ros_module.topic_messages(mailbox.snapshot(101.))
        self.assertEqual(messages[ros_module.TOPIC], payload)
        self.assertIsNone(mailbox.snapshot(104.))
        self.assertFalse(mailbox.update(dict(features, timestamp=99.,
                                             recognition=dict(status="recognized", user_id="U_B"))))
        self.assertEqual(mailbox.snapshot(101.)["recognition"]["user_id"], "U_A")

    def test_silence_and_stale_inference_do_not_refresh_identity(self):
        audio = [np.zeros(48000)] * 3
        recognizer = Mock()
        recognizer.identify.return_value = dict(status="unusable_audio", user_id="")
        payload, _ = observation(audio, 16000, np.zeros((3, 2)), recognizer, 100., 101.)
        self.assertIsNone(payload)
        recognizer.reset_mock()
        payload, _ = observation(audio, 16000, np.zeros((3, 2)), recognizer, 100., 104.)
        self.assertIsNone(payload)
        recognizer.identify.assert_not_called()

    def test_speech_turn_keeps_id_and_restarts_after_pause(self):
        with patch("src.speakers.SpeakerEngine") as engine:
            engine.return_value.database.return_value = {"speakers": {"U_A": {}, "U_B": {}}}
            recognizer = Recognizer(Path("db.json"), .65, .08, 1.5, .8)
        recognizer.has_speech = lambda frame, rate: frame[0] != 0
        recognizer.engine.identify_speech.side_effect = [
            dict(status="recognized", user_id="U_A"),
            dict(status="unknown", user_id=""),
            dict(status="recognized", user_id="U_B"),
        ]
        self.assertEqual(recognizer.min_speech_seconds, 1.2)
        rate, frame = 16000, 480
        captured = []

        def observe(marker, frames):
            captured.extend([marker] * frames)
            window = np.repeat(captured[-50:], frame)
            return recognizer.identify_window(window, rate, len(captured) * frame)

        self.assertEqual(observe(1, 45)[0]["user_id"], "U_A")
        self.assertEqual(observe(0, 10)[0]["user_id"], "U_A")
        self.assertEqual(observe(2, 10)[0]["user_id"], "U_A")
        self.assertEqual(recognizer.engine.identify_speech.call_count, 1)
        self.assertEqual(observe(0, 28)[0]["status"], "insufficient_speech")
        result, offset = observe(2, 10)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["detected_speech_seconds"], .3)
        self.assertGreater(offset, 0)
        self.assertEqual(observe(2, 30)[0]["status"], "unknown")
        np.testing.assert_array_equal(recognizer.engine.identify_speech.call_args.args[0],
                                      np.full(frame * 40, 2))
        self.assertEqual(observe(2, 10)[0]["user_id"], "U_B")

    def test_sparse_speech_accumulates_across_windows_with_short_pauses(self):
        with patch("src.speakers.SpeakerEngine") as engine, patch("webrtcvad.Vad") as vad:
            engine.return_value.database.return_value = {"speakers": {"U_A": {}}}
            recognizer = Recognizer(Path("db.json"), .65, .08, 1.5, .8, vad_mode=1)
        vad.assert_called_once_with(1)
        recognizer.has_speech = lambda block, rate: block[0] != 0
        recognizer.engine.identify_speech.return_value = dict(status="recognized", user_id="U_A")
        rate = 16000
        # 0.45 seconds of voice separated by pauses shorter than the configured gap.
        audio = np.tile(np.r_[np.full(7200, .1), np.zeros(7200)], 3)
        for end, detected in [(16000, .54), (32000, 1.08), (43200, 1.35)]:
            window = audio[max(0, end - rate):end]
            result, _ = recognizer.identify_window(window, rate, end, history=audio[:end])
            self.assertEqual(result["detected_speech_seconds"], detected)
            if detected < 1.2:
                self.assertEqual(result["status"], "pending")
                recognizer.engine.identify_speech.assert_not_called()
        self.assertEqual(result["user_id"], "U_A")
        recognizer.engine.identify_speech.assert_called_once()
        np.testing.assert_array_equal(recognizer.engine.identify_speech.call_args.args[0],
                                      np.full(21600, .1))

    def test_held_speaker_is_old_and_unknown_clears_it(self):
        mailbox = ros_module.FeatureMailbox()

        def publish(status, user_id, timestamp, direction_status="ok"):
            mailbox.update(dict(timestamp=timestamp, source="voice_detect",
                                recognition=dict(status=status, user_id=user_id),
                                direction=dict(status=direction_status, azimuth_deg=30)))
            return ros_module.topic_messages(mailbox.snapshot(timestamp))

        publish("recognized", "U_A", 100.)
        held = publish("insufficient_speech", "", 105.)
        self.assertEqual(held[ros_module.TOPIC]["user_id"], "")
        self.assertEqual(held[ros_module.TOPIC]["timestamp"], 105.)
        self.assertEqual(held[ros_module.RECOGNITION_TOPIC]["status"], "held")
        self.assertEqual(held[ros_module.RECOGNITION_TOPIC]["user_id"], "U_A")
        self.assertEqual(held[ros_module.RECOGNITION_TOPIC]["timestamp"], 100.)
        self.assertEqual(held[ros_module.DIRECTION_TOPIC]["timestamp"], 105.)
        pending = publish("pending", "", 106., "no_signal")
        self.assertEqual(pending[ros_module.RECOGNITION_TOPIC]["user_id"], "")
        self.assertEqual(pending[ros_module.TOPIC], dict(user_id="", location="", azimuth_deg=None,
                                                        timestamp=106., source="voice_detect"))
        unknown = publish("unknown", "", 107., "ambiguous")
        self.assertEqual(unknown[ros_module.RECOGNITION_TOPIC]["status"], "unknown")
        self.assertEqual(unknown[ros_module.TOPIC]["user_id"], "")
        self.assertIsNone(unknown[ros_module.TOPIC]["azimuth_deg"])
        silence = publish("insufficient_speech", "", 110.)
        self.assertEqual(silence[ros_module.RECOGNITION_TOPIC]["user_id"], "")
        known = publish("recognized", "U_B", 111., "disabled")
        self.assertEqual(known[ros_module.TOPIC]["user_id"], "U_B")
        self.assertIsNone(known[ros_module.TOPIC]["azimuth_deg"])
        self.assertIsNone(mailbox.snapshot(115.))
        publish("unknown", "", 116.)
        self.assertEqual(publish("insufficient_speech", "", 117.)[ros_module.RECOGNITION_TOPIC]["user_id"], "")

    def test_live_accepts_one_second_window_and_rejects_shorter(self):
        config = dict(enable_speaker_recognition=True, enable_localization=False, window_seconds=1.0,
                      database="speakers.json")
        with patch("src.live.read_json", return_value=config), patch("src.live.Recognizer", return_value=None) as recognizer:
            run_live("config.json", Mock(), stop=lambda: True)
            self.assertEqual(recognizer.call_args.args[-2], 1.0)
            config["window_seconds"] = .99
            with self.assertRaisesRegex(ValueError, "window_seconds"):
                run_live("config.json", Mock(), stop=lambda: True)


if __name__ == "__main__":
    unittest.main()
