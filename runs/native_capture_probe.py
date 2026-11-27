"""Diagnostic only: retain native WASAPI packet positions, timestamps and flags."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ctypes as c
import json
import threading
import time
import wave
from datetime import datetime
from contextlib import ExitStack
import comtypes
from comtypes import GUID, CLSCTX_ALL
from pycaw.pycaw import AudioUtilities
import sounddevice as sd
from src.hardware import call, out, P


class Format(c.Structure):
    _pack_ = 2
    _fields_ = [('tag', c.c_ushort), ('channels', c.c_ushort),
                ('rate', c.c_uint), ('average', c.c_uint),
                ('align', c.c_ushort), ('bits', c.c_ushort), ('extra', c.c_ushort)]


def endpoint_ids(indices):
    dll = c.CDLL(str(Path(sd.__file__).parent / '_sounddevice_data/portaudio-binaries/libportaudio64bit.dll'))
    dll.PaWasapi_GetIMMDevice.argtypes = [c.c_int, c.POINTER(P)]
    dll.PaWasapi_GetIMMDevice.restype = c.c_int
    ids = []
    for index in indices:
        pointer = P()
        result = dll.PaWasapi_GetIMMDevice(index, c.byref(pointer))
        if result < 0:
            raise RuntimeError(f'Cannot resolve WASAPI input {index}: {result}')
        # PortAudio owns this IMMDevice. Only free the GetId allocation.
        value = out(pointer, 5)
        ids.append(c.wstring_at(value))
        c.windll.ole32.CoTaskMemFree(value)
    return ids


def record(seconds=25, buffer_ms=100, indices=None, mode='exclusive', priority=False):
    indices = [36, 35, 33, 30] if indices is None else indices
    ids = endpoint_ids(indices)
    folder = Path('runs') / (datetime.now().strftime('%Y%m%d_%H%M%S') + f'_native_packet_check_{mode}_{len(indices)}ch')
    folder.mkdir()
    stop = threading.Event()
    barrier = threading.Barrier(len(indices)+1, timeout=10)
    results = [None] * len(indices)

    def worker(channel, endpoint):
        comtypes.CoInitialize()
        packets, audio, error = [], bytearray(), None
        try:
            with ExitStack() as references:
                device = next(d for d in AudioUtilities.GetAllDevices(data_flow=1, device_state=1)
                              if d.id == endpoint)
                client = device._dev.Activate(GUID('{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}'), CLSCTX_ALL, None)
                default, minimum = c.c_longlong(), c.c_longlong()
                call(client, 9, [c.POINTER(c.c_longlong), c.POINTER(c.c_longlong)],
                     c.byref(default), c.byref(minimum))
                fmt = Format(1, 1, 48000, 96000, 2, 16, 0)
                if mode == 'shared_raw':
                    class Properties(c.Structure):
                        _fields_ = [('size',c.c_ulong),('offload',c.c_int),('category',c.c_int),('options',c.c_int)]
                    iid2 = GUID('{726778CD-F60A-4EDA-82DE-E47610CD78AA}')
                    client2 = out(client,0,[c.POINTER(GUID)],(c.byref(iid2),))
                    references.callback(call,client2,2,[])
                    properties = Properties(c.sizeof(Properties),0,0,1)
                    call(client2,16,[c.POINTER(Properties)],c.byref(properties))
                if priority:
                    avrt = c.WinDLL('avrt')
                    avrt.AvSetMmThreadCharacteristicsW.argtypes = [c.c_wchar_p,c.POINTER(c.c_ulong)]
                    avrt.AvSetMmThreadCharacteristicsW.restype = P
                    avrt.AvRevertMmThreadCharacteristics.argtypes = [P]
                    avrt.AvRevertMmThreadCharacteristics.restype = c.c_int
                    task_index = c.c_ulong()
                    task = avrt.AvSetMmThreadCharacteristicsW('Pro Audio',c.byref(task_index))
                    if not task:
                        raise c.WinError()
                    references.callback(avrt.AvRevertMmThreadCharacteristics,task)
                call(client, 3, [c.c_int, c.c_ulong, c.c_longlong, c.c_longlong, c.POINTER(Format), P],
                     0 if mode == 'shared_raw' else 1, 0, buffer_ms * 10000,
                     0 if mode == 'shared_raw' else default.value, c.byref(fmt), None)
                size = out(client, 4, kind=c.c_uint).value
                iid = GUID('{C8ADBD64-E71E-48A0-A4DE-185C395CD317}')
                capture = out(client, 14, [c.POINTER(GUID)], (c.byref(iid),))
                references.callback(call, capture, 2, [])
                barrier.wait()
                call(client, 10, [])
                references.callback(call, client, 11, [])
                while not stop.is_set():
                    count = out(capture, 5, kind=c.c_uint).value
                    if not count:
                        stop.wait(.001)
                        continue
                    data, frames, flags = P(), c.c_uint(), c.c_ulong()
                    position, timestamp = c.c_ulonglong(), c.c_ulonglong()
                    call(capture, 3, [c.POINTER(P), c.POINTER(c.c_uint), c.POINTER(c.c_ulong),
                                     c.POINTER(c.c_ulonglong), c.POINTER(c.c_ulonglong)],
                         c.byref(data), c.byref(frames), c.byref(flags), c.byref(position), c.byref(timestamp))
                    if not frames.value:
                        continue
                    try:
                        packets.append(dict(sample=len(audio)//2, frames=frames.value, flags=flags.value,
                                            position=position.value, qpc_100ns=timestamp.value))
                        audio.extend(bytes(frames.value * 2) if flags.value & 2 else c.string_at(data, frames.value * 2))
                    finally:
                        call(capture, 4, [c.c_uint], frames.value)
                info = dict(mode=mode, priority=priority, buffer_frames=size,
                            default_period_100ns=default.value, minimum_period_100ns=minimum.value)
        except BaseException as exc:
            error = repr(exc)
            barrier.abort()
            stop.set()
            info = {}
        finally:
            # Release comtypes-managed objects before uninitializing their apartment.
            capture = client = device = None
            comtypes.CoUninitialize()
        with wave.open(str(folder / f'{"ABCD"[channel]}.wav'), 'wb') as wav:
            wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            wav.writeframes(audio)
        gaps = [dict(packet=i, sample=b['sample'], delta=b['position']-a['position']-a['frames'])
                for i, (a,b) in enumerate(zip(packets,packets[1:]),1)
                if b['position'] != a['position'] + a['frames']]
        results[channel] = dict(label='ABCD'[channel], index=indices[channel], endpoint=endpoint,
                                error=error, **info, frames=len(audio)//2, packets=packets, gaps=gaps)

    threads = [threading.Thread(target=worker,args=(i,endpoint)) for i,endpoint in enumerate(ids)]
    for thread in threads:
        thread.start()
    try:
        barrier.wait()
        print('Capturing native packets', folder, flush=True)
        stop.wait(seconds)
    except threading.BrokenBarrierError:
        pass
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
    (folder / 'packets.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    for result in results:
        print({key:value for key,value in result.items() if key not in ('packets','endpoint')},flush=True)
        if result['packets']:
            print('flagged',sum(bool(p['flags']) for p in result['packets']),
                  'first',result['packets'][0], 'last',result['packets'][-1],flush=True)
    return folder


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('seconds',type=float,nargs='?',default=25)
    parser.add_argument('buffer_ms',type=int,nargs='?',default=100)
    parser.add_argument('--devices',type=int,nargs='+',default=[36,35,33,30])
    parser.add_argument('--mode',choices=['exclusive','shared_raw'],default='exclusive')
    parser.add_argument('--priority',action='store_true')
    args=parser.parse_args()
    record(args.seconds,args.buffer_ms,args.devices,args.mode,args.priority)
