"""File operations and analysis shared by the local testing interface."""
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from src.common import ROOT, load_audio, save_json
from src.localization import calibration, calibration_distances, locate, measured_positions, robot_frame


def new_run(kind):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder = ROOT / "runs" / f"{stamp}_ui_{kind}"
    folder.mkdir(parents=True)
    return folder


def speaker_clip(folder, source, start, end, gain):
    rate, audio = load_audio(source, 16000)
    duration = len(audio) / rate
    end = duration if end is None else end
    if not 0 <= start < end <= duration + 1 / rate:
        raise ValueError(f"發話區間需在 0～{duration:.2f} 秒內")
    segment = audio[round(start * rate):round(end * rate)]
    if len(segment) == 0 or np.max(np.abs(segment)) >= .999:
        raise ValueError("原音已削波，或選取的區間太短")
    segment = segment * 10 ** (gain / 20)
    if np.max(np.abs(segment)) >= .999:
        raise ValueError("放大後會削波，請降低數位增益")
    clip = folder / "speech.wav"
    wavfile.write(clip, rate, np.round(segment * 32768).astype("<i2"))
    return clip


def tracks_in(folder, count):
    files = [Path(folder) / f"{label}.wav" for label in "ABCD"[:count]]
    missing = [path.name for path in files if not path.is_file()]
    if missing:
        raise ValueError("資料夾缺少 " + "、".join(missing))
    return files


def localize_run(folder, count, geometry, distances, cal_start, cal_end, test_start, test_end, expected):
    files = tracks_in(folder, count)
    positions = measured_positions(geometry, count)
    distances = calibration_distances(geometry, positions, distances)
    robot_frame(geometry, positions)
    output = new_run("direction")
    geometry_path, clock_path = output / "array.json", output / "clock.json"
    save_json(geometry_path, geometry)
    clock = calibration(files, cal_start, cal_end, distances, clock_path)
    result = locate(files, geometry_path, clock_path, test_start, test_end)
    result.update(source_folder=str(folder), interval_seconds=[test_start, test_end],
                  expected_azimuth_deg=expected)
    if expected is not None and result["status"] == "ok":
        result["absolute_error_deg"] = abs((result["azimuth_deg"] - expected + 180) % 360 - 180)
    save_json(output / "direction.json", result)
    return result, clock, output
