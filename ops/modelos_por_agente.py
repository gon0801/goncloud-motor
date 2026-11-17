"""Compara los modelos de cada agente con la tabla de la flota, por director.

ops/comprueba-despliegue.sh lo manda a cada máquina y lo corre con el python que haya: el del
sistema en la Mac y la Mini (3.9, sin tomllib) y el de motor-runtime en la PC (sin PyYAML). Solo
lee. Imprime nombres de agente, modelo y esfuerzo; nunca llaves, tokens ni rutas.
"""

import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Callable, NamedTuple, Optional

Paso = tuple[str, str]
"""(modelo, esfuerzo); el esfuerzo vacío es que la fuente no lo dice."""
Cadena = tuple[Paso, ...]
Agentes = list[tuple[str, Cadena]]


class Ilegible(Exception):
    """Un archivo que falta o no se pudo leer. El texto es la causa corta que se imprime."""


class Director(NamedTuple):
    lee: Callable[[Path], Agentes]
    pasos: Optional[int]
    """Cuántos pasos de la cadena de la tabla cuentan: None, todos; 1, el agente usa un modelo."""
    con_esfuerzo: bool


def _leer_json(ruta: Path, nombre: str) -> object:
    if not ruta.is_file():
        raise Ilegible("sin " + nombre)
    try:
        with open(ruta, encoding="utf-8-sig") as archivo:
            return json.load(archivo)
    except (OSError, ValueError):
        raise Ilegible(nombre + " ilegible") from None


def leer_tabla(ruta: Path) -> tuple[dict[str, Cadena], dict[str, str]]:
    """Los puestos y la herencia del manifiesto que escribe motor.flota.manifiesto.emitir.

    Cada valor es un literal JSON, así que no hace falta tomllib.
    """
    if not ruta.is_file():
        raise Ilegible("sin manifiesto")
    filas: list[dict] = []
    herencia: dict[str, str] = {}
    seccion = ""
    try:
        with open(ruta, encoding="utf-8-sig") as archivo:
            lineas = archivo.read().splitlines()
        for linea in lineas:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            if linea.startswith("["):
                seccion = linea
                if seccion == "[[puesto]]":
                    filas.append({})
                continue
            clave, _, valor = linea.partition("=")
            if seccion == "[[puesto]]":
                filas[-1][clave.strip()] = json.loads(valor)
            elif seccion == "[herencia]":
                herencia[clave.strip().lower()] = json.loads(valor)
        # zip(strict=) no existe en el python 3.9 de la Mini; emitir ya escribe listas parejas.
        puestos = {f["funcion"]: tuple(zip(f["modelo"], f["esfuerzo"])) for f in filas}  # noqa: B905
    except (OSError, ValueError, KeyError, TypeError):
        raise Ilegible("manifiesto ilegible") from None
    return puestos, herencia


def _lee_claw(home: Path) -> Agentes:
    doc = _leer_json(home / ".openclaw" / "openclaw.json", "openclaw.json")
    agentes = (doc.get("agents") if isinstance(doc, dict) else None) or {}

    def cadena(modelo) -> Cadena:
        if isinstance(modelo, str):
            modelo = {"primary": modelo}
        refs = [modelo.get("primary"), *(modelo.get("fallbacks") or [])]
        return tuple((ref, "") for ref in refs if isinstance(ref, str) and ref)

    vivos = []
    for agente, entrada in (agentes.get("entries") or {}).items():
        modelo = entrada.get("model") if isinstance(entrada, dict) else None
        # Una entrada sin modelo (los arneses de CLI) no elige modelo en claw.
        if modelo:
            vivos.append((agente, cadena(modelo)))
    defecto = (agentes.get("defaults") or {}).get("model")
    if defecto and "default" not in (agentes.get("entries") or {}):
        vivos.append(("default", cadena(defecto)))
    return vivos


# Corre con el python del venv de Hermes, el único con PyYAML. Saca solo los nombres de modelo y
# sus esfuerzos: config.yaml también guarda llaves de proveedores.
LEE_HERMES = r"""
import json, os, sys, yaml
base = sys.argv[1]
rutas = {"default": os.path.join(base, "config.yaml")}
perfiles = os.path.join(base, "profiles")
for nombre in sorted(os.listdir(perfiles)) if os.path.isdir(perfiles) else []:
    rutas[nombre] = os.path.join(perfiles, nombre, "config.yaml")
salida = {}
for nombre, ruta in rutas.items():
    if not os.path.isfile(ruta):
        continue
    with open(ruta, encoding="utf-8") as archivo:
        cfg = yaml.safe_load(archivo) or {}
    modelo = cfg.get("model")
    if isinstance(modelo, dict):
        modelo = modelo.get("default") or modelo.get("model")
    respaldos = [r.get("model") for r in cfg.get("fallback_providers") or [] if isinstance(r, dict)]
    esfuerzos = (cfg.get("agent") or {}).get("reasoning_overrides") or {}
    salida[nombre] = {
        "modelos": [m for m in [modelo] + respaldos if isinstance(m, str) and m],
        "esfuerzos": {m: e for m, e in esfuerzos.items() if isinstance(m, str) and isinstance(e, str)},
    }
print(json.dumps(salida))
"""


def _lee_hermes(home: Path) -> Agentes:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) / "hermes" if local else home / ".hermes"
    venv = base / "hermes-agent" / "venv"
    python = venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"
    if not python.is_file():
        raise Ilegible("sin el python de Hermes")
    try:
        hecho = subprocess.run(
            [str(python), "-", str(base)],
            input=LEE_HERMES,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Ilegible("Hermes no respondió") from None
    if hecho.returncode != 0:
        # La última línea de un error de YAML cita el texto del archivo: solo sale su tipo.
        ultima = (hecho.stderr or "").strip().splitlines()[-1:]
        tipo = ultima[0].partition(":")[0].rsplit(".", 1)[-1] if ultima else ""
        raise Ilegible("config.yaml ilegible" + (": " + tipo if tipo.isidentifier() else ""))
    try:
        perfiles = json.loads(hecho.stdout)
    except ValueError:
        raise Ilegible("Hermes no devolvió JSON") from None
    return [
        (perfil, tuple((m, datos["esfuerzos"].get(m, "")) for m in datos["modelos"]))
        for perfil, datos in perfiles.items()
    ]


def _un_modelo(modelo: object, esfuerzo: object) -> Cadena:
    if not isinstance(modelo, str) or not modelo:
        return ()
    return ((modelo, esfuerzo if isinstance(esfuerzo, str) else ""),)


def _lee_openbot(home: Path) -> Agentes:
    db = home / "Library" / "Application Support" / "OpenBot" / "openbot.db"
    if not db.is_file():
        raise Ilegible("sin openbot.db")
    try:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5)) as con:
            filas = con.execute("SELECT agent_json FROM projection_agents").fetchall()
    except sqlite3.Error:
        raise Ilegible("openbot.db ilegible") from None
    vivos = []
    for (crudo,) in filas:
        agente = json.loads(crudo)
        vivos.append(
            (str(agente["name"]), _un_modelo(agente.get("model"), agente.get("reasoningEffort")))
        )
    return vivos


def _lee_mausbot(home: Path) -> Agentes:
    bots = _leer_json(home / ".openmausbot" / "bots.json", "bots.json")
    if not isinstance(bots, list):
        raise Ilegible("bots.json ilegible")
    vivos = []
    for bot in bots:
        eleccion = bot.get("modelSelection") or {}
        esfuerzo = eleccion.get("effort") or eleccion.get("variant")
        vivos.append((str(bot["name"]), _un_modelo(eleccion.get("model"), esfuerzo)))
    return vivos


DIRECTORES = {
    "hermes": Director(_lee_hermes, None, True),
    "claw": Director(_lee_claw, None, False),
    "openbot": Director(_lee_openbot, 1, True),
    "mausbot": Director(_lee_mausbot, 1, True),
}


def _forma(cadena: Cadena, director: Director) -> Cadena:
    # El proveedor no cuenta: kimi/k3.1-pro y kimi-coding/k3.1-pro son el mismo modelo.
    return tuple(
        (modelo.rsplit("/", 1)[-1], esfuerzo if director.con_esfuerzo else "")
        for modelo, esfuerzo in cadena[: director.pasos]
    )


def _texto(cadena: Cadena) -> str:
    return " > ".join((m + " " + e).strip() for m, e in cadena) or "sin modelo"


def comparar(
    director: Director, vivos: Agentes, puestos: dict[str, Cadena], herencia: dict[str, str]
) -> str:
    iguales = 0
    difieren, sin_fila = [], []
    for agente, cadena in sorted(vivos, key=lambda par: par[0].lower()):
        nombre = " ".join(agente.split())
        fila = puestos.get(herencia.get(agente.lower(), ""))
        if fila is None:
            sin_fila.append(nombre)
            continue
        tiene, quiere = _forma(cadena, director), _forma(fila, director)
        if tiene == quiere:
            iguales += 1
        else:
            difieren.append(nombre + " (" + _texto(tiene) + "; tabla " + _texto(quiere) + ")")
    texto = "%d de %d agentes como la tabla" % (iguales, len(vivos))
    if difieren:
        texto += "; difieren: " + ", ".join(difieren)
    if sin_fila:
        texto += "; sin fila en la tabla: " + ", ".join(sin_fila)
    return texto


def _filas_de_claw(generacion: Path) -> dict[str, str]:
    # claw-routing.json asigna fila a los agentes de claw que la herencia no nombra (main).
    try:
        doc = _leer_json(generacion / "config" / "hermes" / "claw-routing.json", "claw-routing.json")
    except Ilegible:
        return {}
    agentes = doc.get("agents") if isinstance(doc, dict) else None
    return {
        nombre.lower(): datos["row"]
        for nombre, datos in (agentes or {}).items()
        if isinstance(datos, dict) and isinstance(datos.get("row"), str)
    }


def informe(home: Path, manifiesto: Path) -> list[str]:
    """Una línea `<director>: <resumen>` por director que usa el motor de esta máquina."""
    canal = home / ".motor-encargos" / "canal.json"
    if not canal.exists():
        return []
    try:
        claves = _leer_json(canal, "canal.json")
    except Ilegible as exc:
        return ["directores: no pude leer (%s)" % exc]
    if not isinstance(claves, dict):
        return ["directores: no pude leer (canal.json ilegible)"]
    propias, error = _tablas_propias(home)
    lineas = ["modelos-propios: no pude leer (%s)" % error] if error else []
    for nombre, director in DIRECTORES.items():
        if nombre not in claves:
            continue
        etiqueta = nombre
        try:
            if nombre in propias:
                etiqueta = nombre + " (tabla propia de esta máquina)"
                puestos, herencia = _tabla_propia(propias[nombre], director)
            else:
                puestos, herencia = leer_tabla(manifiesto)
                if nombre == "claw":
                    herencia = {**herencia, **_filas_de_claw(manifiesto.parent)}
                # El bot que lleva el nombre de su app (MausBot) es su director: la fila de claw.
                if "claw" in herencia and nombre not in herencia:
                    herencia = {**herencia, nombre: herencia["claw"]}
            texto = comparar(director, director.lee(home), puestos, herencia)
        except Exception as exc:
            causa = str(exc) if isinstance(exc, Ilegible) else type(exc).__name__
            texto = "no pude leer (%s)" % causa
            etiqueta = nombre
        lineas.append(etiqueta + ": " + texto)
    return lineas


def _tablas_propias(home: Path) -> tuple[dict, str]:
    # David puede dejar a un director con sus propios modelos: la tabla compartida de la flota
    # no cambia y esta máquina compara ese director contra lo que fijó aquí, fuera del repo.
    # Un archivo ilegible se dice una vez y los directores siguen contra la tabla compartida.
    ruta = home / ".motor-encargos" / "modelos-propios.json"
    if not ruta.exists():
        return {}, ""
    try:
        doc = _leer_json(ruta, "modelos-propios.json")
    except Ilegible as exc:
        return {}, str(exc)
    if not isinstance(doc, dict):
        return {}, "modelos-propios.json ilegible"
    return doc, ""


def _tabla_propia(agentes: object, director: Director) -> tuple[dict[str, Cadena], dict[str, str]]:
    if director.pasos != 1:
        raise Ilegible("modelos-propios.json solo fija directores de un modelo")
    if not isinstance(agentes, dict):
        raise Ilegible("modelos-propios.json ilegible")
    puestos: dict[str, Cadena] = {}
    for agente, paso in agentes.items():
        if not (isinstance(paso, list) and len(paso) == 2 and all(isinstance(x, str) for x in paso)):
            raise Ilegible("modelos-propios.json ilegible")
        puestos[str(agente).lower()] = ((paso[0], paso[1]),)
    return puestos, {clave: clave for clave in puestos}


def imprimir(lineas: list[str], salida) -> None:
    """Escribe cada línea como `modelos <línea>` en la codificación de salida, sin fallar.

    La consola de la PC es cp1252: un nombre de agente con emoji tumbaría el programa y la
    máquina saldría como "no responde". Cada línea se codifica entera antes de escribirla, así
    no queda un prefijo suelto si algo falla.
    """
    codificacion = getattr(salida, "encoding", None) or "utf-8"
    for linea in lineas:
        texto = "modelos " + linea + "\n"
        salida.write(texto.encode(codificacion, "backslashreplace").decode(codificacion))
