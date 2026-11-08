"""Vincula el intento a los ejecutables y al paquete de su generación."""

from __future__ import annotations
from motor.io import leer_json

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from motor.encargos.catalogo_agentes import PREFIJO_DEEPSEEK_API

_TOPE_AYUDA_S = 30
_BANDERA = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)(?![\w-])")


class SuperficieOpenCodeNoAcreditada(RuntimeError):
    """El OpenCode ligado al intento no declara una forma compatible."""


def cargar_runtime(generacion: Path) -> dict:
    path = generacion / "runtime.json"
    if not path.exists():
        return {}
    doc = leer_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("lanzadores", {}), dict):
        raise TypeError("runtime de generacion invalido")
    return doc


def _ruta(generacion: Path, ruta: str) -> Path:
    relativo = Path(ruta)
    if relativo.is_absolute():
        raise ValueError("la ruta de generacion debe ser relativa")
    resultado = (generacion / relativo).resolve()
    if not resultado.is_relative_to(generacion.resolve()):
        raise ValueError("la ruta sale de la generacion")
    return resultado


def resolver_ejecutable(
    generacion: Path, cmd: list[str], *, requerido: bool = False
) -> list[str]:
    doc = cargar_runtime(generacion)
    nombre = Path(cmd[0]).stem.lower()
    prefijo = doc.get("lanzadores", {}).get(nombre)
    if prefijo is None:
        if requerido:
            raise ValueError("la generacion no contiene el ejecutable solicitado")
        return list(cmd)
    if (
        not isinstance(prefijo, list)
        or not prefijo
        or not all(isinstance(x, str) and x for x in prefijo)
    ):
        raise ValueError("lanzador de generacion invalido")
    resuelto = []
    for argumento in prefijo:
        if not Path(argumento).is_absolute() and (generacion / argumento).exists():
            argumento = str(_ruta(generacion, argumento))
        resuelto.append(argumento)
    if not Path(resuelto[0]).is_file():
        raise ValueError("falta el ejecutable de la generacion")
    return [*resuelto, *cmd[1:]]


def modelos_de_chain(
    generacion: Path, funcion: str, prefijo: str
) -> tuple[str, ...]:
    """Todas las identidades `proveedor/modelo` de la chain con ese
    prefijo, en orden de la política y sin duplicados."""
    doc = cargar_runtime(generacion)
    politica = doc.get("politica")
    if not politica:
        return ()
    try:
        tabla = leer_json(_ruta(generacion, politica))
    except (OSError, ValueError):
        return ()
    vistos: list[str] = []
    for etiqueta in tabla.get("chains", {}).get(funcion, []):
        ref, _, _esfuerzo = str(etiqueta).partition(" (")
        if ref.startswith(prefijo) and ref not in vistos:
            vistos.append(ref)
    return tuple(vistos)


def modelo_de_chain(generacion: Path, funcion: str, prefijo: str) -> str:
    """Identidad `proveedor/modelo` de la primera etiqueta de la chain con
    ese prefijo de proveedor. Vacio si la política no ofrece ninguna."""
    doc = cargar_runtime(generacion)
    politica = doc.get("politica")
    if not politica:
        return ""
    try:
        tabla = leer_json(_ruta(generacion, politica))
    except (OSError, ValueError):
        return ""
    for etiqueta in tabla.get("chains", {}).get(funcion, []):
        ref, _, _esfuerzo = str(etiqueta).partition(" (")
        if ref.startswith(prefijo):
            return ref
    return ""


def _poner_modelo(argumentos: list[str], opciones: tuple[str, ...], valor: str) -> None:
    indices = [i for i, arg in enumerate(argumentos) if arg in opciones]
    if indices:
        for i in indices:
            argumentos[i + 1] = valor
    else:
        argumentos.extend([opciones[0], valor])


def _modelo_de(argumentos: list[str]) -> str:
    for indice, argumento in enumerate(argumentos):
        if argumento in ("--model", "-m") and indice + 1 < len(argumentos):
            return argumentos[indice + 1]
    return ""


def _ejecutable_de(prefijo: list[str]) -> str:
    candidato = prefijo[-1]
    if Path(candidato).is_file():
        return str(Path(candidato).resolve())
    hallado = shutil.which(candidato)
    if hallado:
        return hallado
    raise SuperficieOpenCodeNoAcreditada(
        "no se encontro el ejecutable de OpenCode: " + candidato
    )


# La clave incluye mtime y tamano para no repetir la sonda si el CLI no cambia
# y para volver a consultarla si la generacion publica otro ejecutable.
@lru_cache(maxsize=16)
def _ayuda_de(prefijo: tuple[str, ...], _mtime_ns: int, _tamano: int) -> str:
    try:
        hecho = subprocess.run(
            [*prefijo, "run", "--help"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TOPE_AYUDA_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SuperficieOpenCodeNoAcreditada(
            "no se pudo consultar la ayuda de OpenCode: " + type(exc).__name__
        ) from exc
    if hecho.returncode != 0:
        raise SuperficieOpenCodeNoAcreditada(
            "la ayuda de OpenCode termino con codigo " + str(hecho.returncode)
        )
    texto = (hecho.stdout or "") + "\n" + (hecho.stderr or "")
    if not texto.strip():
        raise SuperficieOpenCodeNoAcreditada(
            "la ayuda de OpenCode no devolvio opciones por stdout ni por stderr"
        )
    return texto


def _banderas_de_opencode(prefijo: list[str]) -> frozenset[str]:
    real = _ejecutable_de(prefijo)
    try:
        estado = Path(real).stat()
    except OSError as exc:
        raise SuperficieOpenCodeNoAcreditada(
            "no se pudo leer el ejecutable de OpenCode: " + real
        ) from exc
    texto = _ayuda_de((*prefijo[:-1], real), estado.st_mtime_ns, estado.st_size)
    banderas = frozenset(_BANDERA.findall(texto))
    if "--standalone" not in banderas and "--pure" not in banderas:
        raise SuperficieOpenCodeNoAcreditada(
            "el OpenCode ligado al intento no ofrece ni --standalone ni --pure: "
            + real
        )
    return banderas


def _adaptar_deepseek(argumentos: list[str], cmd: list[str]) -> list[str]:
    """Ajusta solo la ruta deepseek/ a la superficie del OpenCode efectivo."""
    # resolver_ejecutable antepone el lanzador de la generacion; el resto de
    # argumentos conserva su posicion.
    prefijo = argumentos[: len(argumentos) - len(cmd) + 1]
    banderas = _banderas_de_opencode(prefijo)
    if "--standalone" in banderas:
        return [argumento for argumento in argumentos if argumento != "--pure"]
    if "--standalone" in argumentos:
        return [
            "--pure" if argumento == "--standalone" else argumento
            for argumento in argumentos
        ]
    if "--pure" not in argumentos:
        posicion = argumentos.index("run") + 1 if "run" in argumentos else 1
        return [*argumentos[:posicion], "--pure", *argumentos[posicion:]]
    return argumentos


def vincular(
    generacion: Path, cmd: list[str], *, funcion: str = "codigo", modelo_explicito: bool = False
) -> list[str]:
    nombre = Path(cmd[0]).stem.lower()
    doc = cargar_runtime(generacion)
    argumentos = resolver_ejecutable(generacion, cmd)
    if nombre == "opencode" and _modelo_de(argumentos).startswith(PREFIJO_DEEPSEEK_API):
        argumentos = _adaptar_deepseek(argumentos, cmd)
    politica = doc.get("politica")
    if politica and nombre == "grok":
        tabla = leer_json(_ruta(generacion, politica))
        elegible = [
            (ref, esfuerzo.rstrip(")"))
            for ref, _, esfuerzo in (
                str(etiqueta).partition(" (")
                for etiqueta in tabla.get("chains", {}).get(funcion, [])
            )
            if ref.startswith("xai/")
        ]
        actual = ""
        for i, arg in enumerate(argumentos):
            if arg in ("--model", "-m") and i + 1 < len(argumentos):
                actual = argumentos[i + 1]
        elegida = next(
            ((r, e) for r, e in elegible if r.split("/", 1)[1] == actual),
            elegible[0] if elegible and not modelo_explicito else None,
        )
        if elegida is not None:
            _poner_modelo(argumentos, ("--model", "-m"), elegida[0].split("/", 1)[1])
            _poner_modelo(argumentos, ("--reasoning-effort", "--effort"), elegida[1])
    if politica and nombre == "codex":
        refs = modelos_de_chain(generacion, funcion, "openai/")
        actual = ""
        for i, arg in enumerate(argumentos):
            if arg in ("-m", "--model") and i + 1 < len(argumentos):
                actual = argumentos[i + 1]
        if not modelo_explicito and refs and all(r.split("/", 1)[1] != actual for r in refs):
            _poner_modelo(argumentos, ("-m", "--model"), refs[0].split("/", 1)[1])
    return argumentos


class PotetoAusente(RuntimeError):
    pass


def exigir_poteto(
    generacion: Path | None, base: Path, runtime: Path
) -> tuple[Path, str]:
    """Copia poteto-mode al runtime y devuelve (ruta, digest sha256).

    A diferencia de copiar_poteto, la ausencia es un error visible: el
    responsable nunca corre sin poteto en silencio.
    """
    import hashlib

    skill = copiar_poteto(generacion, base, runtime)
    if skill is None:
        skills = Path.home() / ".agents" / "skills"
        fuente = skills / "poteto-mode" / "SKILL.md"
        if fuente.is_file():
            destino = runtime / "skills"
            shutil.copytree(skills, destino, dirs_exist_ok=True)
            skill = destino / "poteto-mode" / "SKILL.md"
    if skill is None:
        raise PotetoAusente(
            "falta poteto-mode en la generacion de flota, en "
            f"{base / 'politica' / 'poteto'} y en ~/.agents/skills/poteto-mode"
        )
    return skill, hashlib.sha256(skill.read_bytes()).hexdigest()


def copiar_poteto(generacion: Path | None, base: Path, runtime: Path) -> Path | None:
    fuentes = []
    if generacion is not None:
        referencia = cargar_runtime(generacion).get("poteto")
        if referencia:
            fuentes.append(_ruta(generacion, referencia))
    fuentes.append(base / "politica" / "poteto")
    for fuente in fuentes:
        for relativo in (
            Path("skills/poteto-mode/SKILL.md"),
            Path("poteto-mode/SKILL.md"),
            Path("SKILL.md"),
        ):
            if (fuente / relativo).is_file():
                destino = runtime / "poteto"
                shutil.copytree(fuente, destino, dirs_exist_ok=True)
                return destino / relativo
    return None
