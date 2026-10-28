"""Proceso con políticas PSEC 1.1 y enumeración separada de lectura de datos."""

from __future__ import annotations

import ctypes as c
import os
import subprocess
from ctypes import wintypes as w
from pathlib import Path


def enumeracion_para(limites: dict) -> list[str]:
    completas = [Path(ruta) for ruta in (*limites["lectura"], *limites["escritura"])]
    candidatas = {padre for ruta in completas for padre in ruta.parents}
    candidatas.add(Path(os.environ["PROGRAMDATA"]))
    return sorted(
        str(ruta)
        for ruta in candidatas
        if not any(ruta.is_relative_to(completa) for completa in completas)
    )


def disponible() -> bool:
    try:
        api = c.WinDLL("processmodel.dll", use_last_error=True)
        _ = api.CreateProcessSecurityEnvironment, api.CloseProcessSecurityEnvironment
        query = api.QueryProcessSecurityEnvironmentSupport
        query.argtypes, query.restype = [c.POINTER(c.c_uint64)], c.c_long
        flags = c.c_uint64()
        return query(c.byref(flags)) >= 0 and bool(flags.value & 4)
    except (OSError, AttributeError):
        return False


class Entorno:
    def __init__(self, especificacion: bytes):
        if not disponible():
            raise RuntimeError("Windows no admite enumeración aislada PSEC 1.1")
        self.api = c.WinDLL("processmodel.dll", use_last_error=True)
        crear = self.api.CreateProcessSecurityEnvironment
        crear.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.HANDLE)]
        crear.restype = c.c_long
        self.close = self.api.CloseProcessSecurityEnvironment
        self.close.argtypes, self.close.restype = [w.HANDLE], None
        self.handle = w.HANDLE()
        datos = c.create_string_buffer(especificacion)
        resultado = crear(datos, len(especificacion), 0, c.byref(self.handle))
        if resultado < 0:
            raise OSError(
                f"CreateProcessSecurityEnvironment: HRESULT {resultado & 0xFFFFFFFF:#x}"
            )

    def cerrar(self):
        if self.handle:
            self.close(self.handle)
            self.handle = w.HANDLE()

    def crear(self, cmd, cwd, env, inicio, informacion):
        from .jaula_windows import Informacion, Inicio, _api

        class InicioExtendido(c.Structure):
            _fields_ = [("inicio", Inicio), ("atributos", c.c_void_p)]

        kernel = _api()
        inicializar = kernel.InitializeProcThreadAttributeList
        inicializar.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)]
        inicializar.restype = w.BOOL
        actualizar = kernel.UpdateProcThreadAttribute
        actualizar.argtypes = [
            c.c_void_p,
            w.DWORD,
            c.c_size_t,
            c.c_void_p,
            c.c_size_t,
            c.c_void_p,
            c.c_void_p,
        ]
        actualizar.restype = w.BOOL
        borrar = kernel.DeleteProcThreadAttributeList
        borrar.argtypes, borrar.restype = [c.c_void_p], None
        crear = kernel.CreateProcessW
        crear.argtypes = [
            w.LPCWSTR,
            w.LPWSTR,
            c.c_void_p,
            c.c_void_p,
            w.BOOL,
            w.DWORD,
            c.c_void_p,
            w.LPCWSTR,
            c.POINTER(InicioExtendido),
            c.POINTER(Informacion),
        ]
        crear.restype = w.BOOL
        size = c.c_size_t()
        inicializar(None, 2, 0, c.byref(size))
        almacen = c.create_string_buffer(size.value)
        if not inicializar(almacen, 2, 0, c.byref(size)):
            raise c.WinError(c.get_last_error())
        try:
            # ProcThreadAttributeSecurityEnvironment = 35, con INPUT (0x20000).
            if not actualizar(
                almacen,
                0,
                0x20023,
                c.byref(self.handle),
                c.sizeof(self.handle),
                None,
                None,
            ):
                raise c.WinError(c.get_last_error())
            permitidos = tuple(
                dict.fromkeys((inicio.stdin, inicio.stdout, inicio.stderr))
            )
            handles = (w.HANDLE * len(permitidos))(*permitidos)
            if not actualizar(
                almacen, 0, 0x20002, handles, c.sizeof(handles), None, None
            ):
                raise c.WinError(c.get_last_error())
            si = InicioExtendido(inicio, c.cast(almacen, c.c_void_p))
            si.inicio.cb = c.sizeof(InicioExtendido)
            entorno = c.create_unicode_buffer(
                "\0".join(k + "=" + v for k, v in sorted(env.items())) + "\0\0"
            )
            argv = c.create_unicode_buffer(subprocess.list2cmdline(cmd))
            if not crear(
                cmd[0],
                argv,
                None,
                None,
                True,
                0x80000 | 0x400 | 0x4 | 0x08000000,
                entorno,
                str(cwd),
                c.byref(si),
                c.byref(informacion),
            ):
                raise c.WinError(c.get_last_error())
        finally:
            borrar(almacen)
