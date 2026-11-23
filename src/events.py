"""Produce Project A native speech input and speaker observations."""
import hashlib
import json
from pathlib import Path
import tempfile
import time

import numpy as np
from scipy.io import wavfile
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from src.common import ROOT, fingerprint, load_tracks, read_json
from src.localization import locate

REFERENCE_COMMIT = "93d9bc1af753deadd4ab2c5487438e2cc765301c"


def validate_native(data, schema_name):
    if data is None:
        return
    try:
        Draft202012Validator(read_json(ROOT / "schemas" / schema_name)).validate(data)
        json.dumps(data, allow_nan=False)
    except ValidationError as error:
        raise ValueError(error.message) from error


def input_event(utterance_id, stt, user_id):
    if stt is None:
        return None
    if not isinstance(stt, dict) or stt.get("utterance_id") != utterance_id:
        raise ValueError("STT utterance_id must match this audio event")
    text = stt.get("text")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError("STT text must contain 1..2000 characters")
    event = dict(utterance_id=utterance_id, text=text.strip(), user_id=user_id)
    validate_native(event, "agent_input.schema.json")
    return event


def analyze_event(files, config, calibration, start, end, database, threshold, margin,
                  utterance_id=None, stt=None, observed_at=None):
    if not np.isfinite([start, end, threshold, margin]).all():
        raise ValueError("Interval and speaker thresholds must be finite")
    if not -1 <= threshold <= 1 or not 0 <= margin <= 2:
        raise ValueError("Threshold must be -1..1 and margin 0..2")
    rate, tracks = load_tracks(files)
    if start < 0 or end <= start or end > min(map(len, tracks)) / rate:
        raise ValueError("Analysis interval is outside the recording")
    hashes = [fingerprint(path) for path in files]
    if utterance_id is None:
        key = json.dumps([hashes, start, end])
        utterance_id = "voice-" + hashlib.sha256(key.encode()).hexdigest()[:24]
    if not isinstance(utterance_id, str) or not utterance_id.strip() or len(utterance_id) > 128:
        raise ValueError("utterance_id must contain 1..128 characters")
    capture = None
    paths = [Path(path).resolve() for path in files]
    if len({path.parent for path in paths}) == 1 and [path.name for path in paths] == ["A.wav", "B.wav", "C.wav"]:
        summary_path = paths[0].parent / "summary.json"
        if summary_path.exists():
            capture = read_json(summary_path)
            if observed_at is None and "started_at_unix" in capture:
                observed_at = capture["started_at_unix"] + end
    if observed_at is not None:
        if isinstance(observed_at, bool) or not isinstance(observed_at, (float, int)) or not np.isfinite(observed_at) or observed_at < 0:
            raise ValueError("observed_at must be nonnegative finite Unix seconds")
    # Validate external STT alignment before expensive model inference.
    input_event(utterance_id, stt, "")
    try:
        direction = locate(files, config, calibration, start, end)
    except (ValueError, OSError, KeyError) as error:
        direction = dict(status="error", azimuth_deg=None, reason=str(error))
    try:
        from src.speakers import SpeakerEngine
        with tempfile.TemporaryDirectory() as temporary:
            segment = Path(temporary) / "event.wav"
            wavfile.write(segment, rate, tracks[0][round(start * rate):round(end * rate)].astype("float32"))
            recognition = SpeakerEngine().identify(segment, database, threshold, margin)
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        recognition = dict(status="error", user_id="", reason=str(error))
    identity = None
    if direction["status"] == "ok" and observed_at is not None:
        identity = dict(user_id=recognition["user_id"], location="",
                        azimuth_deg=direction["azimuth_deg"], timestamp=observed_at,
                        source="voice_detect")
        validate_native(identity, "speaker_identity.schema.json")
    return dict(
        utterance_id=utterance_id, interval_seconds=[start, end],
        speaker_identity=identity,
        agent_input=input_event(utterance_id, stt, recognition["user_id"]),
        diagnostics=dict(
            direction=direction, recognition=recognition, identity_track="A", capture=capture,
            source=dict(track_sha256=hashes, sample_rate_hz=rate), observed_at=observed_at,
            identity_age_seconds=None if observed_at is None else time.time() - observed_at),
        integration=dict(
            identity_topic="/project_a/speaker/identity", input_topic="/project_a/agent/input",
            message_type="std_msgs/msg/String",
            qos=dict(reliability="reliable", durability="volatile", depth=10),
            reference_commit=REFERENCE_COMMIT, identity_receiver_compatible=False,
            identity_receiver_reason="Current receiver accepts source=mock only; voice_detect requires receiver support.",
            identity_pairing="Latest observation within 3 seconds; no audio segment alignment.",
            user_id_mode="agent/input user_id applies when use_speaker_identity=false; true overrides it from identity cache."))
