"""GCC-PHAT clock calibration and horizontal far-field direction estimation."""
from itertools import combinations

import numpy as np
from scipy.fft import next_fast_len

from src.common import fingerprint, load_tracks, read_json, save_json


def correlation(first, second, rate, max_delay):
    size = next_fast_len(len(first) + len(second))
    window = np.hanning(len(first))
    a = np.fft.rfft((first - first.mean()) * window, size)
    b = np.fft.rfft((second - second.mean()) * window, size)
    cross = a * b.conj()
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    mask = (frequencies >= 150) & (frequencies <= min(8000, rate * .45))
    cross = cross / np.maximum(abs(cross), 1e-12) * mask
    interpolation = 8
    values = np.fft.irfft(cross, size * interpolation)
    limit = min(int(np.ceil(max_delay * rate * interpolation)), len(values) // 2 - 1)
    values = np.concatenate((values[-limit:], values[:limit + 1]))
    lags = np.arange(-limit, limit + 1) / (rate * interpolation)
    return lags, values


def calibration(files, start, end, distances, output):
    rate, tracks = load_tracks(files)
    result = fit_clock(tracks, rate, start, end, distances)
    result["fingerprints"] = [fingerprint(p) for p in files]
    save_json(output, result)
    return result


def fit_clock(tracks, rate, start, end, distances):
    if len(tracks) != 3 or len(distances) != 3 or not np.isfinite(distances).all() or min(distances) <= 0:
        raise ValueError("Use three tracks and three finite positive calibration distances")
    if start < 0 or end <= start or end > min(map(len, tracks)) / rate:
        raise ValueError("Calibration interval is outside the recordings")
    if end - start < 4 or len(distances) != 3 or min(distances) <= 0:
        raise ValueError("Use at least 4 seconds and three measured positive source distances")
    rows = []
    for channel in (1, 2):
        times, delays = [], []
        for time in np.arange(start, end - .5, .5):
            begin, stop = int(time * rate), int((time + .5) * rate)
            a, b = tracks[channel][begin:stop], tracks[0][begin:stop]
            if min(np.sqrt(np.mean(a*a)), np.sqrt(np.mean(b*b))) < 1e-5:
                continue
            lags, values = correlation(a, b, rate, .2)
            peak = int(np.argmax(values))
            if peak in (0, len(values) - 1):
                continue
            times.append(time + .25)
            delays.append(lags[peak] - (distances[channel] - distances[0]) / 343)
        if len(times) < 6:
            raise ValueError("Too few usable calibration windows. Use a continuous broadband source.")
        times, delays = np.asarray(times), np.asarray(delays)
        slope, offset = np.polyfit(times, delays, 1)
        residuals = delays - (offset + slope * times)
        keep = abs(residuals - np.median(residuals)) <= max(3 * np.median(abs(residuals)), .00005)
        if keep.sum() < 6:
            raise ValueError("Unstable calibration")
        slope, offset = np.polyfit(times[keep], delays[keep], 1)
        # Evaluate every usable window, including rejected outliers.
        error = float(np.percentile(abs(delays - (offset + slope * times)), 95))
        if error > .0001 or abs(slope) > .002:
            raise ValueError(f"Unstable clock estimate for channel {channel}: p95={error:.6f}s")
        rows.append(dict(offset_seconds=float(offset), drift=float(slope), residual_p95_seconds=error))
    result = dict(schema=1, sample_rate=rate,
                  interval=[start, end], channels=[dict(offset_seconds=0., drift=0.), *rows])
    return result


def direction(audio, rate, positions):
    angles = np.arange(-180, 180)
    # Project A convention: forward=0, left positive, right negative.
    vectors = np.column_stack((-np.sin(np.deg2rad(angles)), np.cos(np.deg2rad(angles))))
    scores = np.zeros(360)
    frame_size = round(.1 * rate)
    windows = 0
    for begin in range(0, len(audio[0]) - frame_size + 1, frame_size):
        segments = [track[begin:begin + frame_size] for track in audio]
        if min(np.sqrt(np.mean(x*x)) for x in segments) < 1e-5:
            continue
        for i, j in combinations(range(3), 2):
            baseline = positions[i] - positions[j]
            limit = np.linalg.norm(baseline) / 343
            lags, values = correlation(segments[i], segments[j], rate, limit)
            expected = -(vectors @ baseline) / 343
            scores += np.interp(expected, lags, values)
        windows += 1
    if not windows or np.max(scores) <= 0:
        return dict(status="no_signal", azimuth_deg=None)
    peak = int(np.argmax(scores))
    angle = int(angles[peak])
    separation = abs((angles - angle + 180) % 360 - 180)
    competitor = np.max(scores[separation >= 20])
    margin = float((scores[peak] - competitor) / max(abs(scores[peak]), 1e-12))
    # A quality indicator, not a probability of a correct direction.
    return dict(status="ok" if margin >= .05 else "ambiguous",
                azimuth_deg=angle if margin >= .05 else None,
                best_candidate_azimuth_deg=angle, peak_margin=margin, windows=windows)


def locate(files, config_path, calibration_path, start, end):
    config = read_json(config_path)
    if config.get("geometry_measured") is not True:
        raise ValueError("Measure microphone coordinates and set geometry_measured=true first")
    positions = np.asarray(config["positions_m"], dtype=float)
    if positions.shape != (3, 2) or not np.isfinite(positions).all():
        raise ValueError("positions_m must contain three finite [x, y] coordinates")
    if np.linalg.matrix_rank(positions[1:] - positions[0]) < 2:
        raise ValueError("Use three non-collinear microphone positions")
    rate, tracks = load_tracks(files)
    clock = read_json(calibration_path)
    if clock["fingerprints"] != [fingerprint(p) for p in files]:
        raise ValueError("Calibration belongs to another recording. Calibrate this continuous recording first.")
    if start < 0 or end <= start:
        raise ValueError("Invalid analysis interval")
    if start < clock["interval"][0] or end > clock["interval"][1] + config.get("max_extrapolation_seconds", 30):
        raise ValueError("Analysis is too far from calibration. Use a longer calibration or a nearer segment.")
    times = np.arange(round(start * rate), round(end * rate)) / rate
    aligned = []
    for audio, channel in zip(tracks, clock["channels"]):
        indices = (times + channel["offset_seconds"] + channel["drift"] * times) * rate
        if indices.min() < 0 or indices.max() >= len(audio) - 1:
            raise ValueError("Requested interval extends past the corrected audio")
        aligned.append(np.interp(indices, np.arange(len(audio)), audio))
    return direction(aligned, rate, positions)
