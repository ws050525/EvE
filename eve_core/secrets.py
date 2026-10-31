"""API Key 的本地保存：Windows DPAPI 加密，不落明文。

云端厂商的 key 存在 `%APPDATA%` 或项目 `.data/keys.json`，只能被当前 Windows 用户解开。
不引第三方库，纯 ctypes 调 crypt32。
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import json
from pathlib import Path

crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        kernel32.LocalFree(blob.pbData)


def protect(text: str) -> str:
    blob_in, _buf = _to_blob(text.encode("utf-8"))
    blob_out = _Blob()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError(f"CryptProtectData 失败：{ctypes.get_last_error()}")
    return base64.b64encode(_from_blob(blob_out)).decode("ascii")


def unprotect(token: str) -> str:
    blob_in, _buf = _to_blob(base64.b64decode(token))
    blob_out = _Blob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError(
            f"CryptUnprotectData 失败：{ctypes.get_last_error()}（可能换了 Windows 用户或机器）"
        )
    return _from_blob(blob_out).decode("utf-8")


class KeyStore:
    """按 model_id 存取加密的 API Key。"""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "keys.json"

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, model_id: str) -> str | None:
        token = self._load().get(model_id)
        if not token:
            return None
        try:
            return unprotect(token)
        except OSError:
            return None

    def set(self, model_id: str, api_key: str) -> None:
        data = self._load()
        data[model_id] = protect(api_key)
        self._save(data)

    def delete(self, model_id: str) -> bool:
        data = self._load()
        if model_id not in data:
            return False
        data.pop(model_id)
        self._save(data)
        return True

    def ids(self) -> list[str]:
        return sorted(self._load())
