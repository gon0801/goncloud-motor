"""El supervisor conserva OAuth; los trabajadores reciben solo acceso de suscripción."""

from __future__ import annotations

import base64
import contextlib
import ctypes
import hashlib
import http.client
import json
import math
import os
import platform
import socket
import ssl
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

_CLIENTE = "17e5f671-d194-4dfb-9706-5516cb48c098"
_IDENTIDAD = ("iss", "sub", "client_id", "user_id", "device_id", "scope", "type")


@dataclass(frozen=True)
class AccesoKimi:
    token: str = field(repr=False)
    expira: float


def _exigir(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise RuntimeError(mensaje)


def _claims(token: str) -> dict:
    try:
        partes = token.split(".")
        _exigir(len(partes) == 3, "JWT OAuth de Kimi invalido")
        datos = json.loads(
            base64.urlsafe_b64decode(partes[1] + "=" * (-len(partes[1]) % 4))
        )
        _exigir(
            isinstance(datos, dict)
            and all(isinstance(datos.get(k), str) and datos[k] for k in _IDENTIDAD)
            and isinstance(datos.get("exp"), (int, float))
            and not isinstance(datos["exp"], bool)
            and math.isfinite(datos["exp"]),
            "identidad OAuth de Kimi invalida",
        )
        return datos
    except (ValueError, TypeError, AttributeError, KeyError):
        raise RuntimeError("JWT OAuth de Kimi invalido") from None


def _json(raw: bytes) -> dict:
    try:
        datos = json.loads(raw)
        _exigir(isinstance(datos, dict), "credencial OAuth de Kimi invalida")
        return datos
    except (ValueError, UnicodeError):
        raise RuntimeError("credencial OAuth de Kimi invalida") from None


def _privado(path: Path, raw: bytes) -> None:
    fd, temporal = tempfile.mkstemp(prefix=".kimi-", dir=path.parent)
    try:
        os.chmod(temporal, 0o600)
        with os.fdopen(fd, "wb") as salida:
            salida.write(raw)
            salida.flush()
            os.fsync(salida.fileno())
        if os.name == "nt":
            from ctypes import wintypes

            mover = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
            mover.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
            mover.restype = wintypes.BOOL
            _exigir(
                bool(mover(temporal, str(path), 0x9)),
                "no se pudo conservar la respuesta OAuth",
            )
        else:
            os.replace(temporal, path)
            directorio = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directorio)
            finally:
                os.close(directorio)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporal)


@contextlib.contextmanager
def _canonico(path: Path):
    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        crear = kernel.CreateFileW
        crear.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        crear.restype = wintypes.HANDLE
        cerrar = kernel.CloseHandle
        cerrar.argtypes = [wintypes.HANDLE]
        cerrar.restype = wintypes.BOOL
        handle = crear(str(path), 0xC0000000, 0, None, 3, 0x80000000, None)
        _exigir(
            handle != ctypes.c_void_p(-1).value,
            "acceso OAuth de Kimi ocupado o inaccesible",
        )
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            cerrar(handle)
            raise
        with os.fdopen(fd, "r+b") as archivo:
            yield archivo
    else:
        import fcntl

        # Coordina nuestros supervisores; el SDK externo puede ignorar flock.
        # Antes de escribir comprobamos tambien version e identidad del archivo.
        with path.open("r+b") as archivo:
            fcntl.flock(archivo, fcntl.LOCK_EX)
            yield archivo


def _respuesta(raw: bytes, anterior: dict, *, permitir_vencido: bool = False) -> dict:
    datos = _json(raw)
    ttl = datos.get("expires_in")
    _exigir(
        isinstance(ttl, (int, float))
        and not isinstance(ttl, bool)
        and math.isfinite(ttl)
        and ttl > 0,
        "duracion OAuth de Kimi invalida",
    )
    for nombre in ("access_token", "refresh_token"):
        nuevo, viejo = _claims(datos.get(nombre)), _claims(anterior.get(nombre))
        _exigir(
            all(nuevo[k] == viejo[k] for k in _IDENTIDAD)
            and (
                (permitir_vencido and nombre == "access_token")
                or nuevo["exp"] > time.time()
            ),
            "respuesta OAuth de Kimi de otra identidad o vencida",
        )
    _exigir(
        datos.get("scope", "") == anterior.get("scope", "")
        and datos.get("token_type", "Bearer") == anterior.get("token_type", ""),
        "alcance OAuth de Kimi distinto",
    )
    # expires_at procede de JWT: un journal recuperado no reinicia su TTL.
    return {
        "access_token": datos["access_token"],
        "refresh_token": datos["refresh_token"],
        "expires_at": _claims(datos["access_token"])["exp"],
        "scope": datos.get("scope", ""),
        "token_type": datos.get("token_type", "Bearer"),
        "expires_in": ttl,
    }


def _refresh(origen: Path, refresh: str) -> bytes:
    device = (origen / "device_id").read_text(encoding="utf-8").strip()
    _exigir(
        bool(device) and all(" " <= c <= "~" for c in device),
        "device OAuth de Kimi invalido",
    )
    limpiar = lambda texto: (
        "".join(c for c in texto if " " <= c <= "~").strip() or "unknown"
    )
    version = platform.version() if os.name == "nt" else platform.release()
    modelo = ("Windows " if os.name == "nt" else platform.system() + " ") + version
    modelo += " " + (
        "x64"
        if platform.machine().lower() in ("amd64", "x86_64")
        else platform.machine()
    )
    headers = {
        "User-Agent": "kimi-code-cli/2.3.3",
        "X-Msh-Platform": "kimi_code_cli",
        "X-Msh-Version": "2.3.3",
        "X-Msh-Device-Name": limpiar(socket.gethostname()),
        "X-Msh-Device-Model": limpiar(modelo),
        "X-Msh-Os-Version": limpiar(version),
        "X-Msh-Device-Id": device,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    conexion = http.client.HTTPSConnection(
        "auth.kimi.com", timeout=30, context=ssl.create_default_context()
    )
    try:
        conexion.request(
            "POST",
            "/api/oauth/token",
            urlencode(
                {
                    "client_id": _CLIENTE,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                }
            ),
            headers,
        )
        respuesta = conexion.getresponse()
        raw = respuesta.read()
        _exigir(
            respuesta.status == 200,
            "OAuth de Kimi requiere iniciar sesion"
            if respuesta.status in (400, 401, 403)
            else "OAuth de Kimi no disponible",
        )
        return raw
    except (OSError, http.client.HTTPException):
        raise RuntimeError("OAuth de Kimi no disponible") from None
    finally:
        conexion.close()


def acceso_kimi(origen: Path) -> AccesoKimi:
    path = origen / "credentials/kimi-code.json"
    pendiente = path.with_name("kimi-code.motor-pending.json")
    with _canonico(path) as archivo:
        inicial = archivo.read()
        stat = os.fstat(archivo.fileno())
        huella = hashlib.sha256(inicial).hexdigest()
        respuesta = None
        journal = _json(pendiente.read_bytes()) if pendiente.exists() else None
        try:
            anterior = _json(inicial)
        except RuntimeError:
            # El HANDLE anterior pudo cerrarse a mitad de una escritura in-place.
            _exigir(
                journal is not None, "credencial OAuth de Kimi invalida sin respaldo"
            )
            baseline = base64.b64decode(journal["baseline_base64"], validate=True)
            _exigir(
                hashlib.sha256(baseline).hexdigest() == journal.get("baseline_sha256"),
                "respaldo OAuth de Kimi invalido",
            )
            anterior = _json(baseline)
            huella = journal["baseline_sha256"]
        acceso = _claims(anterior.get("access_token"))
        _exigir(acceso["client_id"] == _CLIENTE, "cliente OAuth de Kimi distinto")
        if journal is not None and journal.get("baseline_sha256") == huella:
            respuesta = base64.b64decode(journal["response_base64"], validate=True)
        desde_pendiente = respuesta is not None
        if respuesta is None and acceso["exp"] > time.time():
            return AccesoKimi(anterior["access_token"], acceso["exp"])
        if respuesta is None:
            refresh = anterior.get("refresh_token")
            _exigir(
                isinstance(refresh, str) and bool(refresh),
                "OAuth de Kimi requiere iniciar sesion",
            )
            _exigir(
                _claims(refresh)["client_id"] == _CLIENTE,
                "cliente OAuth de Kimi distinto",
            )
            respuesta = _refresh(origen, refresh)
            # Durable antes de interpretar HTTP200: el refresh anterior ya puede estar consumido.
            _privado(
                pendiente,
                json.dumps(
                    {
                        "baseline_sha256": huella,
                        "baseline_base64": base64.b64encode(inicial).decode("ascii"),
                        "response_base64": base64.b64encode(respuesta).decode("ascii"),
                    }
                ).encode(),
            )
        nuevo = _respuesta(respuesta, anterior, permitir_vencido=desde_pendiente)
        archivo.seek(0)
        actual = os.stat(path) if os.name != "nt" else stat
        _exigir(
            archivo.read() == inicial
            and (actual.st_dev, actual.st_ino) == (stat.st_dev, stat.st_ino),
            "el acceso OAuth de Kimi cambio durante la renovacion",
        )
        archivo.seek(0)
        archivo.write((json.dumps(nuevo, indent=2) + "\n").encode())
        archivo.truncate()
        archivo.flush()
        os.fsync(archivo.fileno())
        pendiente.unlink()
        if nuevo["expires_at"] > time.time():
            return AccesoKimi(nuevo["access_token"], nuevo["expires_at"])
    # Un pendiente viejo se instala primero: el siguiente refresh usa su R1, nunca R0.
    return acceso_kimi(origen)
