"""Convierte horas de cierre a FILETIME para cotejar identidades de procesos."""

from __future__ import annotations

from datetime import datetime, timezone


def hora_inicio(pid: int) -> str:  # pragma: no cover
    """Hora de creación del proceso (FILETIME como texto, "" si no existe). Distingue un pid reusado."""
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    ft = [wintypes.FILETIME() for _ in range(4)]
    ok = k.GetProcessTimes(h, *[ctypes.byref(x) for x in ft])
    k.CloseHandle(h)
    if not ok:
        return ""
    return str((ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime)


def fin_filetime(fin: str) -> int | None:
    try:
        fecha = datetime.fromisoformat(fin.replace("Z", "+00:00"))
    except ValueError:
        return None
    if fecha.tzinfo is None:
        return None
    delta = fecha.astimezone(timezone.utc) - datetime(1601, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 10_000_000
            + delta.microseconds * 10)
