import importlib.util
import unittest
from unittest.mock import Mock, patch

import numpy as np

from src.common import ROOT
from src.features import evaluate, combined_identity
from src.live import run_live

spec = importlib.util.spec_from_file_location("feature_ros_node", ROOT / "ros2/voice_detect_node/voice_detect_node/node.py")
ros_node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ros_node)


class FeatureModeTests(unittest.TestCase):
    def test_all_four_feature_combinations(self):
        audio = [np.zeros(48000)] * 3
        for speaker in (False, True):
            for localization in (False, True):
                with self.subTest(speaker=speaker, localization=localization):
                    recognizer = Mock()
                    recognizer.identify.return_value = dict(status="recognized", user_id="U_A")
                    with patch("src.features.direction", return_value=dict(status="ok", azimuth_deg=30)) as locate:
                        recognition, direction = evaluate(audio, 16000, None, recognizer, speaker, localization)
                    self.assertEqual(recognizer.identify.call_count, int(speaker))
                    self.assertEqual(locate.call_count, int(localization))
                    payload = combined_identity(recognition, direction, 100.)
                    self.assertEqual(payload is not None, localization)
                    if payload:
                        self.assertEqual(payload["user_id"], "U_A" if speaker else "")
                    if not localization:
                        self.assertIsNone(direction["azimuth_deg"])

    def test_runtime_failure_is_isolated(self):
        recognizer = Mock()
        recognizer.identify.side_effect = RuntimeError("model failed")
        with patch("src.features.direction", return_value=dict(status="ok", azimuth_deg=-30)):
            recognition, location = evaluate([np.zeros(100)] * 3, 16000, None, recognizer)
        self.assertEqual(recognition["status"], "error")
        self.assertEqual(location["status"], "ok")
        recognizer.identify.side_effect = None
        recognizer.identify.return_value = dict(status="recognized", user_id="U_A")
        with patch("src.features.direction", side_effect=RuntimeError("localizer failed")):
            recognition, location = evaluate([np.zeros(100)] * 3, 16000, None, recognizer)
        self.assertEqual(recognition["user_id"], "U_A")
        self.assertEqual(location["status"], "error")

    def test_both_disabled_opens_no_audio_or_models(self):
        config = dict(microphone_count=4, enable_speaker_recognition=False, enable_localization=False)
        events = []
        with patch("src.live.read_json", return_value=config) as read, patch("src.live.LiveCapture") as capture, patch("src.live.Recognizer") as recognize, patch("src.live.time.sleep"):
            run_live("config.json", events.append, Mock(side_effect=[False, True]))
        self.assertEqual(read.call_count, 1)
        capture.assert_not_called()
        recognize.assert_not_called()
        features = [row["data"] for row in events if row["kind"] == "features"][0]
        self.assertEqual(features["recognition"]["status"], "disabled")
        self.assertEqual(features["direction"]["status"], "disabled")

    def test_microphone_count_requires_integer_three_or_four(self):
        for value in (True, 3.0, 2, 5):
            config = dict(microphone_count=value, enable_speaker_recognition=False, enable_localization=False)
            with self.subTest(count=value), patch("src.live.read_json", return_value=config), self.assertRaises(ValueError):
                run_live("config.json", Mock())

    def test_speaker_only_skips_geometry_and_clock(self):
        config = dict(microphone_count=4, devices=[1, 2, 3, 4], sample_rate_hz=16000,
                      enable_speaker_recognition=True, enable_localization=False,
                      database="database.json", calibration_seconds="unused", max_clock_age_seconds="unused")
        events = []
        capture = Mock()
        capture.started_at = 100.
        capture.snapshots.return_value = [(0, np.zeros(16000 * 3))]
        recognizer = Mock()
        recognizer.identify_window.return_value = (dict(status="recognized", user_id="U_A"), 0)
        with patch("src.live.read_json", return_value=config) as read, patch("src.live.LiveCapture") as cls, patch("src.live.Recognizer", return_value=recognizer), patch("src.live.fit_clock") as clock, patch("src.live.time.time", return_value=103.), patch("src.live.time.sleep"):
            cls.return_value.__enter__.return_value = capture
            run_live("config.json", events.append, Mock(side_effect=[False, True]))
        self.assertEqual(read.call_count, 1)
        self.assertEqual(cls.call_args.args[0], [1])
        clock.assert_not_called()
        features = [row["data"] for row in events if row["kind"] == "features"][0]
        self.assertEqual(features["recognition"]["user_id"], "U_A")
        self.assertEqual(features["direction"]["status"], "disabled")
        self.assertFalse(any(row["kind"] == "identity" for row in events))

    def test_default_four_microphone_localization_without_speaker_model(self):
        config = dict(devices=[1, 2, 3, 4], sample_rate_hz=16000,
                      enable_speaker_recognition=False, enable_localization=True,
                      array_config="array.json")
        geometry = dict(geometry_measured=True, positions_m=[[-.1, -.1, 0], [.1, -.1, 0], [.1, .1, 0], [-.1, .1, .1]],
                        calibration_source_m=[0, 1, .2],
                        robot_frame=dict(array_origin_m=[0, 0, .6], yaw_offset_deg=20))
        capture = Mock()
        capture.started_at = 100.
        capture.snapshots.return_value = [(0, np.zeros(16000 * 9))] * 4
        clock = dict(channels=[dict(offset_seconds=0., drift=0.)] * 4)
        events = []
        with patch("src.live.read_json", side_effect=[config, geometry]), patch("src.live.LiveCapture") as cls, patch("src.live.Recognizer") as model, patch("src.live.fit_clock", return_value=clock) as fit, patch("src.features.direction", return_value=dict(status="ok", azimuth_deg=45)), patch("src.live.time.time", return_value=109.), patch("src.live.time.sleep"):
            cls.return_value.__enter__.return_value = capture
            run_live("config.json", events.append, Mock(side_effect=[False, False, True]))
        model.assert_not_called()
        self.assertEqual(len(fit.call_args.args[0]), 4)
        np.testing.assert_allclose(fit.call_args.args[4],
                                   np.linalg.norm(np.array(geometry["positions_m"]) - geometry["calibration_source_m"],
                                                  axis=1))
        self.assertEqual(cls.call_args.args[0], [1, 2, 3, 4])
        identity = [row["data"] for row in events if row["kind"] == "identity"][0]
        self.assertEqual(identity["user_id"], "")
        self.assertEqual(identity["azimuth_deg"], 45)
        features = [row["data"] for row in events if row["kind"] == "features"][0]
        messages = ros_node.topic_messages(features)
        self.assertEqual(messages[ros_node.DIRECTION_TOPIC]["robot_yaw_deg"], 65)
        self.assertEqual(messages[ros_node.TOPIC]["azimuth_deg"], 45)

    def test_feature_mailbox_keeps_disabled_state_explicit(self):
        mailbox = ros_node.FeatureMailbox()
        mailbox.update(dict(timestamp=100., source="voice_detect",
                            recognition=dict(status="disabled", user_id="old"),
                            direction=dict(status="disabled", azimuth_deg=30)))
        result = mailbox.snapshot(101.)
        self.assertEqual(result["recognition"]["user_id"], "")
        self.assertIsNone(result["direction"]["azimuth_deg"])
        self.assertIsNone(mailbox.snapshot(104.))

    def test_geometry_initialization_failure_preserves_speaker(self):
        config = dict(microphone_count=3, devices=[1], sample_rate_hz=16000,
                      enable_speaker_recognition=True, enable_localization=True,
                      database="database.json", array_config="missing.json")
        capture = Mock()
        capture.started_at = 100.
        capture.snapshots.return_value = [(0, np.zeros(48000))]
        recognizer = Mock()
        recognizer.identify_window.return_value = (dict(status="recognized", user_id="U_A"), 0)
        events = []
        with patch("src.live.read_json", side_effect=[config, FileNotFoundError("missing geometry")]), patch("src.live.LiveCapture") as cls, patch("src.live.Recognizer", return_value=recognizer), patch("src.live.time.time", return_value=103.), patch("src.live.time.sleep"):
            cls.return_value.__enter__.return_value = capture
            run_live("config.json", events.append, Mock(side_effect=[False, True]))
        features = [row["data"] for row in events if row["kind"] == "features"][0]
        self.assertEqual(features["recognition"]["user_id"], "U_A")
        self.assertEqual(features["direction"]["status"], "error")

    def test_calibration_failure_preserves_speaker(self):
        config = dict(microphone_count=3, devices=[1, 2, 3], sample_rate_hz=16000,
                      array_config="array.json", database="database.json",
                      calibration_distances_m=[1, 1, 1])
        geometry = dict(geometry_measured=True, positions_m=[[-.1, 0], [.1, 0], [0, .2]])
        capture = Mock()
        capture.started_at = 100.
        capture.snapshots.return_value = [(0, np.zeros(16000 * 9))] * 3
        recognizer = Mock()
        recognizer.identify_window.return_value = (dict(status="recognized", user_id="U_A"), 0)
        events = []
        with patch("src.live.read_json", side_effect=[config, geometry]), patch("src.live.LiveCapture") as cls, patch("src.live.Recognizer", return_value=recognizer), patch("src.live.fit_clock", side_effect=ValueError("bad calibration")), patch("src.live.time.time", return_value=109.), patch("src.live.time.sleep"):
            cls.return_value.__enter__.return_value = capture
            run_live("config.json", events.append, Mock(side_effect=[False, False, True]))
        features = [row["data"] for row in events if row["kind"] == "features"][0]
        self.assertEqual(features["recognition"]["user_id"], "U_A")
        self.assertEqual(features["direction"]["status"], "error")

    def test_model_initialization_failure_preserves_localization(self):
        config = dict(microphone_count=3, devices=[1, 2, 3], sample_rate_hz=16000,
                      array_config="array.json", database="database.json",
                      calibration_distances_m=[1, 1, 1])
        geometry = dict(geometry_measured=True, positions_m=[[-.1, 0], [.1, 0], [0, .2]])
        capture = Mock()
        capture.started_at = 100.
        capture.snapshots.return_value = [(0, np.zeros(16000 * 9))] * 3
        clock = dict(channels=[dict(offset_seconds=0., drift=0.)] * 3)
        events = []
        with patch("src.live.read_json", side_effect=[config, geometry]), patch("src.live.LiveCapture") as cls, patch("src.live.Recognizer", side_effect=FileNotFoundError("missing model")), patch("src.live.fit_clock", return_value=clock), patch("src.features.direction", return_value=dict(status="ok", azimuth_deg=-45)), patch("src.live.time.time", return_value=109.), patch("src.live.time.sleep"):
            cls.return_value.__enter__.return_value = capture
            run_live("config.json", events.append, Mock(side_effect=[False, False, True]))
        features = [row["data"] for row in events if row["kind"] == "features"][0]
        self.assertEqual(features["recognition"]["status"], "error")
        self.assertEqual(features["direction"]["status"], "ok")
        self.assertEqual(features["direction"]["azimuth_deg"], -45)

    def test_alignment_gap_preserves_speaker(self):
        config = dict(microphone_count=3, devices=[1, 2, 3], sample_rate_hz=16000,
                      array_config="array.json", database="database.json",
                      calibration_distances_m=[1, 1, 1])
        geometry = dict(geometry_measured=True, positions_m=[[-.1, 0], [.1, 0], [0, .2]])
        capture = Mock()
        capture.started_at = 100.
        capture.snapshots.return_value = [(0, np.zeros(16000 * 9))] * 3
        recognizer = Mock()
        recognizer.identify_window.return_value = (dict(status="recognized", user_id="U_A"), 0)
        events = []
        with patch("src.live.read_json", side_effect=[config, geometry]), patch("src.live.LiveCapture") as cls, patch("src.live.Recognizer", return_value=recognizer), patch("src.live.fit_clock", return_value={"channels": []}), patch("src.live.aligned_window", return_value=None), patch("src.live.time.time", return_value=109.), patch("src.live.time.sleep"):
            cls.return_value.__enter__.return_value = capture
            run_live("config.json", events.append, Mock(side_effect=[False, False, True]))
        features = [row["data"] for row in events if row["kind"] == "features"][0]
        self.assertEqual(features["recognition"]["user_id"], "U_A")
        self.assertEqual(features["direction"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
