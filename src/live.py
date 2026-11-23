"""Continuous Windows acquisition and real speaker/azimuth observations."""
from collections import deque
from contextlib import ExitStack
from pathlib import Path
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.io import wavfile
import tempfile

from src.common import ROOT, read_json
from src.localization import direction, fit_clock
from src.speakers import SpeakerEngine


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
        if len(devices) != 3 or len(set(devices)) != 3 or any(isinstance(device, bool) or not isinstance(device, int) or device < 0 for device in devices):
            raise ValueError("Select three distinct WASAPI input devices in physical A/B/C order")
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
    def __init__(self, database, threshold, margin):
        if not np.isfinite([threshold, margin]).all() or not -1 <= threshold <= 1 or not 0 <= margin <= 2:
            raise ValueError("Invalid speaker threshold or margin")
        self.database = database
        self.threshold = threshold
        self.margin = margin
        self.engine = SpeakerEngine()
        if not self.engine.database(database)["speakers"]:
            raise ValueError("Enroll Project A user IDs before starting the live node")

    def identify(self, audio, rate):
        # Reuse the exact enrollment/verification preprocessing and model.
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "window.wav"
            wavfile.write(file, rate, audio.astype("float32"))
            return self.engine.identify(file, self.database, self.threshold, self.margin)


def observation(audio, rate, positions, recognizer, timestamp, now):
    if not 0 <= now - timestamp <= 3:
        return None, dict(status="stale")
    speaker = recognizer.identify(audio[0], rate)
    # During silence, do not renew the previous speaker with a new timestamp.
    if speaker["status"] == "unusable_audio":
        return None, dict(status="insufficient_speech", recognition=speaker)
    location = direction(audio, rate, positions)
    if location["status"] != "ok":
        return None, dict(status=location["status"], direction=location, recognition=speaker)
    payload = dict(user_id=speaker["user_id"], location="", azimuth_deg=location["azimuth_deg"],
                   timestamp=timestamp, source="voice_detect")
    # Inference may have used up the receiving system's freshness interval.
    if not 0 <= time.time() - timestamp <= 3:
        return None, dict(status="stale_after_inference")
    return payload, dict(status="observed", direction=location, recognition=speaker)


def run_live(config_path, emit, stop=None):
    config = read_json(config_path)
    geometry = read_json(ROOT / config["array_config"])
    positions = np.asarray(geometry["positions_m"], dtype=float)
    if geometry.get("geometry_measured") is not True or positions.shape != (3, 2):
        raise ValueError("Measure array coordinates and set geometry_measured=true")
    if not np.isfinite(positions).all() or np.linalg.matrix_rank(positions[1:] - positions[0]) < 2:
        raise ValueError("Microphones must form a measured non-collinear triangle")
    rate = int(config.get("sample_rate_hz", 48000))
    window = float(config.get("window_seconds", 2.5))
    duration = float(config.get("calibration_seconds", 8))
    interval = float(config.get("update_seconds", .5))
    max_age = float(config.get("max_clock_age_seconds", 0))
    if not np.isfinite([rate, window, duration, interval, max_age]).all() or rate < 16000 or not 2.1 <= window <= 10 or not 4 <= duration <= 30 or interval < .1 or max_age < 0:
        raise ValueError("Invalid sample rate, analysis window or calibration timing")
    recognizer = Recognizer(ROOT / config["database"], config.get("threshold", .65), config.get("margin", .08))
    emit(dict(kind="status", phase="calibrating",
              message="Keep the calibration broadband source stationary until phase=ready"))
    with LiveCapture(config["devices"], rate, max(duration + 2, window + 3)) as capture:
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
        if any(start != 0 for start, _ in snapshots):
            raise RuntimeError("Calibration audio buffer expired")
        clock = fit_clock([audio for _, audio in snapshots], rate, .5, duration - .2,
                          config["calibration_distances_m"])
        emit(dict(kind="status", phase="ready", clock=clock))
        last_phase = "ready"
        while not (stop and stop()):
            tick = time.monotonic()
            snapshots = capture.snapshots()
            result = aligned_window(snapshots, clock, rate, window)
            if result is not None:
                end, audio = result
                if max_age and end > duration + max_age:
                    raise RuntimeError("Clock calibration validity expired. Restart with a stationary calibration source.")
                payload, diagnostics = observation(audio, rate, positions, recognizer,
                                                    capture.started_at + end, time.time())
                if payload is not None:
                    emit(dict(kind="identity", data=payload))
                if diagnostics["status"] != last_phase:
                    emit(dict(kind="status", phase=diagnostics["status"],
                              clock_age_seconds=end - duration))
                    last_phase = diagnostics["status"]
            time.sleep(max(0, interval - (time.monotonic() - tick)))
