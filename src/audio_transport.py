"""Selectable capture transport, including Windows shared RAW with packet timing."""
import ctypes as c
from pathlib import Path
import threading
import time

import sounddevice as sd

CAPTURE_MODES = ("exclusive", "shared", "shared_raw")


def input_stream(device, rate, callback, *, mode="exclusive", latency=None):
    if mode not in CAPTURE_MODES:
        raise ValueError(f"capture_mode must be one of {CAPTURE_MODES}")
    if latency is not None and (isinstance(latency, bool) or not .02 <= latency <= .5):
        raise ValueError("capture_latency_seconds must be between 0.02 and 0.5")
    info = sd.query_devices(device, "input")
    if mode != "shared" and sd.query_hostapis(info["hostapi"])["name"] != "Windows WASAPI":
        raise ValueError("Exclusive and shared RAW capture require Windows WASAPI inputs")
    if mode == "shared_raw":
        return NativeRawInput(device, rate, callback, latency or .1)
    options = dict(device=device, channels=1, samplerate=rate, dtype="int16",
                   blocksize=0, callback=callback,
                   extra_settings=sd.WasapiSettings(exclusive=True) if mode == "exclusive" else None)
    if latency is not None:
        options["latency"] = latency
    return sd.RawInputStream(**options)


def endpoint_id(index):
    # Resolve by endpoint ID rather than a duplicate friendly name. PortAudio's
    # IMMDevice remains owned by PortAudio; GetId allocates a separate string.
    from src.hardware import out, P
    dll = c.CDLL(str(Path(sd.__file__).parent / "_sounddevice_data/portaudio-binaries/libportaudio64bit.dll"))
    resolve = dll.PaWasapi_GetIMMDevice
    resolve.argtypes = [c.c_int, c.POINTER(P)]
    resolve.restype = c.c_int
    pointer = P()
    error = resolve(index, c.byref(pointer))
    if error < 0:
        raise OSError(f"Cannot resolve WASAPI input {index}: PortAudio {error}")
    value = out(pointer, 5)
    try:
        return c.wstring_at(value)
    finally:
        free = c.windll.ole32.CoTaskMemFree
        free.argtypes = [P]
        free(value)


def packet_status(previous, position, frames, flags):
    """Ignore only the normal first-packet discontinuity, retaining every flag."""
    errors = []
    if flags & 4:
        errors.append("WASAPI timestamp uncertain")
    if previous is not None:
        if flags & 1:
            errors.append("WASAPI data discontinuity")
        expected = previous[0] + previous[1]
        if position != expected:
            errors.append(f"WASAPI device-position gap: {position - expected} samples")
    return "; ".join(errors)


class NativeRawInput:
    """Dedicated MMCSS capture thread; COM objects never cross apartments.

    RAW bypasses optional Windows effects. Driver/hardware always-on processing
    may remain. Device timestamps are diagnostics, not proof of ADC synchrony.
    """
    def __init__(self, device, rate, callback, latency):
        self.endpoint = endpoint_id(device)
        self.rate, self.callback, self.latency = rate, callback, latency
        self.error = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._active = threading.Event()
        self._thread = None

    @property
    def active(self):
        return self._active.is_set()

    def start(self):
        if self._thread is not None:
            raise RuntimeError("A closed native stream must be reopened and recalibrated")
        self._thread = threading.Thread(target=self._run, name="WASAPI RAW capture", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            self.close()
            raise RuntimeError("Timed out opening WASAPI RAW capture")
        if self.error is not None:
            self.close()
            raise RuntimeError(f"WASAPI RAW could not start: {self.error}") from self.error
        return self

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            if self._thread.is_alive():
                raise RuntimeError("WASAPI RAW capture thread did not stop")

    def _run(self):
        import comtypes
        from comtypes import GUID, CLSCTX_ALL
        from contextlib import ExitStack
        from pycaw.pycaw import AudioUtilities
        from src.hardware import call, out, P

        class Format(c.Structure):
            _pack_ = 2
            _fields_ = [("tag", c.c_ushort), ("channels", c.c_ushort),
                        ("rate", c.c_uint), ("average", c.c_uint),
                        ("align", c.c_ushort), ("bits", c.c_ushort), ("extra", c.c_ushort)]

        class Properties(c.Structure):
            _fields_ = [("size", c.c_ulong), ("offload", c.c_int),
                        ("category", c.c_int), ("options", c.c_int)]

        device = client = None
        initialized = False
        try:
            comtypes.CoInitialize()
            initialized = True
            with ExitStack() as cleanup:
                device = next(d for d in AudioUtilities.GetAllDevices(data_flow=1, device_state=1)
                              if d.id == self.endpoint)
                client = device._dev.Activate(GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}"), CLSCTX_ALL, None)
                iid = GUID("{726778CD-F60A-4EDA-82DE-E47610CD78AA}")
                client2 = out(client, 0, [c.POINTER(GUID)], (c.byref(iid),))
                cleanup.callback(call, client2, 2, [])
                properties = Properties(c.sizeof(Properties), 0, 0, 1)
                call(client2, 16, [c.POINTER(Properties)], c.byref(properties))
                fmt = Format(1, 1, self.rate, self.rate * 2, 2, 16, 0)
                call(client, 3, [c.c_int, c.c_ulong, c.c_longlong, c.c_longlong, c.POINTER(Format), P],
                     0, 0, round(self.latency * 1e7), 0, c.byref(fmt), None)
                iid = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
                capture = out(client, 14, [c.POINTER(GUID)], (c.byref(iid),))
                cleanup.callback(call, capture, 2, [])
                avrt = c.WinDLL("avrt", use_last_error=True)
                avrt.AvSetMmThreadCharacteristicsW.argtypes = [c.c_wchar_p, c.POINTER(c.c_ulong)]
                avrt.AvSetMmThreadCharacteristicsW.restype = P
                avrt.AvRevertMmThreadCharacteristics.argtypes = [P]
                avrt.AvRevertMmThreadCharacteristics.restype = c.c_int
                task_index = c.c_ulong()
                task = avrt.AvSetMmThreadCharacteristicsW("Pro Audio", c.byref(task_index))
                if not task:
                    raise c.WinError(c.get_last_error())
                cleanup.callback(avrt.AvRevertMmThreadCharacteristics, task)
                call(client, 10, [])
                cleanup.callback(call, client, 11, [])
                self._active.set()
                self._ready.set()
                previous = None
                while not self._stop.is_set():
                    if not out(capture, 5, kind=c.c_uint).value:
                        self._stop.wait(.001)
                        continue
                    data, frames, flags = P(), c.c_uint(), c.c_ulong()
                    position, timestamp = c.c_ulonglong(), c.c_ulonglong()
                    call(capture, 3, [c.POINTER(P), c.POINTER(c.c_uint), c.POINTER(c.c_ulong),
                                     c.POINTER(c.c_ulonglong), c.POINTER(c.c_ulonglong)],
                         c.byref(data), c.byref(frames), c.byref(flags), c.byref(position), c.byref(timestamp))
                    if not frames.value:
                        continue
                    try:
                        block = bytes(frames.value * 2) if flags.value & 2 else c.string_at(data, frames.value * 2)
                        timing = dict(inputBufferAdcTime=timestamp.value / 1e7,
                                      device_position=position.value, qpc_100ns=timestamp.value,
                                      native_flags=flags.value)
                        status = packet_status(previous, position.value, frames.value, flags.value)
                        previous = (position.value, frames.value)
                    finally:
                        call(capture, 4, [c.c_uint], frames.value)
                    # Release the native buffer before application processing.
                    self.callback(block, frames.value, timing, status)
        except BaseException as error:
            self.error = error
        finally:
            self._active.clear()
            self._ready.set()
            device = client = None
            if initialized:
                comtypes.CoUninitialize()


def timing_row(timing, sample, frames):
    row = dict(sample=sample, frames=frames)
    for key in ("inputBufferAdcTime", "currentTime", "device_position", "qpc_100ns", "native_flags"):
        value = timing.get(key) if isinstance(timing, dict) else getattr(timing, key, None)
        if value is not None:
            row[key] = value
    return row
