import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from src.common import ROOT
from src.live import AudioRing, aligned_window, observation

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
        mailbox = ros_module.IdentityMailbox()
        mailbox.update(payload)
        self.assertEqual(mailbox.snapshot(101.)["user_id"], "U_A")
        self.assertEqual(mailbox.snapshot(101.)["timestamp"], 100.)
        self.assertIsNone(mailbox.snapshot(104.))
        mailbox.update(dict(payload, timestamp=99., user_id="U_B"))
        self.assertEqual(mailbox.snapshot(101.)["user_id"], "U_A")

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


if __name__ == "__main__":
    unittest.main()
