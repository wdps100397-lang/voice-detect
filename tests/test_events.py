import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

from src.common import save_json
from src.events import analyze_event, input_event


class NativeEventTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.files = [self.folder / f"{label}.wav" for label in "ABC"]
        for file in self.files:
            wavfile.write(file, 16000, np.zeros(48000, dtype="int16"))

    def analyze(self, **kwargs):
        return analyze_event(self.files, self.folder / "config.json", self.folder / "clock.json",
                             0, 2, self.folder / "speakers.json", .65, .08, **kwargs)

    def test_native_payloads(self):
        with patch("src.events.locate", return_value=dict(status="ok", azimuth_deg=-45)), patch("src.speakers.SpeakerEngine") as engine:
            engine.return_value.identify.return_value = dict(status="recognized", user_id="U_A", score=.8)
            result = self.analyze(utterance_id="test-01", observed_at=1234,
                                  stt=dict(utterance_id="test-01", text="我的筆在哪裡？"))
        self.assertEqual(result["speaker_identity"], dict(user_id="U_A", location="", azimuth_deg=-45,
                                                          timestamp=1234, source="voice_detect"))
        self.assertEqual(result["agent_input"], dict(utterance_id="test-01", user_id="U_A", text="我的筆在哪裡？"))
        self.assertFalse(result["integration"]["identity_receiver_compatible"])

    def test_partial_failure_preserved(self):
        result = self.analyze()
        self.assertEqual(result["diagnostics"]["direction"]["status"], "error")
        self.assertEqual(result["diagnostics"]["recognition"]["status"], "error")
        self.assertIsNone(result["speaker_identity"])
        self.assertIsNone(result["agent_input"])

    def test_unknown_identity_uses_empty_id(self):
        with patch("src.events.locate", return_value=dict(status="ok", azimuth_deg=90)), patch("src.speakers.SpeakerEngine") as engine:
            engine.return_value.identify.return_value = dict(status="unknown", user_id="")
            result = self.analyze(observed_at=1234)
        self.assertEqual(result["speaker_identity"]["user_id"], "")
        self.assertEqual(result["speaker_identity"]["azimuth_deg"], 90)

    def test_missing_angle_or_timestamp_produces_no_identity(self):
        with patch("src.events.locate", return_value=dict(status="ambiguous", azimuth_deg=None)):
            self.assertIsNone(self.analyze(observed_at=1234)["speaker_identity"])
        with patch("src.events.locate", return_value=dict(status="ok", azimuth_deg=10)):
            self.assertIsNone(self.analyze()["speaker_identity"])

    def test_capture_clock_and_stable_ids(self):
        save_json(self.folder / "summary.json", dict(started_at_unix=1000))
        with patch("src.events.locate", return_value=dict(status="ok", azimuth_deg=10)):
            first = self.analyze()
            second = self.analyze()
        self.assertEqual(first["diagnostics"]["observed_at"], 1002)
        self.assertEqual(first["utterance_id"], second["utterance_id"])

    def test_stt_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.analyze(utterance_id="one", stt=dict(utterance_id="two", text="測試"))

    def test_disabled_components_are_not_loaded(self):
        with patch("src.events.locate") as locate, patch("src.speakers.SpeakerEngine") as model:
            result = self.analyze(enable_speaker=False, enable_localization=False,
                                  utterance_id="off", stt=dict(utterance_id="off", text="測試"))
        locate.assert_not_called()
        model.assert_not_called()
        self.assertEqual(result["diagnostics"]["recognition"]["status"], "disabled")
        self.assertEqual(result["diagnostics"]["direction"]["status"], "disabled")
        self.assertEqual(result["agent_input"]["user_id"], "")
        self.assertIsNone(result["speaker_identity"])

    def test_speaker_only_keeps_recognition_when_direction_disabled(self):
        with patch("src.events.locate") as locate, patch("src.speakers.SpeakerEngine") as model:
            model.return_value.identify.return_value = dict(status="recognized", user_id="U_A")
            result = self.analyze(enable_localization=False, utterance_id="speaker",
                                  stt=dict(utterance_id="speaker", text="測試"))
        locate.assert_not_called()
        self.assertEqual(result["agent_input"]["user_id"], "U_A")
        self.assertIsNone(result["speaker_identity"])

    def test_contract_limits(self):
        for text in ("", " " * 3, "x" * 2001):
            with self.assertRaises(ValueError):
                input_event("one", dict(utterance_id="one", text=text), "U_A")
        with self.assertRaises(ValueError):
            self.analyze(observed_at=float("nan"))


if __name__ == "__main__":
    unittest.main()
