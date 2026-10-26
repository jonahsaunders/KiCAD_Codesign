"""Create an isolated Windows desktop for native widget tests and rendering.

Call before importing wx. This never switches the user's input desktop and does
not request DESKTOP_SWITCHDESKTOP access. Windows releases the temporary desktop
when the test process exits. No settings or security descriptors are changed.
"""
import ctypes as C
from ctypes import wintypes as W
import os


def isolate():
    if os.name != 'nt':
        raise RuntimeError('Native interface rendering requires Windows.')
    user = C.WinDLL('user32', use_last_error=True)
    user.CreateDesktopW.argtypes = [W.LPCWSTR, W.LPCWSTR, C.c_void_p, W.DWORD, W.DWORD, C.c_void_p]
    user.CreateDesktopW.restype = W.HANDLE
    user.SetThreadDesktop.argtypes = [W.HANDLE]
    user.SetThreadDesktop.restype = W.BOOL
    user.CloseDesktop.argtypes = [W.HANDLE]
    user.CloseDesktop.restype = W.BOOL
    handle = user.CreateDesktopW(f'CodesignTest-{os.getpid()}', None, None, 0, 0x00FF, None)
    if not handle:
        raise C.WinError(C.get_last_error())
    if not user.SetThreadDesktop(handle):
        error = C.get_last_error()
        user.CloseDesktop(handle)
        raise C.WinError(error)
    return handle
