"""Encrypt desktop cache payloads with the signed-in Windows user's DPAPI key."""
import ctypes
from ctypes import wintypes
import os


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value, decrypt=False):
    buffer = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(outgoing.data)


def protect(value):
    if os.name == "nt":
        return b"DP1" + _crypt(value)
    # Development/tests on non-Windows machines; production target is Windows.
    return b"DEV" + value


def unprotect(value):
    if value[:3] == b"DP1":
        if os.name != "nt":
            raise RuntimeError("This cache belongs to its original Windows user")
        return _crypt(value[3:], decrypt=True)
    if value[:3] == b"DEV" and os.name != "nt":
        return value[3:]
    raise RuntimeError("Unsupported cache format")
