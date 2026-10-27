"""Vincula el intento a los ejecutables y al paquete de su generación."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def cargar_runtime(generacion: Path) -> dict:
    path = generacion / "runtime.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
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


def vincular(generacion: Path, cmd: list[str], *, funcion: str = "codigo") -> list[str]:
    doc = cargar_runtime(generacion)
    nombre = Path(cmd[0]).stem.lower()
    prefijo = doc.get("lanzadores", {}).get(nombre)
    if prefijo is None:
        return cmd
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
    argumentos = list(cmd[1:])
    politica = doc.get("politica")
    if politica and nombre == "grok":
        tabla = json.loads(_ruta(generacion, politica).read_text(encoding="utf-8"))
        for etiqueta in tabla.get("chains", {}).get(funcion, []):
            ref, _, esfuerzo = etiqueta.partition(" (")
            if ref.startswith("xai/"):
                for opciones, valor in (
                    (("--model", "-m"), ref.split("/", 1)[1]),
                    (("--reasoning-effort", "--effort"), esfuerzo.rstrip(")")),
                ):
                    indices = [i for i, arg in enumerate(argumentos) if arg in opciones]
                    if indices:
                        for i in indices:
                            argumentos[i + 1] = valor
                    else:
                        argumentos.extend([opciones[0], valor])
                break
    return [*resuelto, *argumentos]


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
