"""Helper to capture a specific Chrome window by title and save to PNG.

Usage:  python _capture.py <output.png> [title_substring]
"""
import sys
from ctypes import windll
import win32gui, win32ui
from PIL import Image


def find_window(needle: str):
    results = []
    def enum(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if needle.lower() in title.lower():
                rect = win32gui.GetWindowRect(hwnd)
                area = (rect[2] - rect[0]) * (rect[3] - rect[1])
                results.append((area, hwnd, title))
    win32gui.EnumWindows(enum, None)
    results.sort(reverse=True)
    return results[0] if results else (0, None, None)


def capture(hwnd: int, path: str):
    rect = win32gui.GetWindowRect(hwnd)
    x, y, x2, y2 = rect
    w, h = x2 - x, y2 - y
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    # PW_RENDERFULLCONTENT = 2 — renders GPU-accelerated content (Chrome needs this)
    ok = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    img.save(path)
    print(f"saved {path}  ({w}x{h})  printwindow_ok={ok}")


if __name__ == "__main__":
    out = sys.argv[1]
    needle = sys.argv[2] if len(sys.argv) > 2 else "MIDI Macropad"
    _, hwnd, title = find_window(needle)
    if not hwnd:
        print(f"window with '{needle}' not found", file=sys.stderr)
        sys.exit(1)
    print(f"found: {title}")
    capture(hwnd, out)
