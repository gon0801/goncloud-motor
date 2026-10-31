"""Mide, dentro de la jaula real de Windows, qué directorios de objetos puede abrir o crear un AppContainer.

msys-2.0.dll llama a NtCreateDirectoryObject sobre `\\BaseNamedObjects\\msys-2.0S5-<clave>` con
OBJ_OPENIF y, desde la sesión 1 o más, también sobre `\\Sessions\\BNOLINKS\\<n>\\msys-2.0S5-<clave>`.
Este ensayo hace las mismas llamadas con nombres propios y con el espacio de nombres del propio
AppContainer, para que la causa se lea en la PC y no se suponga. Lánzalo como lo lanza el supervisor
(tarea programada): `jaula_windows.crear` falla con WinError 5 desde una sesión SSH.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.encargos.aislamiento import aislar, lanzar_en_jaula

CODIGO = r"""
import ctypes as c, json, os, sys, uuid
from ctypes import wintypes as w

nt = c.WinDLL("ntdll")
k = c.WinDLL("kernel32", use_last_error=True)


class Texto(c.Structure):
    _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", w.LPWSTR)]


class Atributos(c.Structure):
    _fields_ = [
        ("Length", w.ULONG),
        ("RootDirectory", w.HANDLE),
        ("ObjectName", c.POINTER(Texto)),
        ("Attributes", w.ULONG),
        ("SecurityDescriptor", c.c_void_p),
        ("SecurityQualityOfService", c.c_void_p),
    ]


for nombre in ("NtOpenDirectoryObject", "NtCreateDirectoryObject"):
    fn = getattr(nt, nombre)
    fn.argtypes, fn.restype = [c.POINTER(w.HANDLE), w.ULONG, c.POINTER(Atributos)], c.c_long
nt.NtClose.argtypes, nt.NtClose.restype = [w.HANDLE], c.c_long
CASO_INSENSIBLE, ABRIR_SI_EXISTE = 0x40, 0x80
CONSULTAR, RECORRER, CREAR_OBJETO, CREAR_SUBDIRECTORIO = 0x1, 0x2, 0x4, 0x8


def llamar(operacion, ruta, acceso, banderas):
    buffer = c.create_unicode_buffer(ruta)
    texto = Texto(len(ruta) * 2, len(ruta) * 2 + 2, c.cast(buffer, w.LPWSTR))
    atributos = Atributos(c.sizeof(Atributos), None, c.pointer(texto), banderas, None, None)
    asa = w.HANDLE()
    estado = getattr(nt, operacion)(c.byref(asa), acceso, c.byref(atributos))
    if estado >= 0:
        nt.NtClose(asa)
    return "0x%08X" % (estado & 0xFFFFFFFF)


sesion = w.DWORD()
k.ProcessIdToSessionId(os.getpid(), c.byref(sesion))
propio = ""
try:
    ruta = c.create_unicode_buffer(1024)
    largo = w.ULONG()
    if k.GetAppContainerNamedObjectPath(None, None, len(ruta), ruta, c.byref(largo)):
        propio = ruta.value
except (AttributeError, OSError):
    pass
marca = "motor-sonda-" + uuid.uuid4().hex[:12]
mascara = CONSULTAR | RECORRER | CREAR_OBJETO | CREAR_SUBDIRECTORIO
pruebas = [
    ("abrir", "NtOpenDirectoryObject", "\\BaseNamedObjects", CONSULTAR | RECORRER, 0),
    ("recorrer", "NtOpenDirectoryObject", "\\BaseNamedObjects", RECORRER, 0),
    ("crear", "NtCreateDirectoryObject", "\\BaseNamedObjects\\" + marca, mascara, CASO_INSENSIBLE | ABRIR_SI_EXISTE),
    ("abrir", "NtOpenDirectoryObject", "\\Sessions\\BNOLINKS\\%d" % sesion.value, CONSULTAR | RECORRER, 0),
    ("crear", "NtCreateDirectoryObject", "\\Sessions\\BNOLINKS\\%d\\%s" % (sesion.value, marca), mascara, CASO_INSENSIBLE | ABRIR_SI_EXISTE),
]
if len(sys.argv) > 1 and sys.argv[1]:
    clave = "msys-2.0S5-" + sys.argv[1]
    pruebas.append(("abrir el de msys", "NtOpenDirectoryObject", "\\BaseNamedObjects\\" + clave, mascara, CASO_INSENSIBLE))
    pruebas.append(("abrir o crear el de msys", "NtCreateDirectoryObject", "\\BaseNamedObjects\\" + clave, mascara, CASO_INSENSIBLE | ABRIR_SI_EXISTE))
if propio:
    pruebas.append(("crear en el espacio del AppContainer", "NtCreateDirectoryObject", propio + "\\" + marca, mascara, CASO_INSENSIBLE | ABRIR_SI_EXISTE))
resultado = {"sesion": sesion.value, "espacio_del_appcontainer": propio, "pruebas": []}
for etiqueta, operacion, ruta, acceso, banderas in pruebas:
    resultado["pruebas"].append([etiqueta, ruta, llamar(operacion, ruta, acceso, banderas)])
print(json.dumps(resultado), flush=True)
"""

EXITO = "0x00000000"


def interpretar(medida: dict) -> list[str]:
    por_ruta = {fila[1]: (fila[0], fila[2]) for fila in medida["pruebas"]}
    global_ = [e for r, (_, e) in por_ruta.items() if r.startswith("\\BaseNamedObjects\\motor-sonda")]
    propio = [e for r, (_, e) in por_ruta.items() if r.startswith(medida["espacio_del_appcontainer"] + "\\")] if medida["espacio_del_appcontainer"] else []
    notas = []
    if global_ and global_[0] != EXITO and propio and propio[0] == EXITO:
        notas.append(
            "Confirmado: el AppContainer no crea en \\BaseNamedObjects y sí en su propio espacio; "
            "msys-2.0.dll solo sabe lo primero."
        )
    elif global_ and global_[0] == EXITO:
        notas.append(
            "El AppContainer SÍ crea en \\BaseNamedObjects: la causa no es el espacio de nombres; "
            "busca el siguiente objeto que msys cree (sección shared.5, pipe de señales)."
        )
    else:
        notas.append("Sin conclusión: falta la medida del espacio propio o de \\BaseNamedObjects.")
    return notas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clave", default="", help="clave de msys-2.0S5-<clave> del error del agente")
    args = ap.parse_args()
    if os.name != "nt":
        ap.error("este instrumento mide la jaula nativa de Windows")
    fallos = 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ident = "enc_objetos" + uuid.uuid4().hex[:8]
        espacio = base / "espacio" / ident
        runtime = base / "runtime" / ident
        espacio.mkdir(parents=True)
        runtime.mkdir(parents=True)
        for nombre, opciones in (
            ("sonda", {}),
            ("agente claude", {"suscripcion": True, "agente": "claude"}),
        ):
            comando = aislar(
                [sys.executable, "-c", CODIGO, args.clave], espacio, runtime, **opciones
            )
            rc, salida = lanzar_en_jaula(comando, espacio, dict(comando.entorno), plazo=60)
            try:
                medida = json.loads(salida.strip().splitlines()[-1])
            except (ValueError, IndexError):
                fallos += 1
                print(f"- {nombre}: FALLA rc={rc} salida={' '.join(salida.split())[:300]!r}")
                continue
            print(f"- {nombre}: sesion={medida['sesion']} espacio_propio={medida['espacio_del_appcontainer']!r}")
            for etiqueta, ruta, estado in medida["pruebas"]:
                print(f"    {etiqueta}: {ruta} -> {estado}")
            for nota in interpretar(medida):
                print(f"    {nota}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
