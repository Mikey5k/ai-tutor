#!/usr/bin/env python3
"""
Configures Windows audio for voice Q&A:
- Finds Realtek microphone in MMDevice registry
- Enables the SysFx effects chain (AEC lives here)
- Disables SysFx bypass so Noise Suppression + AEC are active
- Reports WASAPI device indices for voice_qa.py
Run once, no UI needed, no admin required for read — write needs admin for registry.
"""
import winreg
import sys
import sounddevice as sd

CAPTURE_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
FRIENDLY_NAME_KEY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
SYSFX_KEY        = "{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5"

_REG_FLAGS = winreg.KEY_READ | winreg.KEY_WOW64_64KEY

def find_mic_guids():
    """Return list of (guid, friendly_name) for all capture devices."""
    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CAPTURE_PATH, access=_REG_FLAGS) as root:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(root, i)
                    prop_path = f"{CAPTURE_PATH}\\{guid}\\Properties"
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, prop_path, access=_REG_FLAGS) as p:
                            name, _ = winreg.QueryValueEx(p, FRIENDLY_NAME_KEY)
                            found.append((guid, name))
                    except OSError:
                        pass
                    i += 1
                except OSError:
                    break
    except OSError as e:
        print(f"Cannot read MMDevices registry: {e}")
    return found

def enable_sysfx(guid: str) -> str:
    """Enable SysFx effects chain for a capture device. Returns status string."""
    fx_path = f"{CAPTURE_PATH}\\{guid}\\FxProperties"
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, fx_path,
            access=winreg.KEY_READ | winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
        ) as fx:
            try:
                current, _ = winreg.QueryValueEx(fx, SYSFX_KEY)
            except OSError:
                current = -1
            if current == 0:
                return "already enabled"
            winreg.SetValueEx(fx, SYSFX_KEY, 0, winreg.REG_DWORD, 0)
            return "enabled now"
    except PermissionError:
        return "needs admin — run: python setup_audio.py  (as Administrator)"
    except OSError:
        return "FxProperties not found (driver may not expose it)"

def report_wasapi():
    """Print WASAPI device indices for voice_qa.py config."""
    try:
        apis     = sd.query_hostapis()
        devices  = sd.query_devices()
        wasapi_i = next((i for i, a in enumerate(apis) if "WASAPI" in a["name"]), None)
        if wasapi_i is None:
            print("  WASAPI host: not found")
            return
        print(f"  WASAPI host: {apis[wasapi_i]['name']}")
        for i, d in enumerate(devices):
            if d["hostapi"] == wasapi_i:
                direction = f"in:{d['max_input_channels']}" if d["max_input_channels"] > 0 \
                            else f"out:{d['max_output_channels']}"
                marker = " <-- voice_qa will use this" if d["max_input_channels"] > 0 \
                         and all(
                             d2["max_input_channels"] == 0 or d2["hostapi"] != wasapi_i
                             for d2 in list(devices)[:i]
                         ) else ""
                print(f"  [{i:2d}] {d['name']}  ({direction}){marker}")
    except Exception as e:
        print(f"  sounddevice error: {e}")

def main():
    print()
    print("AI Tutor — Audio Setup")
    print("=" * 40)

    # ── 1. Find all capture devices ──────────────────────────────────────────
    print("\nCapture devices found:")
    devices = find_mic_guids()
    if not devices:
        print("  None found in registry.")
        sys.exit(1)
    for guid, name in devices:
        print(f"  {name}")
        print(f"    GUID: {guid}")

    # ── 2. Enable SysFx for microphone devices ───────────────────────────────
    print("\nEnabling audio effects chain (AEC/Noise Suppression):")
    for guid, name in devices:
        if "Microphone" in name or "Mic" in name or "mic" in name.lower():
            result = enable_sysfx(guid)
            print(f"  {name}: {result}")

    # ── 3. Show WASAPI devices ───────────────────────────────────────────────
    print("\nWASAPI devices (used by voice_qa.py):")
    report_wasapi()

    # ── 4. Mic level check ───────────────────────────────────────────────────
    print("\nRecommendations:")
    print("  1. Speaker volume: keep at 50-70% to reduce echo pickup")
    print("  2. Mic distance: 20-40cm from mouth is ideal")
    print("  3. Best setup: headphones (eliminates echo entirely)")
    print("  4. If echo still fires: increase INTERRUPT_THRESHOLD in voice_qa.py")
    print("     Current value: 0.78  Try: 0.82 or 0.85 if needed")
    print()

if __name__ == "__main__":
    main()
