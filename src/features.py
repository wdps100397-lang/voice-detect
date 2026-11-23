"""Independent feature evaluation and the existing combined-topic contract."""
from src.localization import direction


def switches(config):
    speaker = config.get("enable_speaker_recognition", True)
    localization = config.get("enable_localization", True)
    if not isinstance(speaker, bool) or not isinstance(localization, bool):
        raise ValueError("Feature switches must be JSON booleans")
    return speaker, localization


def evaluate(audio, rate, positions, recognizer, enable_speaker=True, enable_localization=True,
             speaker_error=None, localization_error=None):
    recognition = dict(status="disabled", user_id="")
    location = dict(status="disabled", azimuth_deg=None)
    if enable_speaker:
        try:
            if recognizer is None:
                raise ValueError(speaker_error or "Speaker recognition is unavailable")
            recognition = recognizer.identify(audio[0], rate)
        except Exception as error:
            recognition = dict(status="error", user_id="", reason=str(error))
    if enable_localization:
        try:
            if localization_error:
                raise ValueError(localization_error)
            location = direction(audio, rate, positions)
        except Exception as error:
            location = dict(status="error", azimuth_deg=None, reason=str(error))
    return recognition, location


def combined_identity(recognition, location, timestamp):
    if location["status"] != "ok" or recognition["status"] not in ("recognized", "unknown", "disabled"):
        return None
    return dict(user_id=recognition["user_id"], location="", azimuth_deg=location["azimuth_deg"],
                timestamp=timestamp, source="voice_detect")
