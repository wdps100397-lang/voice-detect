"""Compare native packet timing with acoustic timing in a fixed-source recording."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.signal import butter, fftconvolve, resample_poly, sosfiltfilt
from src.common import ROOT, load_audio, load_tracks, read_json, save_json
from src.localization import correlation, fit_clock, align_tracks, direction, measured_positions, calibration_distances

folder = Path(sys.argv[1])
rate, tracks = load_tracks([folder / f'{c}.wav' for c in 'ABCD'])
geometry = read_json(ROOT / 'config/array.json')
positions = measured_positions(geometry, 4)
distances = calibration_distances(geometry, positions)
duration = min(map(len, tracks)) / rate
_, reference = load_audio(ROOT / 'assets/test_audio/broadband_30s.wav', rate)
match_rate = 16000
sos = butter(4, [800, 6500], btype='bandpass', fs=match_rate, output='sos')
ref = sosfiltfilt(sos, resample_poly(reference, 1, 3))
matched_tracks = [sosfiltfilt(sos, resample_poly(a, 1, 3)) for a in tracks]
size = round(.1 * match_rate)
energy = np.r_[0, np.cumsum(ref ** 2)]
ref_energy = np.maximum(energy[size:] - energy[:-size], 0)
valid = ref_energy > .05 * ref_energy.max()
windows = []
for start in np.arange(.5, duration-.5, .5):
    scores, source_times = [], []
    for audio in matched_tracks:
        begin = round((start+.2)*match_rate)
        chunk = audio[begin:begin+size]
        values = fftconvolve(ref, chunk[::-1], mode='valid')
        norm = np.sqrt(ref_energy * max(float(chunk@chunk),1e-20))
        scores_all = np.zeros_like(values)
        scores_all[valid] = abs(values[valid])/norm[valid]
        peak = np.argmax(scores_all)
        scores.append(float(scores_all[peak]))
        source_times.append(float(peak/match_rate))
    good = np.asarray(scores)>=.18
    detected = bool(good.sum()>=3 and np.ptp(np.asarray(source_times)[good])<=.15)
    delays = []
    for audio in tracks[1:]:
        begin, end = round((start+.2)*rate),round((start+.3)*rate)
        lags, values = correlation(audio[begin:end], tracks[0][begin:end], rate, .2)
        delays.append(float(lags[np.argmax(values)]*rate))
    windows.append(dict(time=float(start+.25), known_noise=detected,
                        matching_scores=scores, source_times=source_times, raw_delays_samples=delays))
print('Known noise',sum(w['known_noise'] for w in windows),'/',len(windows),flush=True)

calibrations = []
for start in range(1,int(duration)-7,2):
    end=start+6
    selected=[w for w in windows if start<=w['time']<=end]
    if sum(w['known_noise'] for w in selected)<.85*len(selected):
        continue
    try:
        clock=fit_clock(tracks,rate,start,end,distances)
    except ValueError as exc:
        calibrations.append(dict(interval=[start,end],status='failed',reason=str(exc)))
        continue
    checks=[]
    for row in windows:
        if not row['known_noise'] or row['time']<end+.5:
            continue
        time=row['time']
        residual=[float(delay-(channel['offset_seconds']+channel['drift']*time)*rate-
                        (distances[i+1]-distances[0])/343*rate)
                  for i,(delay,channel) in enumerate(zip(row['raw_delays_samples'],clock['channels'][1:]))]
        try:
            audio,_=align_tracks(tracks,rate,clock,time-.2,time+.2)
            result=direction(audio,rate,positions)
        except ValueError as exc:
            result=dict(status='error',reason=str(exc))
        checks.append(dict(time=time,residual_samples=residual,**result))
    calibrations.append(dict(interval=[start,end],status='ok',clock=clock,holdouts=checks))
    print('Calibration',start,end,'ppm',[round(c['drift']*1e6,1) for c in clock['channels']],
          'residual_us',[round(c.get('residual_p95_seconds',0)*1e6,1) for c in clock['channels']],flush=True)
    for c in checks[::6]:
        print(' holdout',c['time'],c['status'],c.get('azimuth_deg'),np.round(c['residual_samples'],1),flush=True)
report=dict(note='Same fixed calibration source throughout. Holdouts test temporal stability, not independent angular accuracy.',
            expected_azimuth_deg=float(np.degrees(np.arctan2(-geometry['calibration_source_m'][0],geometry['calibration_source_m'][1]))),
            windows=windows,calibrations=calibrations)
save_json(folder/'acoustic_timing_analysis.json',report)
for row in windows[::4]:
    print('raw',row['time'],row['known_noise'],np.round(row['raw_delays_samples'],1),flush=True)
