"""Store and read the MES credential through Windows Credential Manager.

The password is kept in the current Windows user's credential vault, never in
the repository, a JSON file, or a Scheduled Task command line.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import sys
import base64
from pathlib import Path
from ctypes import wintypes


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
TARGET = "LIMSData/LIMS"
ROOT = Path(__file__).resolve().parents[1]
DPAPI_PATH = ROOT / "data" / ".lims_credentials.dpapi"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi = ctypes.WinDLL("Advapi32.dll")
_advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
_advapi.CredWriteW.restype = wintypes.BOOL
_advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
_advapi.CredReadW.restype = wintypes.BOOL
_advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi.CredDeleteW.restype = wintypes.BOOL
_advapi.CredFree.argtypes = [ctypes.c_void_p]
_advapi.CredFree.restype = None


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_crypt32 = ctypes.WinDLL("Crypt32.dll")
_crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32 = ctypes.WinDLL("Kernel32.dll")
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def read_credential(target: str = TARGET) -> tuple[str, str] | None:
    pointer = ctypes.POINTER(CREDENTIAL)()
    if not _advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return None
    try:
        item = pointer.contents
        username = item.UserName or ""
        blob = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
        return username, blob.decode("utf-8")
    finally:
        _advapi.CredFree(pointer)


def _protect(value: bytes) -> bytes:
    source = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source_blob = DATA_BLOB(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    protected_blob = DATA_BLOB()
    if not _crypt32.CryptProtectData(ctypes.byref(source_blob), "LIMSData", None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(protected_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(protected_blob.pbData, protected_blob.cbData)
    finally:
        _kernel32.LocalFree(protected_blob.pbData)


def _unprotect(value: bytes) -> bytes:
    source = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source_blob = DATA_BLOB(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    plain_blob = DATA_BLOB()
    if not _crypt32.CryptUnprotectData(ctypes.byref(source_blob), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(plain_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(plain_blob.pbData, plain_blob.cbData)
    finally:
        _kernel32.LocalFree(plain_blob.pbData)


def read_dpapi_credential(path: Path = DPAPI_PATH) -> tuple[str, str] | None:
    try:
        encoded = path.read_text(encoding="ascii").strip()
        payload = json.loads(_unprotect(base64.b64decode(encoded)).decode("utf-8"))
        username, password = str(payload.get("username", "")), str(payload.get("password", ""))
        return (username, password) if username and password else None
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, UnicodeError):
        return None


def write_dpapi_credential(username: str, password: str, path: Path = DPAPI_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"username": username, "password": password}, ensure_ascii=False).encode("utf-8")
    path.write_text(base64.b64encode(_protect(payload)).decode("ascii"), encoding="ascii")


def delete_dpapi_credential(path: Path = DPAPI_PATH) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def write_credential(username: str, password: str, target: str = TARGET) -> None:
    blob = password.encode("utf-8")
    blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = CREDENTIAL()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = username
    if not _advapi.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


def delete_credential(target: str = TARGET) -> bool:
    if _advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == 1168:  # ERROR_NOT_FOUND
        return False
    raise ctypes.WinError(error)


def load_credential() -> tuple[str, str] | None:
    return read_credential() or read_dpapi_credential()


def main() -> int:
    parser = argparse.ArgumentParser(description="配置MES Windows凭据")
    sub = parser.add_subparsers(dest="command", required=True)
    set_parser = sub.add_parser("set", help="交互式写入凭据")
    set_parser.add_argument("--username", required=True)
    sub.add_parser("check", help="只检查凭据是否存在，不输出密码")
    sub.add_parser("delete", help="删除当前用户的MES凭据")
    args = parser.parse_args()

    if args.command == "set":
        password = getpass.getpass("MES密码（不会显示，也不会写入代码库）: ")
        if not password:
            print("密码不能为空", file=sys.stderr)
            return 2
        username = args.username.strip()
        storage = "credential_manager"
        try:
            write_credential(username, password)
        except OSError:
            write_dpapi_credential(username, password)
            storage = "dpapi_current_windows_user"
        print(json.dumps({"configured": True, "target": TARGET, "storage": storage, "username": username}, ensure_ascii=False))
        return 0
    if args.command == "check":
        item = load_credential()
        print(json.dumps({"configured": bool(item), "target": TARGET, "username": item[0] if item else None, "dpapi_file_present": DPAPI_PATH.exists()}, ensure_ascii=False))
        return 0 if item and item[0] and item[1] else 1
    deleted = delete_credential()
    dpapi_deleted = delete_dpapi_credential()
    print(json.dumps({"deleted": deleted or dpapi_deleted, "target": TARGET}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
