"""Sequential, source-stationary comparison; requires explicit playback readiness."""
from native_capture_probe import record
from communication_analysis import analyze
from src.common import save_json

folders=[]
for name,indices,mode,buffer_ms in [
    ('single A exclusive',[36],'exclusive',100),
    ('four shared RAW',[36,35,33,30],'shared_raw',100),
    ('four exclusive larger buffer',[36,35,33,30],'exclusive',250),
]:
    print('Starting',name,flush=True)
    folders.append(record(40,buffer_ms,indices,mode,True))
    print('Finished',name,flush=True)
print('All recordings complete. Playback may stop.',flush=True)
reports=[analyze(folder) for folder in folders]
save_json(folders[0]/'comparison.json',dict(folders=[str(p) for p in folders],reports=reports))
