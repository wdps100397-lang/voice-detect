"""Use known playback waveform to measure timing slips without cross-mic assumptions."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.signal import butter,fftconvolve,resample_poly,sosfiltfilt
from src.common import ROOT,load_audio,read_json,save_json
from src.localization import correlation,fit_clock,align_tracks,direction,measured_positions,calibration_distances


def analyze(folder):
    folder=Path(folder)
    packets=read_json(folder/'packets.json')
    tracks=[load_audio(folder/f'{r["label"]}.wav')[1] for r in packets]
    rate=48000
    duration=min(map(len,tracks))/rate
    _,reference=load_audio(ROOT/'assets/test_audio/broadband_30s.wav',rate)
    match_rate=16000
    sos=butter(4,[800,6500],btype='bandpass',fs=match_rate,output='sos')
    ref=sosfiltfilt(sos,resample_poly(reference,1,3))
    size=round(.1*match_rate)
    cumulative=np.r_[0,np.cumsum(ref*ref)]
    energy=np.maximum(cumulative[size:]-cumulative[:-size],0)
    active=energy>energy.max()*.05
    channels=[]
    for row,audio in zip(packets,tracks):
        matched=sosfiltfilt(sos,resample_poly(audio,1,3))
        windows=[]
        for start in np.arange(.5,duration-.5,.5):
            t=float(start+.2)
            chunk=matched[round(t*match_rate):round(t*match_rate)+size]
            values=fftconvolve(ref,chunk[::-1],mode='valid')
            denominator=np.sqrt(energy*max(float(chunk@chunk),1e-20))
            scores=np.zeros_like(values)
            scores[active]=abs(values[active])/denominator[active]
            peak=int(np.argmax(scores))
            windows.append(dict(time=t,source_time=peak/match_rate,score=float(scores[peak]),known_noise=bool(scores[peak]>=.18)))
        usable=[w for w in windows if w['known_noise']]
        consecutive=[(a,b) for a,b in zip(usable,usable[1:]) if b['time']-a['time']<=1.1
                     and abs((b['source_time']-a['source_time'])-(b['time']-a['time']))<.1]
        slopes=[(b['source_time']-a['source_time'])/(b['time']-a['time']) for a,b in consecutive]
        slope=float(np.median(slopes)) if slopes else None
        jumps=[]
        if slope is not None:
            for a,b in consecutive:
                delta=b['source_time']-a['source_time']-slope*(b['time']-a['time'])
                if abs(delta)>.0025:
                    jumps.append(dict(interval=[a['time'],b['time']],seconds=float(delta),samples_48k=float(delta*48000)))
        result=dict(label=row['label'],index=row['index'],mode=row.get('mode','exclusive'),
                    matched_windows=len(usable),total_windows=len(windows),
                    relative_playback_clock_ppm=None if slope is None else (slope-1)*1e6,
                    native_position_gaps=len(row['gaps']),native_nonstartup_flags=sum(bool(p['flags']) for p in row['packets'][1:]),
                    detected_acoustic_phase_slips=jumps,windows=windows)
        channels.append(result)
        print(result['label'],result['mode'],'matched',len(usable),'/',len(windows),'phase_slips',len(jumps),jumps[:6],flush=True)
    report=dict(folder=str(folder),duration_seconds=duration,channels=channels)
    if len(tracks)==4:
        geometry=read_json(ROOT/'config/array.json')
        positions=measured_positions(geometry,4)
        distances=calibration_distances(geometry,positions)
        try:
            clock=fit_clock(tracks,rate,9,15,distances)
            checks=[]
            for t in np.arange(16,min(duration-.6,40),2):
                begin,end=round((t+.2)*rate),round((t+.3)*rate)
                residual=[]
                for i,(audio,c) in enumerate(zip(tracks[1:],clock['channels'][1:]),1):
                    lags,values=correlation(audio[begin:end],tracks[0][begin:end],rate,.2)
                    measured=lags[np.argmax(values)]
                    residual.append(float((measured-(c['offset_seconds']+c['drift']*(t+.25))-(distances[i]-distances[0])/343)*rate))
                aligned,_=align_tracks(tracks,rate,clock,float(t),float(t+.5))
                result=direction(aligned,rate,positions)
                checks.append(dict(time=float(t),residual_samples=residual,**result))
                print(' holdout',t,np.round(residual,2),result['status'],result.get('azimuth_deg'),flush=True)
            report.update(clock=clock,holdouts=checks)
        except ValueError as exc:
            report['calibration_error']=str(exc)
            print('Calibration failed',exc,flush=True)
    save_json(folder/'communication_analysis.json',report)
    return report


if __name__=='__main__':
    for folder in sys.argv[1:]:
        analyze(folder)
