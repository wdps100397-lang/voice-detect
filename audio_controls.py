"""Compatibility entry point for the original hardware inspection utility."""
from src.hardware import *

if __name__ == "__main__":
    for device in AudioUtilities.GetAllDevices():
        if "USBAudio2.0" in device.FriendlyName and device.id.startswith("{0.0.1."):
            print(device.FriendlyName, device.EndpointVolume.GetMasterVolumeLevel())
            for control in agcs(device):
                print("AGC", out(control, 3, kind=c.c_int).value)
