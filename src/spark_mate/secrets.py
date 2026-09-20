from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data: bytes, *, encrypt: bool) -> bytes:
    if os.name != 'nt':
        raise ValueError('登录态加密仅支持 Windows')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    src, dest = Blob(len(data), buf), Blob()
    op = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    op.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    op.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not op(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dest)):
        raise ValueError('无法解密本机登录状态，请重新扫码')
    try:
        return ctypes.string_at(dest.data, dest.size)
    finally:
        kernel32.LocalFree(dest.data)


class Vault:
    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, state: dict) -> None:
        encrypted = crypt(json.dumps(state, ensure_ascii=False).encode('utf-8'), encrypt=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_bytes(encrypted)
        os.replace(temp, self.path)

    def load(self) -> dict:
        try:
            data = json.loads(crypt(self.path.read_bytes(), encrypt=False))
            if not isinstance(data, dict) or not isinstance(data.get('cookies'), list):
                raise ValueError('无效的登录状态')  # noqa: TRY004 -- corrupted vault has a ValueError contract
            return data
        except (OSError, ValueError) as exc:
            raise ValueError('登录状态缺失或损坏，请重新扫码登录') from exc

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
