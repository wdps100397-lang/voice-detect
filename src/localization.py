"""GCC-PHAT clock calibration and three-dimensional far-field direction estimation."""
from itertools import combinations

import numpy as np
from scipy.fft import next_fast_len

from src.common import fingerprint, load_tracks, read_json, save_json

SOUND_SPEED_MPS = 343
MIN_PEAK_MARGIN = .05


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
    if len(tracks) not in (3, 4) or len(distances) != len(tracks) or not np.isfinite(distances).all() or min(distances) <= 0:
        raise ValueError("Use three or four tracks with one finite positive calibration distance per track")
    if start < 0 or end <= start or end > min(map(len, tracks)) / rate:
        raise ValueError("Calibration interval is outside the recordings")
    if end - start < 4:
        raise ValueError("Use at least 4 seconds of calibration audio")
    rows = []
    for channel in range(1, len(tracks)):
        times, delays = [], []
        # Short windows keep independent USB clock drift from smearing the peak.
        for time in np.arange(start, end - .1, .1):
            begin, stop = round(time * rate), round((time + .1) * rate)
            a, b = tracks[channel][begin:stop], tracks[0][begin:stop]
            if min(np.sqrt(np.mean(a*a)), np.sqrt(np.mean(b*b))) < 1e-5:
                continue
            lags, values = correlation(a, b, rate, .2)
            peak = int(np.argmax(values))
            if peak in (0, len(values) - 1):
                continue
            times.append(time + .05)
            delays.append(lags[peak] - (distances[channel] - distances[0]) / SOUND_SPEED_MPS)
        if len(times) < 6:
            raise ValueError("Too few usable calibration windows. Use a continuous broadband source.")
        times, delays = np.asarray(times), np.asarray(delays)
        slope, offset = np.polyfit(times, delays, 1)
        for _ in range(3):
            residuals = delays - (offset + slope * times)
            keep = abs(residuals - np.median(residuals)) <= max(3 * np.median(abs(residuals)), .00005)
            if keep.sum() < 6:
                raise ValueError("Unstable calibration")
            slope, offset = np.polyfit(times[keep], delays[keep], 1)
        # Evaluate every usable window, including rejected outliers.
        error = float(np.percentile(abs(delays - (offset + slope * times)), 95))
        if error > .0001 or abs(slope) > .002:
            label = "ABCD"[channel]
            raise ValueError(f"{label} 與 A 的錄音時間校準不穩（殘差 {error * 1000:.3f} ms）。"
                             "請將喇叭固定在校準位置持續播放沙沙聲，重新錄音。")
        rows.append(dict(offset_seconds=float(offset), drift=float(slope), residual_p95_seconds=error))
    result = dict(schema=1, sample_rate=rate,
                  interval=[start, end], channels=[dict(offset_seconds=0., drift=0.), *rows])
    return result


def source_vectors(azimuths, elevations):
    azimuth, elevation = np.meshgrid(np.deg2rad(azimuths), np.deg2rad(elevations))
    horizontal = np.cos(elevation)
    # Array axes: X right, Y forward, Z up. Forward=0, left positive.
    return np.stack((-np.sin(azimuth) * horizontal, np.cos(azimuth) * horizontal,
                     np.sin(elevation)), axis=-1)


def direction_scores(vectors, baselines, correlations):
    scores = np.zeros(vectors.shape[:-1])
    for baseline, (lags, values) in zip(baselines, correlations):
        expected = -(vectors @ baseline) / SOUND_SPEED_MPS
        scores += np.interp(expected, lags, values)
    return scores


def wrap_degrees(angle):
    return (angle + 180) % 360 - 180


def align_tracks(tracks, rate, clock, start, end, *, starts=None, positions=None, packet_samples=0):
    """Apply clock drift and optionally recover measured whole-packet timing jumps."""
    starts = [0] * len(tracks) if starts is None else starts
    times = np.arange(round(start * rate), round(end * rate)) / rate
    if not len(times) or len(clock["channels"]) != len(tracks):
        raise ValueError("Invalid synchronization window or channel count")

    def sample(channel, target_times, shift=0):
        row = clock["channels"][channel]
        indices = (target_times + row["offset_seconds"] + row["drift"] * target_times) * rate
        indices += shift - starts[channel]
        track = tracks[channel]
        if not len(track) or indices.min() < 0 or indices.max() >= len(track) - 1:
            raise ValueError("Requested interval extends past the corrected audio")
        left = np.floor(indices).astype(np.int64)
        weight = indices - left
        return track[left] * (1 - weight) + track[left + 1] * weight

    aligned = [sample(index, times) for index in range(len(tracks))]
    if packet_samples == 0:
        return aligned, None
    if isinstance(packet_samples, bool) or not isinstance(packet_samples, int) or packet_samples <= 0:
        raise ValueError("clock_jump_samples must be a positive integer or zero")
    positions = validate_positions(positions, len(tracks))
    pairs = list(combinations(range(len(tracks)), 2))
    limits = [np.linalg.norm(positions[i] - positions[j]) / SOUND_SPEED_MPS for i, j in pairs]
    if packet_samples <= 2 * (max(limits) * rate + 2):
        raise ValueError("Array is too large to distinguish packet jumps from acoustic delays")
    frame_size = round(.1 * rate)
    valid, total, corrections = 0, 0, []
    for begin in range(0, len(times) - frame_size + 1, frame_size):
        stop = begin + frame_size
        frame_times = times[begin:stop]
        center = (frame_times[0] + frame_times[-1]) / 2
        context_times = np.arange(round((center - .15) * rate), round((center + .15) * rate)) / rate
        total += 1
        shifts = [0]
        try:
            reference = sample(0, context_times)
            for index in range(1, len(tracks)):
                context = sample(index, context_times)
                lags, values = correlation(context, reference, rate, .1)
                lag = lags[int(np.argmax(values))]
                shifts.append(int(np.rint(lag * rate / packet_samples)) * packet_samples)
            frames = [sample(index, frame_times, shift) for index, shift in enumerate(shifts)]
            usable = min(np.sqrt(np.mean(frame * frame)) for frame in frames) >= 1e-5
            for (i, j), limit in zip(pairs, limits):
                lags, values = correlation(frames[i], frames[j], rate, .02)
                peak = int(np.argmax(values))
                usable &= abs(lags[peak]) <= limit + 2 / rate
        except ValueError:
            usable = False
        corrections.append(dict(start_seconds=round(float(frame_times[0]), 4),
                                shifts_samples=shifts, usable=bool(usable)))
        if usable:
            valid += 1
            for index, frame in enumerate(frames):
                aligned[index][begin:stop] = frame
        else:
            # Preserve the reference channel for the existing speaker recognizer.
            # Direction estimation skips frames containing a silent channel.
            for track in aligned[1:]:
                track[begin:stop] = 0
    if not total or valid / total < .7:
        raise ValueError(f"同步補償後仍有過多片段無法驗證（{valid}/{total}），請重新校準。")
    return aligned, dict(packet_samples=packet_samples, valid_frames=valid,
                         total_frames=total, corrections=corrections)


def direction(audio, rate, positions):
    positions = validate_positions(positions, len(audio))
    pairs = list(combinations(range(len(audio)), 2))
    baselines = [positions[i] - positions[j] for i, j in pairs]
    limits = [np.linalg.norm(baseline) / SOUND_SPEED_MPS for baseline in baselines]
    correlations = []
    invalid_delays = np.zeros(len(pairs), dtype=int)
    frame_size = round(.1 * rate)
    windows = 0
    for begin in range(0, min(map(len, audio)) - frame_size + 1, frame_size):
        segments = [track[begin:begin + frame_size] for track in audio]
        if min(np.sqrt(np.mean(x*x)) for x in segments) < 1e-5:
            continue
        for index, ((i, j), limit) in enumerate(zip(pairs, limits)):
            lags, values = correlation(segments[i], segments[j], rate, max(limit, .02))
            physical = abs(lags) <= limit + 1 / rate
            peak = int(np.argmax(values))
            # A dominant peak beyond the maximum acoustic delay indicates
            # inconsistent channel timing or dominant reflected sound.
            invalid_delays[index] += not physical[peak] and values[peak] > 2 * max(np.max(values[physical]), 1e-12)
            if windows:
                correlations[index][1][:] += values
            else:
                correlations.append((lags, values))
        windows += 1
    if not windows:
        return dict(status="no_signal", azimuth_deg=None, elevation_deg=None,
                    absolute_elevation_deg=None)
    if np.count_nonzero(invalid_delays >= max(2, np.ceil(windows / 2))) >= 2:
        return dict(status="error", azimuth_deg=None, elevation_deg=None,
                    absolute_elevation_deg=None, windows=windows,
                    reason="錄音的時間差超出麥克風間距允許的範圍，可能有時間跳變或強反射。請重新錄音。")

    centered = positions - positions.mean(axis=0)
    _, singular_values, axes = np.linalg.svd(centered, full_matrices=False)
    # Use the same numerical rank tolerance as validate_positions.
    planar = singular_values[-1] <= singular_values[0] * max(centered.shape) * np.finfo(float).eps
    normal = axes[-1] if planar else None
    horizontal_plane = planar and np.allclose(normal[:2], 0, rtol=0, atol=1e-12)
    min_elevation = 0 if horizontal_plane else -90
    azimuths = np.arange(-180, 180)
    elevations = np.arange(min_elevation, 91, 2)
    scores = direction_scores(source_vectors(azimuths, elevations), baselines, correlations)
    row, column = np.unravel_index(np.argmax(scores), scores.shape)
    if scores[row, column] <= 0:
        return dict(status="no_signal", azimuth_deg=None, elevation_deg=None,
                    absolute_elevation_deg=None)

    fine_azimuths = azimuths[column] + np.arange(-10, 11) / 10
    fine_elevations = np.clip(elevations[row] + np.arange(-20, 21) / 10, min_elevation, 90)
    vectors = source_vectors(fine_azimuths, fine_elevations)
    refined = direction_scores(vectors, baselines, correlations)
    row, column = np.unravel_index(np.argmax(refined), refined.shape)
    angle = round(float(wrap_degrees(fine_azimuths[column])), 1)
    elevation = float(fine_elevations[row])
    peak = refined[row, column]
    competitor = np.max(scores[:, abs(wrap_degrees(azimuths - angle)) >= 20])
    margin = float((peak - competitor) / peak)

    azimuth_unique = abs(elevation) < 90
    signed_elevation = elevation
    absolute_elevation = abs(elevation)
    if planar:
        # Mirroring a direction about the microphone plane preserves every delay.
        vector = vectors[row, column]
        reflected = vector - 2 * (vector @ normal) * normal
        reflected_angle = np.rad2deg(np.arctan2(-reflected[0], reflected[1]))
        reflected_elevation = np.rad2deg(np.arcsin(np.clip(reflected[2], -1, 1)))
        azimuth_unique &= abs(reflected_elevation) < 90 and abs(wrap_degrees(reflected_angle - angle)) <= .1
        if abs(elevation - reflected_elevation) > .1:
            signed_elevation = None
        if abs(abs(elevation) - abs(reflected_elevation)) > .1:
            absolute_elevation = None
    reliable = azimuth_unique and margin >= MIN_PEAK_MARGIN
    return dict(status="ok" if reliable else "ambiguous",
                azimuth_deg=angle if reliable else None,
                elevation_deg=signed_elevation if reliable else None,
                absolute_elevation_deg=absolute_elevation if reliable else None,
                best_candidate_azimuth_deg=angle, peak_margin=margin, windows=windows)


def validate_positions(positions, count, *, require_xyz=False):
    positions = np.asarray(positions, dtype=float)
    dimensions = (3,) if require_xyz else (2, 3)
    if count not in (3, 4) or positions.ndim != 2 or positions.shape[1] not in dimensions or len(positions) < count:
        raise ValueError("Provide one [x, y, z] coordinate per active microphone")
    positions = positions[:count]
    if not np.isfinite(positions).all() or len(np.unique(positions, axis=0)) != count:
        raise ValueError("Microphone coordinates must be finite and distinct")
    if np.linalg.matrix_rank(positions - positions.mean(axis=0)) < 2:
        raise ValueError("Use non-collinear microphone positions")
    if positions.shape[1] == 2:
        positions = np.pad(positions, ((0, 0), (0, 1)))
    return positions


def robot_base_mm_to_local_m(points):
    # RobotBase X forward, Y left, Z up. The estimator uses X right, Y forward, Z up.
    return points[..., [1, 0, 2]] * [-.001, .001, .001]


def measured_positions(config, count):
    if config.get("geometry_measured") is not True:
        raise ValueError("Measure microphone coordinates and set geometry_measured=true first")
    if "robot_base_positions_mm" in config:
        points = validate_positions(config["robot_base_positions_mm"], count, require_xyz=True)
        return robot_base_mm_to_local_m(points)
    return validate_positions(config.get("positions_m"), count)


def calibration_distances(config, positions, legacy_distances=None):
    robot_base = "robot_base_positions_mm" in config
    source_key = "robot_base_calibration_source_mm" if robot_base else "calibration_source_m"
    if source_key in config:
        source = np.asarray(config[source_key], dtype=float)
        if source.shape != (3,) or not np.isfinite(source).all():
            raise ValueError(f"Fill in {source_key} with the measured [x, y, z] of the calibration speaker")
        if robot_base:
            source = robot_base_mm_to_local_m(source)
        distances = np.linalg.norm(positions - source, axis=1)
    elif legacy_distances is not None:
        distances = np.asarray(legacy_distances[:len(positions)], dtype=float)
    else:
        raise ValueError(f"Provide {source_key} in the array JSON or explicit calibration distances")
    if distances.shape != (len(positions),) or not np.isfinite(distances).all() or np.any(distances <= 0):
        raise ValueError("Calibration requires one finite positive distance per active microphone")
    return distances.tolist()


def robot_frame(config, positions):
    if "robot_base_positions_mm" in config:
        center = positions.mean(axis=0)
        origin = [float(center[1]), float(-center[0]), float(center[2])]
        return dict(array_origin_m=origin, yaw_offset_deg=0)
    frame = config.get("robot_frame")
    if frame is None:
        return None
    if not isinstance(frame, dict):
        raise ValueError("robot_frame must contain array_origin_m and yaw_offset_deg")
    coordinates, yaw = frame.get("array_origin_m"), frame.get("yaw_offset_deg")
    if yaw is None and (coordinates is None or coordinates == [None, None, None]):
        return None
    origin = np.asarray(coordinates, dtype=float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError("Fill in robot_frame.array_origin_m with the measured [x, y, z] of the array origin")
    if isinstance(yaw, bool) or not isinstance(yaw, (float, int)) or not -180 <= yaw <= 180:
        raise ValueError("Fill in robot_frame.yaw_offset_deg between -180 and 180 degrees")
    return dict(array_origin_m=origin.tolist(), yaw_offset_deg=yaw)


def in_robot_frame(result, frame):
    if frame is None:
        return result
    angle = result["azimuth_deg"]
    return dict(result, robot_yaw_deg=None if angle is None else round(wrap_degrees(angle + frame["yaw_offset_deg"]), 2),
                robot_ray_origin_m=frame["array_origin_m"])


def locate(files, config_path, calibration_path, start, end):
    rate, tracks = load_tracks(files)
    config = read_json(config_path)
    positions = measured_positions(config, len(tracks))
    frame = robot_frame(config, positions)
    clock = read_json(calibration_path)
    if len(clock["channels"]) != len(tracks) or clock["sample_rate"] != rate:
        raise ValueError("Calibration microphone count or sample rate mismatch")
    if clock["fingerprints"] != [fingerprint(p) for p in files]:
        raise ValueError("Calibration belongs to another recording. Calibrate this continuous recording first.")
    if start < 0 or end <= start:
        raise ValueError("Invalid analysis interval")
    if start < clock["interval"][0] or end > clock["interval"][1] + config.get("max_extrapolation_seconds", 30):
        raise ValueError("Analysis is too far from calibration. Use a longer calibration or a nearer segment.")
    aligned, sync = align_tracks(tracks, rate, clock, start, end, positions=positions,
                                packet_samples=config.get("clock_jump_samples", 0))
    result = direction(aligned, rate, positions)
    if sync is not None:
        result["synchronization"] = sync
    return in_robot_frame(result, frame)
