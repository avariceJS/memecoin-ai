"""macOS process birth witnesses for conservative owned-process recovery.

Kernel start time includes microseconds; unlike ps lstart it is not truncated to
seconds. A disappeared or unprovable process is never signalled. Darwin has no
Linux pidfd equivalent: identity and group membership are rechecked immediately
before killpg; this does not claim an atomic kernel identity-bound signal API.
"""

from __future__ import annotations

import ctypes
import os
import platform
import signal


class _ProcBsdInfo(ctypes.Structure):
    # macOS SDK sys/proc_info.h: struct proc_bsdinfo, PROC_PIDTBSDINFO=3.
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "flags",
            "status",
            "xstatus",
            "pid",
            "ppid",
            "uid",
            "gid",
            "ruid",
            "rgid",
            "svuid",
            "svgid",
            "reserved",
        )
    ] + [
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("tdev", ctypes.c_uint32),
        ("tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    ]


def _info(pid: int):
    if platform.system() != "Darwin" or type(pid) is not int or pid <= 1:
        return None
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        function = library.proc_pidinfo
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        function.restype = ctypes.c_int
        info = _ProcBsdInfo()
        size = ctypes.sizeof(info)
        returned = function(pid, 3, 0, ctypes.byref(info), size)
        if returned != size or info.pid != pid or not info.start_sec:
            return None
        return info
    except (OSError, AttributeError):
        return None


def _identity(info) -> str:
    return f"darwin:{info.pid}:{info.start_sec}:{info.start_usec:06d}:{info.uid}"


def process_identity(pid: int) -> str | None:
    info = _info(pid)
    return _identity(info) if info is not None else None


def terminate_owned(pid: int, birth: str | None) -> bool:
    """Kill only a same-UID session/group leader with the recorded kernel birth."""
    if type(pid) is not int or pid <= 1 or pid == os.getpid() or not birth:
        return False
    info = _info(pid)
    if info is None or info.uid != os.getuid() or info.pgid != pid or _identity(info) != birth:
        return False
    try:
        if os.getpgid(pid) != pid or os.getsid(pid) != pid:
            return False
        # Re-read after the group/session lookups to narrow PID reuse races.
        current = _info(pid)
        if current is None or _identity(current) != birth or current.pgid != pid:
            return False
        os.killpg(pid, signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError):
        return False
