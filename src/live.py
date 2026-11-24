"""Continuous Windows acquisition and real speaker/azimuth observations."""
from collections import deque
from contextlib import ExitStack
from math import gcd
from pathlib import Path
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from scipy.signal import resample_poly
import tempfile

from src.common import ROOT, read_json
from src.localization import fit_clock, validate_positions
from src.features import switches, evaluate, combined_identity

MIN_WINDOW_SECONDS = 1.0
DEFAULT_WINDOW_SECONDS = 1.5
DEFAULT_SPEECH_GAP_SECONDS = .8


class AudioRing:
    def __init__(self, capacity):
        self.capacity = capacity
        self.blocks = deque()
        self.total = 0
        self.size = 0
        self.lock = threading.Lock()
        self.error = None

    def append(self, data, status=None):
        block = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768
        with self.lock:
            if status:
                self.error = str(status)
            self.blocks.append(block)
            self.total += len(block)
            self.size += len(block)
            while self.size > self.capacity:
                excess = self.size - self.capacity
                first = self.blocks.popleft()
                if len(first) > excess:
                    self.blocks.appendleft(first[excess:])
                    self.size -= excess
                else:
                    self.size -= len(first)

    def snapshot(self):
        with self.lock:
            if self.error:
                raise RuntimeError(f"Audio discontinuity: {self.error}. Restart to recalibrate.")
            return self.total - self.size, np.concatenate(self.blocks) if self.blocks else np.array([])


class LiveCapture:
    def __init__(self, devices, rate, buffer_seconds):
        if len(devices) not in (1, 3, 4) or len(set(devices)) != len(devices) or any(isinstance(device, bool) or not isinstance(device, int) or device < 0 for device in devices):
            raise ValueError("Select distinct WASAPI devices in physical A/B/C/D order")
        self.devices = devices
        self.rate = rate
        self.rings = [AudioRing(round(rate * buffer_seconds)) for _ in devices]
        self.stack = ExitStack()
        self.streams = []
        self.started_at = None

    def __enter__(self):
        try:
            for device, ring in zip(self.devices, self.rings):
                info = sd.query_devices(device, "input")
                if sd.query_hostapis(info["hostapi"])["name"] != "Windows WASAPI":
                    raise ValueError("Live USB acquisition requires WASAPI inputs")
                def callback(data, frames, timing, status, target=ring):
                    target.append(data, status)
                stream = sd.RawInputStream(device=device, channels=1, samplerate=self.rate,
                                           dtype="int16", blocksize=0, callback=callback,
                                           extra_settings=sd.WasapiSettings(exclusive=True))
                self.stack.callback(stream.close)
                self.streams.append(stream)
            self.started_at = time.time()
            for stream in self.streams:
                stream.start()
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def snapshots(self):
        if not all(stream.active for stream in self.streams):
            raise RuntimeError("A microphone stopped. Restart the worker to recalibrate.")
        return [ring.snapshot() for ring in self.rings]


def aligned_window(snapshots, clock, rate, seconds):
    if len(snapshots) != len(clock["channels"]):
        raise ValueError("Clock channel count differs from the active microphones")
    # Reserve enough samples for either sign of device offset, then interpolate
    # each device onto A's sample-time axis.
    common_end = min((start + len(audio)) / rate for start, audio in snapshots)
    allowance = max(abs(row["offset_seconds"]) + abs(row["drift"]) * common_end
                    for row in clock["channels"]) + .02
    end = common_end - allowance
    times = np.arange(round((end - seconds) * rate), round(end * rate)) / rate
    if len(times) == 0:
        return None
    aligned = []
    for (start, audio), channel in zip(snapshots, clock["channels"]):
        indices = (times + channel["offset_seconds"] + channel["drift"] * times) * rate - start
        if not len(audio) or indices.min() < 0 or indices.max() >= len(audio) - 1:
            return None
        aligned.append(np.interp(indices, np.arange(len(audio)), audio))
    return end, aligned


class Recognizer:
    def __init__(self, database, threshold, margin, window_seconds, speech_gap_seconds):
        if not np.isfinite([threshold, margin]).all() or not -1 <= threshold <= 1 or not 0 <= margin <= 2:
            raise ValueError("Invalid speaker threshold or margin")
        self.database = database
        self.threshold = threshold
        self.margin = margin
        self.min_speech_seconds = min(2.0, round(window_seconds * .8, 3))
        self.speech_gap_seconds = speech_gap_seconds
        self.next_sample = None
        self.turn_start = None
        self.last_voice_end = None
        self.confirmed = None
        from webrtcvad import Vad
        from src.speakers import SpeakerEngine
        self.vad = Vad(2)
        self.engine = SpeakerEngine()
        if not self.engine.database(database)["speakers"]:
            raise ValueError("Enroll Project A user IDs before starting the live node")

    def identify(self, audio, rate):
        # Reuse the exact enrollment/verification preprocessing and model.
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "window.wav"
            wavfile.write(file, rate, audio.astype("float32"))
            return self.engine.identify(file, self.database, self.threshold, self.margin,
                                        min_speech_seconds=self.min_speech_seconds)

    def has_speech(self, block, rate):
        if rate not in (16000, 32000, 48000):
            divisor = gcd(rate, 16000)
            converted = resample_poly(block, 16000 // divisor, rate // divisor)
            block = np.zeros(480)
            block[:min(len(converted), 480)] = converted[:480]
            rate = 16000
        pcm = (np.clip(block, -1, .999969) * 32768).astype("<i2")
        return self.vad.is_speech(pcm.tobytes(), rate)

    def identify_window(self, audio, rate, end_sample):
        """Latch a speaker within one continuous turn and ignore overlapping old frames."""
        start = end_sample - len(audio)
        frame_size = round(rate * .03)
        gap = round(rate * self.speech_gap_seconds)
        if self.next_sample is None or self.next_sample < start:
            # An unobserved part of the stream might have contained a turn boundary.
            self.next_sample = start
            self.turn_start = self.last_voice_end = self.confirmed = None
        for begin in range(self.next_sample, end_sample - frame_size + 1, frame_size):
            finish = begin + frame_size
            self.next_sample = finish
            if self.has_speech(audio[begin - start:finish - start], rate):
                if self.turn_start is None:
                    self.turn_start = begin
                self.last_voice_end = finish
            elif self.last_voice_end is not None and finish - self.last_voice_end >= gap:
                self.turn_start = self.last_voice_end = self.confirmed = None
        if self.turn_start is None:
            return dict(status="insufficient_speech", user_id=""), 0
        offset = max(0, self.turn_start - start)
        if self.confirmed is not None:
            return self.confirmed, offset
        if self.last_voice_end <= start:
            return dict(status="pending", user_id=""), offset
        result = self.identify(audio[offset:self.last_voice_end - start], rate)
        if result["status"] == "recognized":
            self.confirmed = result
        elif result["status"] == "insufficient_speech":
            # A new person may be speaking. Publish an empty ID until there is a score.
            result["status"] = "pending"
        return result, offset


def observation(audio, rate, positions, recognizer, timestamp, now,
                enable_speaker=True, enable_localization=True,
                speaker_error=None, localization_error=None, window_end_sample=None):
    if not 0 <= now - timestamp <= 3:
        return None, dict(status="stale")
    speaker, location = evaluate(audio, rate, positions, recognizer,
                                 enable_speaker, enable_localization,
                                 speaker_error, localization_error, window_end_sample=window_end_sample)
    if not 0 <= time.time() - timestamp <= 3:
        return None, dict(status="stale_after_inference")
    payload = combined_identity(speaker, location, timestamp)
    status = "partial"
    if payload is not None:
        status = "observed"
    elif speaker["status"] == "error" or location["status"] == "error":
        status = "degraded"
    elif speaker["status"] in ("pending", "insufficient_speech", "unusable_audio"):
        status = "insufficient_speech"
    return payload, dict(status=status, direction=location, recognition=speaker)


def raw_window(snapshots, rate, seconds):
    start, audio = snapshots[0]
    size = round(seconds * rate)
    if len(audio) < size:
        return None
    return (start + len(audio)) / rate, [audio[-size:]]


def run_live(config_path, emit, stop=None):
    config = read_json(config_path)
    enable_speaker, enable_localization = switches(config)
    count = config.get("microphone_count", 4)
    if isinstance(count, bool) or not isinstance(count, int) or count not in (3, 4):
        raise ValueError("microphone_count must be 3 or 4")
    interval = float(config.get("update_seconds", .5))
    if not np.isfinite(interval) or interval < .1:
        raise ValueError("update_seconds must be finite and at least 0.1")
    window = DEFAULT_WINDOW_SECONDS
    if enable_speaker or enable_localization:
        window = float(config.get("window_seconds", DEFAULT_WINDOW_SECONDS))
        if not MIN_WINDOW_SECONDS <= window <= 10:
            raise ValueError(f"window_seconds must be between {MIN_WINDOW_SECONDS:g} and 10")
    positions, recognizer = None, None
    speaker_error, localization_error = None, None
    if enable_localization:
        try:
            geometry = read_json(ROOT / config["array_config"])
            if geometry.get("geometry_measured") is not True:
                raise ValueError("Measure array coordinates and set geometry_measured=true")
            positions = validate_positions(geometry["positions_m"], count)
        except Exception as error:
            localization_error = str(error)
    if enable_speaker:
        try:
            speech_gap = float(config.get("speech_gap_seconds", DEFAULT_SPEECH_GAP_SECONDS))
            if not .2 <= speech_gap <= 5:
                raise ValueError("speech_gap_seconds must be between 0.2 and 5")
            recognizer = Recognizer(ROOT / config["database"], config.get("threshold", .65),
                                    config.get("margin", .08), window, speech_gap)
        except Exception as error:
            speaker_error = str(error)
    idle_features = dict(
        recognition=dict(status="error" if enable_speaker else "disabled", user_id="",
                         reason=speaker_error),
        direction=dict(status="error" if enable_localization else "disabled", azimuth_deg=None,
                       reason=localization_error))
    if positions is None and recognizer is None:
        emit(dict(kind="status", phase="degraded" if enable_speaker or enable_localization else "disabled"))
        while not (stop and stop()):
            emit(dict(kind="features", data=dict(timestamp=time.time(), source="voice_detect", **idle_features)))
            time.sleep(interval)
        return
    rate = int(config.get("sample_rate_hz", 48000))
    duration = float(config.get("calibration_seconds", 8)) if positions is not None else 0
    max_age = float(config.get("max_clock_age_seconds", 0)) if positions is not None else 0
    if not np.isfinite([rate, duration, max_age]).all() or rate < 16000 or max_age < 0:
        raise ValueError("Invalid sample rate or clock age")
    if positions is not None and not 5 <= duration <= 30:
        raise ValueError("calibration_seconds must be between 5 and 30")
    required = count if positions is not None else 1
    devices = config.get("devices", [])
    if len(devices) < required:
        raise ValueError(f"Configure at least {required} devices in A/B/C/D order")
    clock = None
    with LiveCapture(devices[:required], rate, max(duration + 2, window + 3)) as capture:
        if positions is not None:
            emit(dict(kind="status", phase="calibrating",
                      message="Keep the broadband calibration source stationary until phase=ready"))
            deadline = time.monotonic() + duration + 10
            while True:
                if stop and stop():
                    return
                snapshots = capture.snapshots()
                if min(start + len(audio) for start, audio in snapshots) >= round(duration * rate):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Calibration audio did not arrive before timeout")
                time.sleep(.05)
            try:
                if any(start != 0 for start, _ in snapshots):
                    raise ValueError("Calibration audio buffer expired")
                distances = config["calibration_distances_m"][:count]
                clock = fit_clock([audio for _, audio in snapshots], rate, .5, duration - .2, distances)
            except Exception as error:
                localization_error = str(error)
        emit(dict(kind="status", phase="ready" if clock is not None or recognizer is not None else "degraded",
                  clock=clock, speaker_error=speaker_error, localization_error=localization_error))
        last_phase = None
        while not (stop and stop()):
            tick = time.monotonic()
            snapshots = capture.snapshots()
            window_localization_error = localization_error
            result = aligned_window(snapshots, clock, rate, window) if clock else raw_window(snapshots, rate, window)
            if result is None and clock is not None:
                result = raw_window(snapshots, rate, window)
                window_localization_error = localization_error or "Aligned localization window is unavailable"
            if result is not None:
                end, audio = result
                if max_age and clock is not None and end > duration + max_age:
                    window_localization_error = "Clock calibration validity expired; restart to recalibrate"
                timestamp = capture.started_at + end
                payload, diagnostics = observation(
                    audio, rate, positions, recognizer, timestamp, time.time(),
                    enable_speaker, enable_localization, speaker_error, window_localization_error,
                    window_end_sample=round(end * rate))
                if payload is not None:
                    emit(dict(kind="identity", data=payload))
                if not diagnostics["status"].startswith("stale"):
                    emit(dict(kind="features", data=dict(
                        timestamp=timestamp, source="voice_detect",
                        recognition=diagnostics["recognition"], direction=diagnostics["direction"])))
                if diagnostics["status"] != last_phase:
                    emit(dict(kind="status", phase=diagnostics["status"]))
                    last_phase = diagnostics["status"]
            time.sleep(max(0, interval - (time.monotonic() - tick)))
