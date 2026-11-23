"""Windows DeviceTopology access for USB capture controls."""
import ctypes as c
from comtypes import GUID, CLSCTX_ALL
from pycaw.pycaw import AudioUtilities

P = c.c_void_p
TOP = "{2A07407E-6497-4A18-9787-32F79BD0D98F}"
PART = "{AE2DE0E4-5BCA-4F2D-AA46-5D13F8FDB3A9}"
AGC = "{85401FD4-6DE4-4B9D-9869-2D6753A82F3C}"


def call(obj, index, types, *args):
    """Call a COM vtable slot from the Windows SDK devicetopology.h layout."""
    pointer = c.cast(obj, P)
    table = c.cast(pointer, c.POINTER(c.POINTER(P))).contents
    function = c.WINFUNCTYPE(c.c_long, P, *types)(table[index])
    result = function(pointer, *args)
    if result < 0:
        raise OSError(f"Windows audio control failed: HRESULT {result & 0xffffffff:#x}")


def out(obj, index, types=(), args=(), kind=P):
    value = kind()
    call(obj, index, [*types, c.POINTER(kind)], *args, c.byref(value))
    return value


def qi(obj, guid):
    interface_id = GUID(guid)
    return out(obj, 0, [c.POINTER(GUID)], (c.byref(interface_id),))


def agcs(device):
    from contextlib import ExitStack
    controls = []
    # Release temporary counted references, retaining returned AGC controls.
    with ExitStack() as references:
        def retain(pointer):
            references.callback(call, pointer, 2, [])
            return pointer
        topology = device._dev.Activate(GUID(TOP), CLSCTX_ALL, None)
        connector = retain(out(topology, 4, [c.c_uint], (0,)))
        connected = retain(out(connector, 8))
        part = retain(qi(connected, PART))
        adapter = retain(out(part, 12))
        count = out(adapter, 5, kind=c.c_uint).value
        for index in range(count):
            node = retain(out(adapter, 6, [c.c_uint], (index,)))
            part = retain(qi(node, PART))
            subtype = out(part, 7, kind=GUID)
            if str(subtype).upper() == "{E88C9BA0-C557-11D0-8A2B-00A0C9255AC1}":
                interface_id = GUID(AGC)
                control = out(part, 13, [c.c_ulong, c.POINTER(GUID)],
                              (CLSCTX_ALL, c.byref(interface_id)))
                controls.append(control)
    return controls


def configure():
    from datetime import datetime
    from src.common import ROOT, save_json
    from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY, PROPVARIANT
    devices = [d for d in AudioUtilities.GetAllDevices(data_flow=1, device_state=1)
               if 'USBAudio2.0' in d.FriendlyName and d.id.startswith('{0.0.1.') and d.state.value == 1]
    if len(devices) not in (3, 4):
        raise ValueError(f"Expected three or four active USBAudio2.0 inputs, found {len(devices)}")
    key = PROPERTYKEY(GUID('{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E}'), 5)
    folder = ROOT / 'runs' / (datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_settings')
    prepared, before = [], []
    for device in devices:
        controls = agcs(device)
        if len(controls) != 1:
            raise ValueError(f"Expected one AGC control on {device.FriendlyName}")
        store = device._dev.OpenPropertyStore(2)
        old = store.GetValue(key)
        before.append(dict(id=device.id, name=device.FriendlyName,
                           agc=out(controls[0],3,kind=c.c_int).value,
                           volume_db=device.EndpointVolume.GetMasterVolumeLevel(),
                           mute=device.EndpointVolume.GetMute(),
                           sysfx_vt=old.vt, sysfx_value=old.GetValue()))
        prepared.append((device, controls[0], store))
    save_json(folder / 'before.json', before)
    results = []
    try:
        for device, control, store in prepared:
            call(control, 4, [c.c_int,P], 0, None)
            device.EndpointVolume.SetMasterVolumeLevel(before[0]['volume_db'], None)
            device.EndpointVolume.SetMute(0, None)
            value = PROPVARIANT()
            value.vt = 19
            value.union.lVal = 1
            store.SetValue(key, value)
            store.Commit()
            readback = device._dev.OpenPropertyStore(0).GetValue(key)
            result = dict(id=device.id, name=device.FriendlyName,
                          agc=out(control,3,kind=c.c_int).value,
                          volume_db=device.EndpointVolume.GetMasterVolumeLevel(),
                          mute=device.EndpointVolume.GetMute(),
                          sysfx_disabled=readback.vt == 19 and readback.GetValue() == 1)
            results.append(result)
            if result['agc'] != 0 or result['mute'] != 0 or not result['sysfx_disabled']:
                raise ValueError(f"Setting verification failed on {device.FriendlyName}")
            if abs(result['volume_db'] - before[0]['volume_db']) > .01:
                raise ValueError("Gain readback mismatch")
    finally:
        save_json(folder / 'after.json', results)
    return folder


if __name__=='__main__':
    for d in AudioUtilities.GetAllDevices():
        if 'USBAudio2.0' in d.FriendlyName and d.id.startswith('{0.0.1.'):
            print(d.FriendlyName,d.id)
            v=d.EndpointVolume
            print('volume db',v.GetMasterVolumeLevel(),'range',v.GetVolumeRange(),'mute',v.GetMute())
            for ctrl in agcs(d):
                print('AGC',out(ctrl,3,kind=c.c_int).value)
