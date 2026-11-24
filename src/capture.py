"""List Windows capture inputs and make a short, separate recording per input."""

import argparse
from array import array
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import wave

import sounddevice as sd


def list_inputs():
    hosts = sd.query_hostapis()
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            host = hosts[device["hostapi"]]["name"]
            print(f"{index:>3}  {host:<16}  {device['name']}")


class Track:
    def __init__(self, label, index):
        device = sd.query_devices(index, "input")
        self.label = label
        self.index = index
        self.name = device["name"]
        self.rate = round(device["default_samplerate"])
        self.audio = bytearray()
        self.flags = {}

    def collect(self, data, frames, timing, status):
        self.audio.extend(data)
        if status:
            flag = str(status)
            self.flags[flag] = self.flags.get(flag, 0) + 1

    def save(self, folder):
        samples = array("h")
        samples.frombytes(self.audio)
        path = folder / f"{self.label}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, self.rate, 0, "NONE", "not compressed"))
            wav.writeframes(self.audio)
        return {
            "track": self.label,
            "index": self.index,
            "device": self.name,
            "sample_rate_hz": self.rate,
            "frames": len(samples),
            "seconds": round(len(samples) / self.rate, 3),
            "peak_pcm": max(map(abs, samples), default=0),
            "at_pcm_limits": sum(s in (-32768, 32767) for s in samples),
            "stream_flags": self.flags,
        }


def capture_to_directory(indices, seconds, exclusive=False, *, samplerate=None,
                         on_started=None, on_progress=None, stop=None):
    if not 1 <= len(indices) <= 4 or len(set(indices)) != len(indices):
        raise ValueError("Select one to four distinct input devices")
    if stop and stop.is_set():
        raise InterruptedError("已停止錄音")
    tracks = [Track(label, index) for label, index in zip("ABCD", indices)]
    with ExitStack() as stack:
        streams = []
        for track in tracks:
            try:
                stream = sd.RawInputStream(
                    device=track.index, channels=1, samplerate=samplerate or track.rate,
                    dtype="int16", callback=track.collect,
                    extra_settings=sd.WasapiSettings(exclusive=True) if exclusive else None,
                )
            except sd.PortAudioError as error:
                raise RuntimeError(f"{track.label} [{track.index}] {track.name}: {error}") from error
            # Register cleanup as soon as the device is opened.
            stack.callback(stream.close)
            streams.append(stream)
            if samplerate:
                track.rate = samplerate
        started_at_unix = time.time()
        for track, stream in zip(tracks, streams):
            try:
                stream.start()
            except sd.PortAudioError as error:
                raise RuntimeError(f"Could not start {track.label}: {error}") from error
        started = time.monotonic()
        if on_started:
            on_started()
        while time.monotonic() - started < seconds and all(stream.active for stream in streams):
            if stop and stop.is_set():
                raise InterruptedError("已停止錄音")
            if on_progress:
                on_progress(time.monotonic() - started)
            time.sleep(0.1)
        inactive = [track.label for track, stream in zip(tracks, streams) if not stream.active]

    if stop and stop.is_set():
        raise InterruptedError("已停止錄音")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder = Path(__file__).resolve().parents[1] / "runs" / f"{stamp}_mic_check"
    folder.mkdir(parents=True)
    results = [track.save(folder) for track in tracks]
    needs_review = bool(inactive)
    for result in results:
        needs_review |= (
            result["peak_pcm"] == 0 or result["at_pcm_limits"] > 0
            or bool(result["stream_flags"]) or result["seconds"] < seconds - 0.5
        )
    summary = {
        "requested_seconds": seconds,
        "microphone_count": len(tracks),
        "started_at_unix": started_at_unix,
        "exclusive_mode": exclusive,
        "inactive_streams": inactive,
        "needs_review": needs_review,
        "tracks": results,
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder, summary


def record(indices, seconds, exclusive=False):
    def announce():
        print(f"Recording for {seconds} seconds. Ambient or test sounds are sufficient.", flush=True)

    folder, summary = capture_to_directory(indices, seconds, exclusive, on_started=announce)
    print(folder)
    for result in summary["tracks"]:
        print(f"{result['track']} [{result['index']}] {result['device']} | "
              f"{result['seconds']:.3f} s | peak {result['peak_pcm']}/32768 | "
              f"PCM limits {result['at_pcm_limits']} | stream flags {result['stream_flags']}")
    if summary["needs_review"]:
        print("Review silent, clipped, short or flagged tracks and listen to all WAVs.")
        return 1
    print("All tracks contain samples. Listen to each WAV to confirm the speech is audible.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, nargs="+", help="1 to 4 input indices in A/B/C/D order")
    parser.add_argument("--exclusive", action="store_true", help="Use WASAPI exclusive capture")
    parser.add_argument("--seconds", type=int, default=20, choices=range(5, 61), metavar="5..60")
    args = parser.parse_args()
    if args.devices is None:
        list_inputs()
        return 0
    if len(args.devices) > 4 or len(set(args.devices)) != len(args.devices) or min(args.devices) < 0:
        parser.error("--devices needs 1 to 4 distinct, nonnegative input indices")
    try:
        return record(args.devices, args.seconds, args.exclusive)
    except (sd.PortAudioError, OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
