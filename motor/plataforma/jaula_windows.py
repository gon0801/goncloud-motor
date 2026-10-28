"""AppContainer con permisos BFS y escritorio privado, también desde OpenSSH Session 0.

Usa el contrato SBOX 0.1.0 de processmodel.dll. No cambia ACL de rutas instaladas.
"""

from __future__ import annotations

import atexit
import ctypes as c
import json
import os
import struct
import subprocess
import threading
import time
import uuid
from ctypes import wintypes as w
from pathlib import Path

from .windows import JobMatarAlCerrar, _jobs, hora_inicio

JOB_OBJECT_UILIMIT_ALL = 0xFF
UOI_NAME = 2


def imagen_proceso(pid: int, inicio: str) -> str | None:  # pragma: no cover
    k = c.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [
        w.HANDLE,
        w.DWORD,
        w.LPWSTR,
        c.POINTER(w.DWORD),
    ]
    k.QueryFullProcessImageNameW.restype = w.BOOL
    k.CloseHandle.argtypes = [w.HANDLE]
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        texto = c.create_unicode_buffer(32768)
        tamano = w.DWORD(len(texto))
        if not k.QueryFullProcessImageNameW(handle, 0, texto, c.byref(tamano)):
            return None
        return texto.value if hora_inicio(pid) == inicio else None
    finally:
        k.CloseHandle(handle)


def disponible() -> bool:
    try:
        query = c.WinDLL(
            "processmodel.dll", use_last_error=True
        ).Experimental_QuerySandboxSupport
        query.argtypes, query.restype = [c.POINTER(c.c_uint64)], w.BOOL
        capacidades = c.c_uint64()
        return bool(query(c.byref(capacidades)) and capacidades.value & 1)
    except (OSError, AttributeError):
        return False


class Inicio(c.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("xs", w.DWORD),
        ("ys", w.DWORD),
        ("charsx", w.DWORD),
        ("charsy", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved2size", w.WORD),
        ("reserved2", c.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class Informacion(c.Structure):
    _fields_ = [
        ("process", w.HANDLE),
        ("thread", w.HANDLE),
        ("pid", w.DWORD),
        ("tid", w.DWORD),
    ]


class Seguridad(c.Structure):
    _fields_ = [("length", w.DWORD), ("sd", c.c_void_p), ("inherit", w.BOOL)]


def _api():
    k = c.WinDLL("kernel32", use_last_error=True)
    for nombre, args, resultado in (
        ("CloseHandle", [w.HANDLE], w.BOOL),
        ("LocalFree", [c.c_void_p], c.c_void_p),
        ("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD),
        ("GetExitCodeProcess", [w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        ("TerminateProcess", [w.HANDLE, w.UINT], w.BOOL),
        ("ResumeThread", [w.HANDLE], w.DWORD),
    ):
        fn = getattr(k, nombre)
        fn.argtypes, fn.restype = args, resultado
    return k


def _especificacion(limites: dict, *, psec: bool = False) -> bytes:
    # Esquemas Microsoft SBOX 0.1.0 y ProcessSecurityEnvironment 1.1.
    buf = bytearray(80)
    if psec:
        struct.pack_into("<I", buf, 0, 32)
        buf[4:8] = b"PSEC"
        struct.pack_into("<HH9H", buf, 8, 22, 48, 4, 8, 12, 16, 24, 28, 32, 36, 40)
        struct.pack_into("<iHH", buf, 32, 24, 1, 1)
        struct.pack_into("<Q", buf, 48, JOB_OBJECT_UILIMIT_ALL)
        campo_capacidades, campo_escritura, campo_lectura, campo_denegado, campo_red = (
            40,
            56,
            60,
            64,
            68,
        )
    else:
        struct.pack_into("<I", buf, 0, 36)
        buf[4:8] = b"SBOX"
        struct.pack_into(
            "<HH12H", buf, 8, 28, 44, 4, 8, 0, 0, 36, 9, 24, 12, 16, 28, 0, 20
        )
        struct.pack_into("<i", buf, 36, 28)
        buf[44] = buf[45] = 1
        struct.pack_into("<Q", buf, 72, JOB_OBJECT_UILIMIT_ALL)
        campo_capacidades, campo_escritura, campo_lectura, campo_denegado, campo_red = (
            60,
            48,
            52,
            56,
            64,
        )

    def cadena(valor):
        while len(buf) % 4:
            buf.append(0)
        posicion = len(buf)
        texto = valor.encode("utf-8")
        buf.extend(struct.pack("<I", len(texto)) + texto + b"\0")
        return posicion

    def puntero(campo, destino):
        struct.pack_into("<I", buf, campo, destino - campo)

    def vector(campo, valores):
        while len(buf) % 4:
            buf.append(0)
        posicion = len(buf)
        buf.extend(struct.pack("<I", len(valores)) + bytes(4 * len(valores)))
        puntero(campo, posicion)
        for i, valor in enumerate(valores):
            puntero(posicion + 4 + 4 * i, cadena(valor))

    if psec:
        vector(72, limites["enumeracion"])
    else:
        puntero(40, cadena("0.1.0"))
    capacidades = (
        "registryRead,internetClient" if limites["internet"] else "registryRead"
    )
    puntero(campo_capacidades, cadena(capacidades))
    vector(campo_escritura, limites["escritura"])
    vector(campo_lectura, limites["lectura"])
    vector(campo_denegado, [])

    def tabla(campos, tamano):
        while len(buf) % 2:
            buf.append(0)
        vtable = len(buf)
        buf.extend(struct.pack("<HH", 4 + 2 * len(campos), tamano))
        buf.extend(struct.pack("<" + "H" * len(campos), *campos))
        while len(buf) % 4:
            buf.append(0)
        posicion = len(buf)
        buf.extend(bytes(tamano))
        struct.pack_into("<i", buf, posicion, posicion - vtable)
        return posicion

    def referencias(campo, cantidad):
        while len(buf) % 4:
            buf.append(0)
        posicion = len(buf)
        buf.extend(struct.pack("<I", cantidad) + bytes(4 * cantidad))
        puntero(campo, posicion)
        return posicion + 4

    red = tabla([0, 4, 0], 8)
    salida = tabla([0, 4, 0], 8) if limites["internet"] else tabla([0, 0, 0], 4)
    puntero(red + 4, salida)
    puntero(campo_red, red)
    if limites["internet"]:
        destinos = limites["proveedor"]
        permitidas = referencias(salida + 4, 1)
        regla = tabla([4, 8], 12)
        puntero(permitidas, regla)
        rutas = referencias(regla + 4, len(destinos))
        for i, direccion in enumerate(destinos):
            destino = tabla([4, 0], 8)
            puntero(rutas + 4 * i, destino)
            subred = tabla([4, 8], 12)
            puntero(destino + 4, subred)
            buf[subred + 8] = 128 if ":" in direccion else 32
            puntero(subred + 4, cadena(direccion))
        puertos = referencias(regla + 8, 1)
        puerto = tabla([4, 6, 0], 8)
        puntero(puertos, puerto)
        buf[puerto + 4] = 1
        struct.pack_into("<H", buf, puerto + 6, 443)
    return bytes(buf)


_estacion_lock = threading.Lock()


class Escritorio:
    def __init__(self):
        self.u = c.WinDLL("user32", use_last_error=True)
        self.k = _api()
        self.station = self.desktop = None
        self.nombre = ""
        self.desktop_nombre = "motor-" + uuid.uuid4().hex
        for nombre, args, resultado in (
            (
                "CreateWindowStationW",
                [w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(Seguridad)],
                w.HANDLE,
            ),
            ("GetProcessWindowStation", [], w.HANDLE),
            ("SetProcessWindowStation", [w.HANDLE], w.BOOL),
            (
                "GetUserObjectInformationW",
                [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)],
                w.BOOL,
            ),
            (
                "CreateDesktopW",
                [
                    w.LPCWSTR,
                    w.LPCWSTR,
                    c.c_void_p,
                    w.DWORD,
                    w.DWORD,
                    c.POINTER(Seguridad),
                ],
                w.HANDLE,
            ),
            ("CloseDesktop", [w.HANDLE], w.BOOL),
            ("CloseWindowStation", [w.HANDLE], w.BOOL),
        ):
            fn = getattr(self.u, nombre)
            fn.argtypes, fn.restype = args, resultado
        adv = c.WinDLL("advapi32", use_last_error=True)
        convertir = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convertir.argtypes = [
            w.LPCWSTR,
            w.DWORD,
            c.POINTER(c.c_void_p),
            c.POINTER(w.DWORD),
        ]
        convertir.restype = w.BOOL
        sd = c.c_void_p()
        # El descriptor solo se aplica al crear; nunca cambia ACL de una estacion existente.
        sddl = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;OW)(A;;GA;;;AC)(A;;GA;;;S-1-15-2-2)(A;;GA;;;RC)S:(ML;;NW;;;LW)"
        if not convertir(sddl, 1, c.byref(sd), None):
            raise c.WinError(c.get_last_error())
        sa = Seguridad(c.sizeof(Seguridad), sd, False)
        try:
            with _estacion_lock:
                original = self.u.GetProcessWindowStation()
                self.station = self.u.CreateWindowStationW(
                    None, 0, 0xF037F, c.byref(sa)
                )
                if not self.station or not self.u.SetProcessWindowStation(self.station):
                    raise c.WinError(c.get_last_error())
                try:
                    nombre = c.create_unicode_buffer(512)
                    necesario = w.DWORD()
                    if not self.u.GetUserObjectInformationW(
                        self.station,
                        UOI_NAME,
                        nombre,
                        c.sizeof(nombre),
                        c.byref(necesario),
                    ):
                        raise c.WinError(c.get_last_error())
                    self.nombre = nombre.value
                    if self.nombre.casefold() == "winsta0":
                        raise RuntimeError(
                            "la jaula requiere una estacion no interactiva"
                        )
                    self.desktop = self.u.CreateDesktopW(
                        self.desktop_nombre, None, None, 0, 0xF01FF, c.byref(sa)
                    )
                    if not self.desktop:
                        raise c.WinError(c.get_last_error())
                finally:
                    if not self.u.SetProcessWindowStation(original):
                        raise c.WinError(c.get_last_error())
        except BaseException:
            self.cerrar()
            raise
        finally:
            self.k.LocalFree(sd)

    def cerrar(self):
        if self.desktop:
            self.u.CloseDesktop(self.desktop)
            self.desktop = None
        if self.station:
            self.u.CloseWindowStation(self.station)
            self.station = None


class Proceso:
    def __init__(self, info: Informacion, argv: list[str], stdout, stderr):
        self.pid, self._handle, self._thread = info.pid, info.process, info.thread
        self.args, self.stdout, self.stderr = argv, stdout, stderr
        self.returncode = None
        self.k = _api()
        self._lectores = None
        self._salidas = ["", ""]

    def poll(self):
        if self.returncode is None and self.k.WaitForSingleObject(self._handle, 0) == 0:
            codigo = w.DWORD()
            if not self.k.GetExitCodeProcess(self._handle, c.byref(codigo)):
                raise c.WinError(c.get_last_error())
            self.returncode = codigo.value
        return self.returncode

    def wait(self, timeout=None):
        espera = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        resultado = self.k.WaitForSingleObject(self._handle, espera)
        if resultado == 0x102:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if resultado != 0:
            raise c.WinError(c.get_last_error())
        return self.poll()

    def kill(self):
        if self.poll() is None and not self.k.TerminateProcess(self._handle, 1):
            raise c.WinError(c.get_last_error())

    def communicate(self, timeout=None):
        if self._lectores is None:
            self._lectores = []

            def leer(i, flujo):
                self._salidas[i] = flujo.read()

            for i, flujo in enumerate((self.stdout, self.stderr)):
                if flujo is not None:
                    t = threading.Thread(target=leer, args=(i, flujo), daemon=True)
                    self._lectores.append(t)
                    t.start()
        fin = None if timeout is None else time.monotonic() + timeout
        self.wait(timeout)
        for t in self._lectores:
            t.join(None if fin is None else max(0, fin - time.monotonic()))
            if t.is_alive():
                raise subprocess.TimeoutExpired(self.args, timeout)
        return tuple(self._salidas)

    def __del__(self):
        for flujo in (self.stdout, self.stderr):
            if flujo is not None:
                flujo.close()
        for atributo in ("_thread", "_handle"):
            valor = getattr(self, atributo, None)
            if valor:
                self.k.CloseHandle(valor)
                setattr(self, atributo, None)


def _guardar(path: Path, datos: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos), encoding="utf-8")
    os.replace(temporal, path)


def limpiar(control: Path) -> None:
    path = Path(control) / "jaula.json"
    if not path.is_file():
        return
    datos = json.loads(path.read_text(encoding="utf-8"))
    identidad = datos["identidad"]
    if (
        not identidad.startswith("motor-")
        or str(uuid.UUID(hex=identidad[6:])).replace("-", "") != identidad[6:]
    ):
        raise ValueError("identidad de jaula ajena")
    if (Path(control) / "jaula-cierre.json").is_file():
        cierre = json.loads(
            (Path(control) / "jaula-cierre.json").read_text(encoding="utf-8")
        )
        if cierre.get("perfil_eliminado") and cierre.get("identidad") == identidad:
            return
    borrar = c.WinDLL("userenv").DeleteAppContainerProfile
    borrar.argtypes, borrar.restype = [w.LPCWSTR], c.c_long
    resultado = borrar(identidad)
    _guardar(
        Path(control) / "jaula-cierre.json",
        {
            "identidad": identidad,
            "perfil_eliminado": resultado in (0, -2147024894),
            "hresult": resultado,
        },
    )


class Recursos(JobMatarAlCerrar):
    def __init__(self, nombre, escritorio, proceso, control, entorno=None):
        super().__init__(nombre)
        self.escritorio = escritorio
        self.proceso, self.control = proceso, Path(control)
        self.entorno = entorno
        self.cerrado = False

    def cerrar(self):
        if self.cerrado:
            return
        self.cerrado = True
        super().cerrar()
        self.proceso.wait(5)
        if self.entorno:
            self.entorno.cerrar()
        self.escritorio.cerrar()
        limpiar(self.control)


def crear(
    cmd: list[str],
    limites: dict,
    *,
    cwd,
    env,
    stdin,
    stdout,
    stderr,
    control=None,
    **opciones,
):
    from . import ProcesoCreado

    if (
        stdin != subprocess.DEVNULL
        or stdout != subprocess.PIPE
        or stderr not in (subprocess.PIPE, subprocess.STDOUT)
    ):
        raise ValueError("la jaula necesita stdin cerrado y salidas administradas")
    import msvcrt

    control = (
        Path(control)
        if control
        else Path(limites["runtime"]).with_name(
            Path(limites["runtime"]).name + "-control"
        )
    )
    escritorio = Escritorio()
    identidad = "motor-" + uuid.uuid4().hex
    _guardar(control / "jaula.json", {"identidad": identidad})
    extremos = []
    p = None
    job = None
    entorno_seguro = None
    lecturas = []
    nulo = os.open(os.devnull, os.O_RDONLY)
    os.set_inheritable(nulo, True)
    try:
        for _ in range(1 if stderr == subprocess.STDOUT else 2):
            rd, wr = os.pipe()
            os.set_inheritable(wr, True)
            extremos.append(wr)
            lecturas.append(
                os.fdopen(
                    rd,
                    "r",
                    encoding=opciones.get("encoding", "utf-8"),
                    errors=opciones.get("errors", "replace"),
                )
            )
        si = Inicio()
        si.cb, si.desktop, si.flags = (
            c.sizeof(Inicio),
            escritorio.nombre + "\\" + escritorio.desktop_nombre,
            0x100,
        )
        si.stdin = msvcrt.get_osfhandle(nulo)
        si.stdout = msvcrt.get_osfhandle(extremos[0])
        si.stderr = msvcrt.get_osfhandle(extremos[-1])
        info = Informacion()
        if "enumeracion" in limites:
            from .psec_windows import Entorno

            entorno_seguro = Entorno(_especificacion(limites, psec=True))
            entorno_seguro.crear(cmd, cwd, env, si, info)
        else:
            api = c.WinDLL(
                "processmodel.dll", use_last_error=True
            ).Experimental_CreateProcessInSandbox
            api.argtypes = [
                w.LPCWSTR,
                w.LPWSTR,
                c.c_void_p,
                c.c_void_p,
                w.BOOL,
                w.DWORD,
                c.c_void_p,
                w.LPCWSTR,
                c.POINTER(Inicio),
                w.LPCWSTR,
                c.c_void_p,
                w.DWORD,
                c.POINTER(Informacion),
            ]
            api.restype = w.BOOL
            entorno = c.create_unicode_buffer(
                "\0".join(k + "=" + v for k, v in sorted(env.items())) + "\0\0"
            )
            especificacion = _especificacion(limites)
            argv = c.create_unicode_buffer(subprocess.list2cmdline(cmd))
            if not api(
                cmd[0],
                argv,
                None,
                None,
                False,
                0x4 | 0x400 | 0x08000000,
                entorno,
                str(cwd),
                c.byref(si),
                identidad,
                especificacion,
                len(especificacion),
                c.byref(info),
            ):
                raise c.WinError(c.get_last_error())
        p = Proceso(info, cmd, lecturas[0], lecturas[1] if len(lecturas) > 1 else None)
        nombre_job = "Local\\goncloud-motor-" + uuid.uuid4().hex
        try:
            job = Recursos(nombre_job, escritorio, p, control, entorno_seguro)
            job.asignar(p)
        except BaseException:
            p.kill()
            p.wait(5)
            if job:
                job.cerrar()
            raise
        _jobs[p.pid] = job
        atexit.register(job.cerrar)

        def liberar():
            if p.k.ResumeThread(p._thread) == 0xFFFFFFFF:
                job.cerrar()
                raise c.WinError(c.get_last_error())

        p._motor_liberar = liberar
        return ProcesoCreado(p, "appcontainer-suspendido", nombre_job)
    except BaseException:
        if p:
            p.kill()
            p.wait(5)
        if job:
            job.cerrar()
        if entorno_seguro:
            entorno_seguro.cerrar()
        escritorio.cerrar()
        limpiar(control)
        for flujo in lecturas:
            flujo.close()
        raise
    finally:
        os.close(nulo)
        for fd in extremos:
            os.close(fd)
