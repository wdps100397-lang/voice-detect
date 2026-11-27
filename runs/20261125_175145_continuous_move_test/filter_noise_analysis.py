"""Select playback of the known noise file, preserving recording timestamps."""
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, resample_poly, sosfiltfilt

from src.common import ROOT, load_audio, load_tracks, read_json, save_json
from src.localization import direction, measured_positions


folder = Path(__file__).resolve().parent
output = folder / "noise_only"
output.mkdir(exist_ok=True)
rate, tracks = load_tracks([folder / (label + ".wav") for label in "ABCD"])
clock = read_json(folder / "clock.json")
geometry = read_json(folder / "array.json")
positions = measured_positions(geometry, 4)
_, reference = load_audio(ROOT / "assets/test_audio/broadband_30s.wav", rate)

match_rate = 16000
match_filter = butter(4, [800, 6500], btype="bandpass", fs=match_rate, output="sos")
reference = sosfiltfilt(match_filter, resample_poly(reference, 1, 3))
match_tracks = [sosfiltfilt(match_filter, resample_poly(track, 1, 3)) for track in tracks]
template_size = round(.1 * match_rate)
energy = np.r_[0, np.cumsum(reference ** 2)]
reference_energy = np.maximum(energy[template_size:] - energy[:-template_size], 0)
valid_reference = reference_energy > reference_energy.max() * .05

filter_sos = butter(4, [500, 7000], btype="bandpass", fs=rate, output="sos")
filtered = [sosfiltfilt(filter_sos, track) for track in tracks]
duration = min(map(len, tracks)) / rate
selection = []
blocks = []

for start in np.arange(0, duration - .5, .5):
    # Three independent matching channels must agree on playback-file time.
    coefficients, reference_times = [], []
    for track in match_tracks:
        begin = round((start + .2) * match_rate)
        segment = track[begin:begin + template_size]
        correlation = fftconvolve(reference, segment[::-1], mode="valid")
        denominator = np.sqrt(reference_energy * max(float(segment @ segment), 1e-20))
        score = np.zeros_like(correlation)
        score[valid_reference] = np.abs(correlation[valid_reference]) / denominator[valid_reference]
        peak = int(np.argmax(score))
        coefficients.append(float(score[peak]))
        reference_times.append(peak / match_rate)
    confident = np.array(coefficients) >= .18
    times = np.array(reference_times)[confident]
    keep = bool(confident.sum() >= 3 and np.ptp(times) <= .15)
    selection.append(dict(start_seconds=float(start), end_seconds=float(start + .5),
                          noise_detected=keep, coefficients=coefficients,
                          reference_times=reference_times))
    if keep:
        blocks.append((float(start), float(start + .5)))

mask = np.zeros(min(map(len, tracks)))
for start, end in blocks:
    first, last = round(start * rate), round(end * rate)
    mask[first:last] = 1
for label, track in zip("ABCD", filtered):
    # Keep the original sample timeline. Rejected blocks become silence.
    values = track[:len(mask)] * mask
    wavfile.write(output / (label + ".wav"), rate,
                  np.clip(np.rint(values * 32768), -32768, 32767).astype("<i2"))

checks = []
for start in np.arange(10, duration - 2, 2):
    selected = [(a, b) for a, b in blocks if a >= start and b <= start + 2]
    if not selected:
        checks.append(dict(interval_seconds=[float(start), float(start + 2)],
                           noise_seconds=0, status="no_reference_noise"))
        continue
    pieces = [[] for _ in tracks]
    for begin, end in selected:
        # Select only the middle 0.3 s of each matched block, avoiding edges.
        times = np.arange(round((begin + .1) * rate), round((end - .1) * rate)) / rate
        for index, (track, row) in enumerate(zip(filtered, clock["channels"])):
            corrected = (times + row["offset_seconds"] + row["drift"] * times) * rate
            pieces[index].append(np.interp(corrected, np.arange(len(track)), track))
    aligned = [np.concatenate(chunks) for chunks in pieces]
    result = direction(aligned, rate, positions)
    result.update(interval_seconds=[float(start), float(start + 2)],
                  noise_seconds=round(sum(b - a for a, b in selected), 2))
    checks.append(result)

report = dict(method="Known-waveform matching on at least three channels, then identical 500-7000 Hz filtering",
              matching_coefficient_threshold=.18, maximum_reference_time_spread_seconds=.15,
              original_clock_retained=True, retained_seconds=len(blocks) * .5,
              selection=selection, direction_checks=checks)
save_json(output / "analysis.json", report)
print("Retained", len(blocks) * .5, "seconds of known-noise playback out of", round(duration, 1))
for row in checks:
    print(row["interval_seconds"], row["noise_seconds"], row["status"],
          row.get("azimuth_deg"), row.get("best_candidate_azimuth_deg"))
