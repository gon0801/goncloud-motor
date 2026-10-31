"""Mide, dentro de la jaula real de Windows, por qué msys no puede crear o abrir su objeto, y si una concesión acotada alcanza.

msys-2.0.dll llama a NtCreateDirectoryObject sobre `\\BaseNamedObjects\\msys-2.0S5-<clave>` con
OBJ_OPENIF y, desde la sesión 1 o más, también sobre `\\Sessions\\BNOLINKS\\<n>\\msys-2.0S5-<clave>`.
Después crea, dentro de ese directorio, secciones, pipes y enlaces con una DACL que solo nombra a
Everyone y al usuario.

Sin opciones el ensayo mide, desde dentro de la jaula:
  1. qué directorios de objetos puede abrir o crear el AppContainer;
  2. si el AppContainer puede reabrir un objeto que creó él mismo con una DACL solo Everyone (como
     msys) y con otras que además nombran a ALL APPLICATION PACKAGES y a ALL RESTRICTED APPLICATION
     PACKAGES (el que usa un LPAC) o al SID del contenedor;
  3. si el token es AppContainer y si es LPAC, con su SID.

Con `--concesion --clave <clave>` hace, además, el experimento que valida o descarta el arreglo
acotado: el lanzador, fuera de la jaula, crea y mantiene abiertos los dos directorios de msys con
una DACL que nombra a ALL APPLICATION PACKAGES y a ALL RESTRICTED APPLICATION PACKAGES, con
etiqueta de integridad baja, y dentro de la jaula
corre bash, sh y git push para ver hasta dónde llega msys. Mientras corre, un msys sin jaula del
mismo usuario que abra esos nombres se une a ellos. Cierra los procesos msys antes de lanzarlo.

Lánzalo como lo lanza el supervisor (tarea programada): `jaula_windows.crear` falla con WinError 5
desde una sesión SSH.
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
sys.path.insert(1, str(Path(__file__).resolve().parent))

from ensayo_shell_jaula import PUSH

from motor.encargos.aislamiento import (
    _ejecutar_en_jaula,
    _herramientas_git,
    aislar,
    lanzar_en_jaula,
)

NT = r"""
import ctypes as c, os, uuid
from ctypes import wintypes as w

nt = c.WinDLL("ntdll")
k = c.WinDLL("kernel32", use_last_error=True)
adv = c.WinDLL("advapi32", use_last_error=True)


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
adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)
]
adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
k.LocalFree.argtypes, k.LocalFree.restype = [c.c_void_p], c.c_void_p
CASO_INSENSIBLE, ABRIR_SI_EXISTE = 0x40, 0x80
CONSULTAR, RECORRER, CREAR_OBJETO, CREAR_SUBDIRECTORIO = 0x1, 0x2, 0x4, 0x8
MASCARA = CONSULTAR | RECORRER | CREAR_OBJETO | CREAR_SUBDIRECTORIO


def llamar(operacion, ruta, acceso, banderas, sddl=None, conservar=False):
    descriptor = c.c_void_p()
    if sddl and not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, c.byref(descriptor), None
    ):
        raise c.WinError(c.get_last_error())
    buffer = c.create_unicode_buffer(ruta)
    texto = Texto(len(ruta) * 2, len(ruta) * 2 + 2, c.cast(buffer, w.LPWSTR))
    atributos = Atributos(
        c.sizeof(Atributos), None, c.pointer(texto), banderas, descriptor.value, None
    )
    asa = w.HANDLE()
    estado = getattr(nt, operacion)(c.byref(asa), acceso, c.byref(atributos))
    if descriptor.value:
        k.LocalFree(descriptor)
    codigo = "0x%08X" % (estado & 0xFFFFFFFF)
    if estado >= 0 and conservar:
        return codigo, asa
    if estado >= 0:
        nt.NtClose(asa)
    return codigo, None


def sesion_actual():
    sesion = w.DWORD()
    k.ProcessIdToSessionId(os.getpid(), c.byref(sesion))
    return sesion.value


def token_actual():
    # TokenIsAppContainer = 29, TokenAppContainerSid = 31, TokenIsLessPrivilegedAppContainer = 46.
    k.GetCurrentProcess.restype = w.HANDLE
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    adv.GetTokenInformation.restype = w.BOOL
    adv.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    datos = {"appcontainer": None, "lpac": None, "sid": ""}
    asa = w.HANDLE()
    if not adv.OpenProcessToken(k.GetCurrentProcess(), 0x8, c.byref(asa)):
        return datos

    def entero(clase):
        valor, tamano = w.DWORD(), w.DWORD()
        if adv.GetTokenInformation(asa, clase, c.byref(valor), c.sizeof(valor), c.byref(tamano)):
            return bool(valor.value)
        return None

    datos["appcontainer"], datos["lpac"] = entero(29), entero(46)
    tamano = w.DWORD()
    adv.GetTokenInformation(asa, 31, None, 0, c.byref(tamano))
    if tamano.value:
        buffer = c.create_string_buffer(tamano.value)
        if adv.GetTokenInformation(asa, 31, buffer, tamano.value, c.byref(tamano)):
            sid, texto = c.cast(buffer, c.POINTER(c.c_void_p))[0], w.LPWSTR()
            if sid and adv.ConvertSidToStringSidW(sid, c.byref(texto)):
                datos["sid"] = texto.value
                k.LocalFree(c.cast(texto, c.c_void_p))
    nt.NtClose(asa)
    return datos
"""

# GetAppContainerNamedObjectPath devuelve `AppContainerNamedObjects\<SID>`, sin el prefijo
# `\Sessions\<n>\`. NtCreateDirectoryObject sin RootDirectory la rechaza con 0xC000003B (medido en la
# PC el 30-oct), así que se completa a la ruta NT absoluta.
RUTA_PROPIA = r"""
def ruta_nt_propia(ruta, sesion):
    if not ruta or ruta.startswith("\\"):
        return ruta
    return "\\Sessions\\%d\\%s" % (sesion, ruta)
"""

CODIGO = (
    NT
    + RUTA_PROPIA
    + r"""
import json, sys

sesion = sesion_actual()
propio = ""
try:
    ruta = c.create_unicode_buffer(1024)
    largo = w.ULONG()
    if k.GetAppContainerNamedObjectPath(None, None, len(ruta), ruta, c.byref(largo)):
        propio = ruta_nt_propia(ruta.value, sesion)
except (AttributeError, OSError):
    pass
marca = "motor-sonda-" + uuid.uuid4().hex[:12]
EXISTE = CASO_INSENSIBLE | ABRIR_SI_EXISTE
pruebas = [
    ("abrir", "NtOpenDirectoryObject", "\\BaseNamedObjects", CONSULTAR | RECORRER, 0),
    ("recorrer", "NtOpenDirectoryObject", "\\BaseNamedObjects", RECORRER, 0),
    ("crear", "NtCreateDirectoryObject", "\\BaseNamedObjects\\" + marca, MASCARA, EXISTE),
    ("abrir", "NtOpenDirectoryObject", "\\Sessions\\BNOLINKS\\%d" % sesion, CONSULTAR | RECORRER, 0),
    ("crear", "NtCreateDirectoryObject", "\\Sessions\\BNOLINKS\\%d\\%s" % (sesion, marca), MASCARA, EXISTE),
]
if len(sys.argv) > 1 and sys.argv[1]:
    clave = "msys-2.0S5-" + sys.argv[1]
    pruebas.append(("abrir el de msys", "NtOpenDirectoryObject", "\\BaseNamedObjects\\" + clave, MASCARA, CASO_INSENSIBLE))
    pruebas.append(("abrir o crear el de msys", "NtCreateDirectoryObject", "\\BaseNamedObjects\\" + clave, MASCARA, EXISTE))
    pruebas.append(("abrir el de msys en la sesion", "NtOpenDirectoryObject", "\\Sessions\\BNOLINKS\\%d\\%s" % (sesion, clave), MASCARA, CASO_INSENSIBLE))
if propio:
    pruebas.append(("crear en el espacio del AppContainer", "NtCreateDirectoryObject", propio + "\\" + marca, MASCARA, EXISTE))
token = token_actual()
resultado = {"sesion": sesion, "espacio_del_appcontainer": propio, "token": token, "pruebas": []}
for etiqueta, operacion, ruta, acceso, banderas in pruebas:
    resultado["pruebas"].append([etiqueta, ruta, llamar(operacion, ruta, acceso, banderas)[0]])
if propio:
    reaperturas = [
        ("reabrir lo propio, DACL solo Everyone (como msys)", "-w", "D:(A;;0xF000F;;;WD)"),
        (
            "reabrir lo propio, DACL Everyone y los dos paquetes (AC y S-1-15-2-2)",
            "-aap",
            "D:(A;;0xF000F;;;WD)(A;;0xF000F;;;AC)(A;;0xF000F;;;S-1-15-2-2)",
        ),
    ]
    if token["sid"]:
        reaperturas.append(
            (
                "reabrir lo propio, DACL Everyone y el SID del contenedor",
                "-sid",
                "D:(A;;0xF000F;;;WD)(A;;0xF000F;;;" + token["sid"] + ")",
            )
        )
    for etiqueta, sufijo, sddl in reaperturas:
        ruta = propio + "\\" + marca + sufijo
        creado, asa = llamar("NtCreateDirectoryObject", ruta, MASCARA, EXISTE, sddl, True)
        if asa is None:
            estado = "no se pudo crear: " + creado
        else:
            estado = llamar("NtOpenDirectoryObject", ruta, CONSULTAR | RECORRER, CASO_INSENSIBLE)[0]
            nt.NtClose(asa)
        resultado["pruebas"].append([etiqueta, ruta, estado])
print(json.dumps(resultado), flush=True)
"""
)

EXITO = "0x00000000"
SOLO_EVERYONE = "reabrir lo propio, DACL solo Everyone (como msys)"
CON_PAQUETES = "reabrir lo propio, DACL Everyone y los dos paquetes (AC y S-1-15-2-2)"
CON_SID = "reabrir lo propio, DACL Everyone y el SID del contenedor"
# AC es ALL APPLICATION PACKAGES; S-1-15-2-2 es ALL RESTRICTED APPLICATION PACKAGES, la que usa un
# token LPAC. La jaula ya concede a las dos en su estación de ventanas (jaula_windows.Escritorio).
SDDL_CONCESION = (
    "D:(A;;0xF000F;;;WD)(A;;0xF000F;;;AC)(A;;0xF000F;;;S-1-15-2-2)(A;;0xF000F;;;OW)"
    "S:(ML;;NW;;;LW)"
)


def interpretar(medida: dict) -> list[str]:
    estados = {fila[0]: fila[2] for fila in medida["pruebas"]}
    propio = medida["espacio_del_appcontainer"]
    global_ = [
        fila[2]
        for fila in medida["pruebas"]
        if fila[1].startswith("\\BaseNamedObjects\\motor-sonda")
    ]
    en_propio = [
        fila[2] for fila in medida["pruebas"] if propio and fila[1].startswith(propio + "\\motor-sonda") and fila[0].startswith("crear")
    ]
    notas = []
    if global_ and global_[0] != EXITO and en_propio and en_propio[0] == EXITO:
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
    reabre_con_concesion = any(estados.get(x) == EXITO for x in (CON_PAQUETES, CON_SID))
    if SOLO_EVERYONE in estados and (CON_PAQUETES in estados or CON_SID in estados):
        if estados[SOLO_EVERYONE] != EXITO and reabre_con_concesion:
            notas.append(
                "Una concesión acotada no alcanza: el contenedor no reabre ni lo que creó él con una "
                "DACL solo Everyone, que es la que msys da a sus secciones, pipes y enlaces. "
                "Conceder el directorio deja a msys fallar en el objeto siguiente."
            )
        elif estados[SOLO_EVERYONE] == EXITO:
            notas.append(
                "El contenedor sí reabre lo propio con DACL solo Everyone: la concesión acotada "
                "del directorio puede bastar; corre --concesion para verlo con msys."
            )
        else:
            notas.append("Sin conclusión sobre la DACL de los objetos propios: " + json.dumps(estados))
    token = medida.get("token") or {}
    if token.get("lpac"):
        notas.append(
            "El token es LPAC: ALL APPLICATION PACKAGES no le da acceso. La concesión debe nombrar a "
            "S-1-15-2-2 o al SID del contenedor (" + (token.get("sid") or "sin SID") + ")."
        )
    elif token.get("lpac") is None:
        notas.append("No se pudo leer si el token es LPAC: por eso la concesión nombra a los dos paquetes.")
    return notas


def _concesion(clave: str) -> tuple[list, list[str]]:
    """Crea fuera de la jaula los dos directorios de msys con una DACL que nombra al paquete."""
    espacio_nt: dict = {}
    exec(NT, espacio_nt)
    sesion = espacio_nt["sesion_actual"]()
    abiertos, lineas = [], []
    for ruta in (
        "\\BaseNamedObjects\\msys-2.0S5-" + clave,
        "\\Sessions\\BNOLINKS\\%d\\msys-2.0S5-%s" % (sesion, clave),
    ):
        codigo, asa = espacio_nt["llamar"](
            "NtCreateDirectoryObject",
            ruta,
            espacio_nt["MASCARA"],
            espacio_nt["CASO_INSENSIBLE"] | espacio_nt["ABRIR_SI_EXISTE"],
            SDDL_CONCESION,
            True,
        )
        if asa is not None:
            abiertos.append((espacio_nt, asa))
        nota = ""
        if codigo == "0x40000000":
            nota = " (ya existía con otro descriptor: cierra los procesos msys y repite)"
        lineas.append(f"    crear fuera de la jaula {ruta} -> {codigo}{nota}")
    return abiertos, lineas


def _shell(comando_base: dict, espacio: Path, runtime: Path, herramientas: dict) -> int:
    fallos = 0
    for caso, cmd, cwd in (
        ("bash de Git", [herramientas["MOTOR_SONDA_SHELL"], "-c", "echo ok"], espacio),
        ("sh de Git", [herramientas["MOTOR_SONDA_SH"], "-c", "echo ok"], espacio),
        (
            "git push a un bare local",
            [herramientas["MOTOR_SONDA_SHELL"], "-c", PUSH],
            runtime / "tmp",
        ),
    ):
        comando = aislar(cmd, espacio, runtime, **comando_base)
        rc, salida, error = _ejecutar_en_jaula(comando, cwd, dict(comando.entorno), plazo=60)
        bien = rc == 0 and salida.split()[-1:] == ["ok"]
        fallos += not bien
        # El texto de msys sale por stderr: sin él no se ve en qué objeto se detiene.
        print(
            f"    {caso}: {'ok' if bien else 'FALLA'} rc={rc} salida={' '.join(salida.split())[:300]!r}"
            f" error={' '.join(error.split())[:300]!r}"
        )
    return fallos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clave", default="", help="clave de msys-2.0S5-<clave> del error del agente")
    ap.add_argument(
        "--concesion",
        action="store_true",
        help="experimento: crear fuera de la jaula los directorios de msys con una DACL para el paquete",
    )
    args = ap.parse_args()
    if os.name != "nt":
        ap.error("este instrumento mide la jaula nativa de Windows")
    if args.concesion and not args.clave:
        ap.error("--concesion necesita --clave")
    fallos = 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ident = "enc_objetos" + uuid.uuid4().hex[:8]
        espacio = base / "espacio" / ident
        runtime = base / "runtime" / ident
        espacio.mkdir(parents=True)
        runtime.mkdir(parents=True)
        variantes = [("sonda", {}), ("agente claude", {"suscripcion": True, "agente": "claude"})]
        for nombre, opciones in variantes:
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
            print(f"    token: {json.dumps(medida.get('token'))}")
            for etiqueta, ruta, estado in medida["pruebas"]:
                print(f"    {etiqueta}: {ruta} -> {estado}")
            for nota in interpretar(medida):
                print(f"    {nota}")
        if args.concesion:
            print("- experimento de concesión acotada (agente claude)")
            abiertos, lineas = _concesion(args.clave)
            try:
                print("\n".join(lineas))
                herramientas = _herramientas_git(dict(os.environ))
                fallos += _shell({"suscripcion": True, "agente": "claude"}, espacio, runtime, herramientas)
                print(
                    "    Lectura: si bash, sh y git push dan ok, la concesión acotada basta y se puede llevar "
                    "al motor. Si falla con otro texto (por ejemplo 'couldn't create signal pipe' o "
                    "'CreateFileMapping'), la concesión del directorio avanza a msys hasta el siguiente "
                    "objeto y no alcanza."
                )
            finally:
                for espacio_nt, asa in abiertos:
                    espacio_nt["nt"].NtClose(asa)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
