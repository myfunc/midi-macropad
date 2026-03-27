"""Windows audio control via pycaw.

Supports endpoint volume (master + microphone), app-session volume by process
name, and endpoint enumeration / selection by device ID.
"""
import comtypes
import time
from ctypes import POINTER, cast, pointer
from comtypes import CLSCTX_ALL, GUID
from pycaw.pycaw import (
    AudioUtilities,
    IAudioEndpointVolume,
)
from logger import get_logger

log = get_logger("audio")


def _get_enumerator():
    try:
        from pycaw.pycaw import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
    except ImportError:
        from pycaw.pycaw import IMMDeviceEnumerator
        CLSID_MMDeviceEnumerator = GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
    return comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator,
        IMMDeviceEnumerator,
        CLSCTX_ALL,
    )


def _device_friendly_name(device) -> str:
    """Best-effort friendly name extraction from IMMDevice via property store."""
    try:
        from pycaw.pycaw import PROPERTYKEY
        store = device.OpenPropertyStore(0)
        key = PROPERTYKEY()
        key.fmtid = GUID('{a45c254e-df1c-4efd-8020-67d146a850e0}')
        key.pid = 14
        value = store.GetValue(pointer(key))

        if isinstance(value, str):
            return value
        if hasattr(value, 'GetValue'):
            r = value.GetValue()
            if isinstance(r, str):
                return r
        for attr in ('pwszVal',):
            v = getattr(value, attr, None) or (
                getattr(getattr(value, 'union', None), attr, None)
            )
            if v:
                return str(v)
        if hasattr(value, 'vt') and value.vt == 0x1F and hasattr(value, 'data'):
            import struct
            ptr = struct.unpack('P', bytes(value.data[0:8]))[0]
            if ptr:
                import ctypes
                return ctypes.wstring_at(ptr)
    except Exception:
        pass
    return ""


def enumerate_output_devices() -> list[tuple[str, str]]:
    """Active output endpoints as [(device_id, friendly_name), ...]."""
    return _enumerate_endpoints(0)


def enumerate_input_devices() -> list[tuple[str, str]]:
    """Active input endpoints as [(device_id, friendly_name), ...]."""
    return _enumerate_endpoints(1)


def _enumerate_endpoints(data_flow: int) -> list[tuple[str, str]]:
    try:
        enumerator = _get_enumerator()
        collection = enumerator.EnumAudioEndpoints(data_flow, 1)  # DEVICE_STATE_ACTIVE
        devices = []
        count = collection.GetCount()
        for i in range(count):
            dev = collection.Item(i)
            dev_id = dev.GetId()
            name = _device_friendly_name(dev) or dev_id
            devices.append((dev_id, name))
        return devices
    except Exception as e:
        log.warning("enumerate_endpoints(flow=%s) failed: %s", data_flow, e)
        return []


def _activate_device(device_id: str | None, data_flow: int):
    """Return (IAudioEndpointVolume | None) for given device or default."""
    try:
        enumerator = _get_enumerator()
        if device_id:
            device = enumerator.GetDevice(device_id)
        else:
            device = enumerator.GetDefaultAudioEndpoint(data_flow, 1)  # eConsole
        iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(iface, POINTER(IAudioEndpointVolume))
    except Exception as exc:
        target = device_id or "default"
        log.warning("Failed to activate %s audio device '%s': %s", data_flow, target, exc)
        return None


def _normalize_process_name(process_name: str) -> str:
    name = (process_name or "").strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _get_app_volume_controls(process_name: str) -> list:
    """Return SimpleAudioVolume controls for matching process sessions."""
    target = _normalize_process_name(process_name)
    if not target:
        return []
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as exc:
        log.warning("GetAllSessions failed for %s: %s", process_name, exc)
        return []

    matches = []
    for session in sessions:
        process = getattr(session, "Process", None)
        if not process:
            continue
        try:
            session_name = _normalize_process_name(process.name())
        except Exception:
            continue
        if session_name != target:
            continue
        volume = getattr(session, "SimpleAudioVolume", None)
        if volume is not None:
            matches.append(volume)
    return matches


class AudioController:
    """Controls master output volume and microphone input volume."""

    def __init__(self, output_device_id: str | None = None,
                 input_device_id: str | None = None,
                 midi_master_cap: float = 1.0,
                 midi_mic_cap: float = 1.0):
        self._master = None
        self._mic = None
        self._app_controls_cache: dict[str, tuple[float, list]] = {}
        self._app_controls_ttl_sec = 1.0
        self._last_app_miss_log: dict[str, float] = {}
        self.output_device_id = output_device_id
        self.input_device_id = input_device_id
        self.midi_master_cap = midi_master_cap
        self.midi_mic_cap = midi_mic_cap
        self._init_master()
        self._init_mic()

    def _get_cached_app_volume_controls(self, process_name: str, refresh: bool = False) -> list:
        target = _normalize_process_name(process_name)
        if not target:
            return []

        now = time.monotonic()
        cached = self._app_controls_cache.get(target)
        if cached and not refresh and (now - cached[0]) < self._app_controls_ttl_sec:
            return cached[1]

        controls = _get_app_volume_controls(target)
        self._app_controls_cache[target] = (now, controls)
        if not controls:
            last_log = self._last_app_miss_log.get(target, 0.0)
            if now - last_log > 5.0:
                log.info("No audio session found for app '%s'", process_name)
                self._last_app_miss_log[target] = now
        return controls

    def _init_master(self):
        self._master = _activate_device(self.output_device_id, data_flow=0)
        if not self._master:
            try:
                device = AudioUtilities.GetSpeakers()
                iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self._master = cast(iface, POINTER(IAudioEndpointVolume))
            except Exception as e:
                log.error("master init failed: %s", e)

    def _init_mic(self):
        self._mic = _activate_device(self.input_device_id, data_flow=1)
        if not self._mic:
            try:
                device = AudioUtilities.GetMicrophone()
                if device:
                    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    self._mic = cast(iface, POINTER(IAudioEndpointVolume))
            except Exception as e:
                log.error("mic init failed: %s", e)

    def set_output_device(self, device_id: str | None):
        self.output_device_id = device_id
        log.info("Switching output device to %s", device_id or "default")
        self._init_master()

    def set_input_device(self, device_id: str | None):
        self.input_device_id = device_id
        log.info("Switching input device to %s", device_id or "default")
        self._init_mic()

    # -- master volume --

    def get_master_volume(self) -> float:
        if self._master:
            try:
                return self._master.GetMasterVolumeLevelScalar()
            except Exception:
                pass
        return 0.0

    def set_master_volume(self, level: float):
        level = max(0.0, min(1.0, level))
        if self._master:
            try:
                self._master.SetMasterVolumeLevelScalar(level, None)
            except Exception:
                pass

    def get_master_mute(self) -> bool:
        if self._master:
            try:
                return bool(self._master.GetMute())
            except Exception:
                pass
        return False

    def set_master_mute(self, mute: bool):
        if self._master:
            try:
                self._master.SetMute(int(mute), None)
            except Exception:
                pass

    # -- mic volume --

    def get_mic_volume(self) -> float:
        if self._mic:
            try:
                return self._mic.GetMasterVolumeLevelScalar()
            except Exception:
                pass
        return 0.0

    def set_mic_volume(self, level: float):
        level = max(0.0, min(1.0, level))
        if self._mic:
            try:
                self._mic.SetMasterVolumeLevelScalar(level, None)
            except Exception:
                pass

    def get_mic_mute(self) -> bool:
        if self._mic:
            try:
                return bool(self._mic.GetMute())
            except Exception:
                pass
        return False

    def set_mic_mute(self, mute: bool):
        if self._mic:
            try:
                self._mic.SetMute(int(mute), None)
            except Exception:
                pass

    # -- app session volume --

    def get_app_volume(self, process_name: str) -> float | None:
        controls = self._get_cached_app_volume_controls(process_name)
        if not controls:
            controls = self._get_cached_app_volume_controls(process_name, refresh=True)
        if not controls:
            return None
        levels = []
        for control in controls:
            try:
                levels.append(control.GetMasterVolume())
            except Exception:
                continue
        if not levels:
            return None
        return sum(levels) / len(levels)

    def set_app_volume(self, process_name: str, level: float) -> bool:
        level = max(0.0, min(1.0, level))
        for attempt in range(2):
            controls = self._get_cached_app_volume_controls(
                process_name,
                refresh=attempt > 0,
            )
            if not controls:
                return False
            changed = False
            for control in controls:
                try:
                    control.SetMasterVolume(level, None)
                    changed = True
                except Exception:
                    continue
            if changed:
                return True
        return False

    # -- helpers --

    def midi_to_master_volume(self, midi_value: int) -> float:
        return (midi_value / 127.0) * self.midi_master_cap

    def midi_to_mic_volume(self, midi_value: int) -> float:
        return (midi_value / 127.0) * self.midi_mic_cap
