"""Read Apple Code Signing identity for static files and running processes.

This module uses the public Security and CoreFoundation C APIs. A code-directory
hash identifies signed Mach-O code; it complements the manifest's SHA-256 file
pin and does not identify Python's imported modules or dynamic libraries.
"""

from __future__ import annotations

import ctypes
import os
import platform
from pathlib import Path


class MacCodeIdentityError(ValueError):
    """A code-signing identity could not be read or validated."""


_HASH_BYTES = 20
_KCFNUMBER_SINT32 = 3
_KSEC_CSSIGNING_INFORMATION = 1 << 1
_KSEC_CSDYNAMIC_INFORMATION = 1 << 3


class _DictionaryKeyCallbacks(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copy_description", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
        ("hash", ctypes.c_void_p),
    ]


class _DictionaryValueCallbacks(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copy_description", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
    ]


def _frameworks():
    if platform.system() != "Darwin":
        raise MacCodeIdentityError("mac_code_identity_requires_macos")
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
    except OSError:
        raise MacCodeIdentityError("mac_code_identity_framework_unavailable") from None
    return security, core_foundation


def _constant_pointer(library, name: str) -> int:
    try:
        value = ctypes.c_void_p.in_dll(library, name).value
    except (OSError, ValueError):
        raise MacCodeIdentityError("mac_code_identity_symbol_unavailable") from None
    if value is None:
        raise MacCodeIdentityError("mac_code_identity_symbol_invalid")
    return value


def _release(core_foundation, *values: int | None) -> None:
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    core_foundation.CFRelease.restype = None
    for value in values:
        if value:
            core_foundation.CFRelease(value)


def _unique_identity(core_foundation, security, code: int, *, dynamic: bool):
    check = security.SecCodeCheckValidity if dynamic else security.SecStaticCodeCheckValidity
    check.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    check.restype = ctypes.c_int32
    if check(code, 0, None) != 0:
        raise MacCodeIdentityError("mac_code_identity_validity_failed")

    information = ctypes.c_void_p()
    copy_information = security.SecCodeCopySigningInformation
    copy_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    copy_information.restype = ctypes.c_int32
    flags = _KSEC_CSSIGNING_INFORMATION
    if dynamic:
        flags |= _KSEC_CSDYNAMIC_INFORMATION
    status = copy_information(code, flags, ctypes.byref(information))
    if status != 0 or not information.value:
        raise MacCodeIdentityError("mac_code_identity_signing_info_failed")
    try:
        unique_key = _constant_pointer(security, "kSecCodeInfoUnique")
        get_value = core_foundation.CFDictionaryGetValue
        get_value.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        get_value.restype = ctypes.c_void_p
        data = get_value(information, unique_key)
        if not data:
            raise MacCodeIdentityError("mac_code_identity_unsigned_code")

        get_type_id = core_foundation.CFGetTypeID
        get_type_id.argtypes = [ctypes.c_void_p]
        get_type_id.restype = ctypes.c_ulong
        data_type_id = core_foundation.CFDataGetTypeID
        data_type_id.argtypes = []
        data_type_id.restype = ctypes.c_ulong
        if get_type_id(data) != data_type_id():
            raise MacCodeIdentityError("mac_code_identity_hash_type_invalid")

        get_length = core_foundation.CFDataGetLength
        get_length.argtypes = [ctypes.c_void_p]
        get_length.restype = ctypes.c_long
        length = get_length(data)
        if length != _HASH_BYTES:
            raise MacCodeIdentityError("mac_code_identity_hash_size_unsupported")
        get_bytes = core_foundation.CFDataGetBytePtr
        get_bytes.argtypes = [ctypes.c_void_p]
        get_bytes.restype = ctypes.POINTER(ctypes.c_ubyte)
        pointer = get_bytes(data)
        if not pointer:
            raise MacCodeIdentityError("mac_code_identity_hash_unavailable")
        code_hash = bytes(pointer[index] for index in range(length)).hex()
        executable_path = None
        if dynamic:
            path_key = _constant_pointer(security, "kSecCodeInfoMainExecutable")
            url = get_value(information, path_key)
            get_url_type = core_foundation.CFURLGetTypeID
            get_url_type.argtypes = []
            get_url_type.restype = ctypes.c_ulong
            if not url or get_type_id(url) != get_url_type():
                raise MacCodeIdentityError("mac_code_identity_executable_path_invalid")
            get_path = core_foundation.CFURLGetFileSystemRepresentation
            get_path.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ubyte,
                ctypes.c_void_p,
                ctypes.c_long,
            ]
            get_path.restype = ctypes.c_ubyte
            buffer = ctypes.create_string_buffer(4096)
            if not get_path(url, 1, buffer, len(buffer)):
                raise MacCodeIdentityError("mac_code_identity_executable_path_unavailable")
            executable_path = os.fsdecode(buffer.value)
            if not Path(executable_path).is_absolute():
                raise MacCodeIdentityError("mac_code_identity_executable_path_invalid")
        return code_hash, executable_path
    finally:
        _release(core_foundation, information.value)


def static_code_directory_hash(path: str | Path) -> str:
    """Return the validated code-directory hash for a canonical signed Mach-O file."""
    candidate = Path(path)
    if not candidate.is_absolute() or str(candidate) != str(candidate.resolve(strict=True)):
        raise MacCodeIdentityError("mac_code_identity_path_not_canonical")
    security, core_foundation = _frameworks()
    raw_path = os.fsencode(candidate)
    create_url = core_foundation.CFURLCreateFromFileSystemRepresentation
    create_url.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_ubyte]
    create_url.restype = ctypes.c_void_p
    url = create_url(None, raw_path, len(raw_path), 0)
    if not url:
        raise MacCodeIdentityError("mac_code_identity_url_invalid")
    code = ctypes.c_void_p()
    create_code = security.SecStaticCodeCreateWithPath
    create_code.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    create_code.restype = ctypes.c_int32
    try:
        if create_code(url, 0, ctypes.byref(code)) != 0 or not code.value:
            raise MacCodeIdentityError("mac_code_identity_static_code_unavailable")
        code_hash, _ = _unique_identity(core_foundation, security, code, dynamic=False)
        return code_hash
    finally:
        _release(core_foundation, code.value, url)


def running_code_directory_hash(pid: int) -> str:
    """Return the validated code-directory hash associated with a live process PID."""
    code_hash, _ = running_code_identity(pid)
    return code_hash


def running_code_identity(pid: int) -> tuple[str, str]:
    """Return the validated code-directory hash and main executable path for a live PID."""
    if type(pid) is not int or pid <= 0 or pid > 0x7FFFFFFF:
        raise MacCodeIdentityError("mac_code_identity_pid_invalid")
    security, core_foundation = _frameworks()
    key = _constant_pointer(security, "kSecGuestAttributePid")
    key_callbacks = _DictionaryKeyCallbacks.in_dll(core_foundation, "kCFTypeDictionaryKeyCallBacks")
    value_callbacks = _DictionaryValueCallbacks.in_dll(
        core_foundation, "kCFTypeDictionaryValueCallBacks"
    )
    create_dictionary = core_foundation.CFDictionaryCreateMutable
    create_dictionary.argtypes = [
        ctypes.c_void_p,
        ctypes.c_long,
        ctypes.POINTER(_DictionaryKeyCallbacks),
        ctypes.POINTER(_DictionaryValueCallbacks),
    ]
    create_dictionary.restype = ctypes.c_void_p
    attributes = create_dictionary(
        None, 0, ctypes.byref(key_callbacks), ctypes.byref(value_callbacks)
    )
    if not attributes:
        raise MacCodeIdentityError("mac_code_identity_attributes_unavailable")

    number_value = ctypes.c_int32(pid)
    create_number = core_foundation.CFNumberCreate
    create_number.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    create_number.restype = ctypes.c_void_p
    number = create_number(None, _KCFNUMBER_SINT32, ctypes.byref(number_value))
    if not number:
        _release(core_foundation, attributes)
        raise MacCodeIdentityError("mac_code_identity_pid_value_unavailable")

    set_value = core_foundation.CFDictionarySetValue
    set_value.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_value.restype = None
    set_value(attributes, key, number)
    code = ctypes.c_void_p()
    copy_guest = security.SecCodeCopyGuestWithAttributes
    copy_guest.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    copy_guest.restype = ctypes.c_int32
    try:
        if copy_guest(None, attributes, 0, ctypes.byref(code)) != 0 or not code.value:
            raise MacCodeIdentityError("mac_code_identity_process_unavailable")
        code_hash, executable_path = _unique_identity(core_foundation, security, code, dynamic=True)
        if executable_path is None:
            raise MacCodeIdentityError("mac_code_identity_executable_path_unavailable")
        return code_hash, executable_path
    finally:
        _release(core_foundation, code.value, number, attributes)
