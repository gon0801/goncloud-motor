"""Pliega el registro de encargos de esta máquina con el código de un commit candidato.

Lo manda ops/compuerta-plegado.sh y corre en la máquina: `python -c ... <arbol> <sha>`,
con `<arbol>` el clon de git del supervisor. El candidato sale a un temporal con un clon
compartido de ese árbol, que no le escribe nada: ni su checkout ni sus refs se mueven.
tools/plegado.py del candidato corre con el intérprete del supervisor y abre el registro
en solo lectura. El registro no sale de la máquina.

Imprime una línea `resultado`, que decide la compuerta:

    resultado pliega <encargos>
    resultado no_pliega <encargos> <fallan>
    resultado sin_registro
    resultado no_pude <causa>

una línea `comparar <resumen>` contra la versión instalada, que solo informa, y una
`aviso` si no pudo borrar su temporal. Ninguna lleva identificadores de encargos ni
valores del registro.

Solo stdlib y Python 3.9: el python3 de ssh en la Mini es el del sistema.
"""

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time

PREFIJO = "motor-compuerta-"
VIEJO_S = 3600
TOPE_PLEGADO_S = 900
MAX_CLAVES = 20


class NoPude(Exception):
    pass


def existe(ruta, que):
    """False solo si ahí no hay nada. Un enlace roto o un error de lectura es NoPude."""
    try:
        os.stat(str(ruta))
        return True
    except FileNotFoundError:
        if not any(os.path.islink(str(r)) for r in (ruta, ruta.parent)):
            return False
        causa = "hay un enlace roto en el camino"
    except OSError as exc:
        causa = type(exc).__name__
    raise NoPude("no pude leer %s: %s" % (que, causa))


def servicio(home):
    """El intérprete y la base de encargos con que arranca el supervisor.

    En Mac y Mini salen del LaunchAgent. En la PC el vigilante lanza un
    supervisor.ps1 que el repo no genera: ahí quedan el intérprete de esta
    sesión y la base fija. La base fija vale solo si el plist no existe.
    """
    py, base = sys.executable, home / ".motor-encargos"
    plist = home / "Library" / "LaunchAgents" / "ai.goncloud.motor-encargos.plist"
    if os.name == "nt" or not existe(plist, "el plist del LaunchAgent"):
        return py, base
    import plistlib

    try:
        datos = plistlib.loads(plist.read_bytes())
    except Exception as exc:
        raise NoPude("el plist del LaunchAgent no se puede leer: " + type(exc).__name__) from None
    argumentos = datos.get("ProgramArguments") if isinstance(datos, dict) else None
    if not isinstance(argumentos, list) or not all(isinstance(a, str) for a in argumentos):
        raise NoPude("el plist del LaunchAgent no trae ProgramArguments como lista de textos")
    if argumentos and pathlib.Path(argumentos[0]).exists():
        py = argumentos[0]
    # El supervisor exige --base y argparse se queda con la última, en cualquiera de las dos formas.
    texto = ""
    for i, argumento in enumerate(argumentos):
        if i and argumentos[i - 1] == "--base":
            texto = argumento
        elif argumento.startswith("--base="):
            texto = argumento[len("--base="):]
    if not texto or texto.startswith("-"):
        raise NoPude("el LaunchAgent no dice con qué --base arranca el supervisor")
    base = pathlib.Path(texto)
    if not base.is_absolute():
        # launchd la resuelve contra el WorkingDirectory; ssh corre desde el home.
        directorio = datos.get("WorkingDirectory")
        if not isinstance(directorio, str) or not directorio:
            raise NoPude("el --base del LaunchAgent es relativo y el plist no trae WorkingDirectory")
        base = pathlib.Path(directorio) / base
    return py, base


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=300)


def _quita_solo_lectura(funcion, ruta, _exc):
    # Git deja de solo lectura sus objetos en Windows y rmtree no los borra sin esto.
    os.chmod(ruta, stat.S_IWRITE)
    funcion(ruta)


def borra(ruta):
    shutil.rmtree(str(ruta), onerror=_quita_solo_lectura)


def barre_viejos():
    """Temporales de una corrida que murió antes de su `finally`, como un ssh cortado."""
    for p in pathlib.Path(tempfile.gettempdir()).glob(PREFIJO + "*"):
        try:
            if time.time() - p.stat().st_mtime > VIEJO_S:
                borra(p)
        except OSError:
            pass


def saca_candidato(arbol, sha, destino):
    """El clon compartido lee los objetos del árbol del servicio y no le escribe nada.

    Si el candidato aún no está ahí, lo trae el temporal desde el origin del servicio.
    """
    if git("clone", "-q", "--shared", "--no-checkout", arbol, str(destino)).returncode != 0:
        raise NoPude("git no pudo clonar el árbol del servicio a un temporal")
    if git("-C", str(destino), "cat-file", "-e", sha + "^{commit}").returncode != 0:
        origen = git("-C", arbol, "remote", "get-url", "origin").stdout.strip()
        if not origen or git("-C", str(destino), "fetch", "-q", origen,
                             "+refs/heads/*:refs/remotes/candidato/*").returncode != 0:
            raise NoPude("git no pudo traer el commit candidato del origin del servicio")
    if git("-C", str(destino), "checkout", "-q", "--detach", sha).returncode != 0:
        raise NoPude("el commit candidato no está en el origin del servicio")
    if not (destino / "tools" / "plegado.py").is_file():
        raise NoPude("el commit candidato no trae tools/plegado.py")


def plegado(py, candidato, *args):
    try:
        hecho = subprocess.run(
            # Sin -X utf8, en Windows la salida a un tubo va en cp1252 y un carácter
            # fuera de esa tabla tumba a plegado.py.
            [py, "-X", "utf8", "-B", str(candidato / "tools" / "plegado.py"), *args],
            cwd=str(candidato), capture_output=True, encoding="utf-8", errors="replace",
            timeout=TOPE_PLEGADO_S,
        )
    except subprocess.TimeoutExpired:
        raise NoPude("tools/plegado.py no terminó en %d s" % TOPE_PLEGADO_S) from None
    try:
        registro = json.loads(hecho.stdout)["registros"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        registro = None
    if hecho.returncode in (0, 1) and isinstance(registro, dict):
        return registro
    # Solo los avisos propios de plegado.py: un traceback del reductor puede citar datos.
    primera = (hecho.stderr or "").strip().splitlines()[:1]
    if primera and primera[0].startswith("plegado: "):
        raise NoPude("tools/plegado.py: " + primera[0][len("plegado: "):].rstrip(":"))
    raise NoPude("tools/plegado.py salió con %d" % hecho.returncode)


def claves(rutas):
    if not rutas:
        return "ninguna"
    sobran = len(rutas) - MAX_CLAVES
    texto = ", ".join(rutas[:MAX_CLAVES])
    return texto + (" y %d más" % sobran if sobran > 0 else "")


def resumen_comparar(py, candidato, instalado, sha, registro):
    if not instalado:
        return "no sé qué versión está instalada; no comparo"
    if instalado == sha:
        return "la versión instalada ya es la candidata; no comparo"
    try:
        r = plegado(py, candidato, "comparar", "--json", "--base", instalado, str(registro))
    except NoPude as exc:
        return "no pude comparar con %s: %s" % (instalado[:12], exc)
    forma = r.get("forma") or []
    aparecen = sorted(f["ruta"] for f in forma if f.get("solo_en") == "arbol")
    desaparecen = sorted(f["ruta"] for f in forma if f.get("solo_en") == "base")
    return "contra %s cambian de valor %d de %d encargos; claves que aparecen: %s; claves que desaparecen: %s" % (
        instalado[:12], len(r.get("diferencias") or []), r.get("encargos", 0),
        claves(aparecen), claves(desaparecen),
    )


def compuerta(arbol, sha):
    py, base = servicio(pathlib.Path.home())
    registro = base / "registro.sqlite"
    if not existe(registro, "el registro de encargos"):
        return ["resultado sin_registro", "comparar sin registro; no comparo"]
    instalado = git("-C", arbol, "rev-parse", "HEAD").stdout.strip()
    barre_viejos()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=PREFIJO))
    try:
        candidato = tmp / "arbol"
        saca_candidato(arbol, sha, candidato)
        r = plegado(py, candidato, "comprobar", "--json", str(registro))
        encargos, fallan = int(r["encargos"]), len(r["fallos"])
        if fallan:
            return ["resultado no_pliega %d %d" % (encargos, fallan), "comparar no comparo: no pliega"]
        return [
            "resultado pliega %d" % encargos,
            "comparar " + resumen_comparar(py, candidato, instalado, sha, registro),
        ]
    finally:
        try:
            borra(tmp)
        except OSError:
            sys.stdout.write("aviso no pude borrar el temporal %s\n" % tmp)


def main():
    arbol, sha = sys.argv[1], sys.argv[2]
    try:
        lineas = compuerta(arbol, sha)
    except NoPude as exc:
        lineas = ["resultado no_pude " + str(exc)]
    except Exception as exc:
        lineas = ["resultado no_pude " + type(exc).__name__]
    sys.stdout.write("\n".join(lineas) + "\n")


if __name__ == "__main__":
    main()
