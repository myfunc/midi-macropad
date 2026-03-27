"""Detect the foreground application on Windows using Win32 API."""
import ctypes
import ctypes.wintypes
import os

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


def get_foreground_process() -> str | None:
    """Return the executable name of the foreground window's process, e.g. 'Cursor.exe'."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    
    if pid.value == 0:
        return None
    
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value)
    if not handle:
        return None
    
    try:
        buf = (ctypes.c_wchar * 260)()
        psapi.GetModuleFileNameExW(handle, None, buf, 260)
        full_path = buf.value
        if full_path:
            return os.path.basename(full_path)
    finally:
        kernel32.CloseHandle(handle)
    
    return None
