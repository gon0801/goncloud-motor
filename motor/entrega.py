"""Qué cuenta como entrega de un agente.

Una entrega válida es una de dos. Un resultado con la forma de hoy cuyo sha es el que
el motor espera, o ``{"bloqueado": "<motivo>"}`` con un motivo no vacío. Del
implementador se espera la punta de la rama del bloque en origin. Del revisor, el sha
del encargo. Un sha que solo está en la copia local no cuenta.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .workspace import git

ENTREGA = "entrega.json"
AJENO = "result.ajeno.json"


def bloqueado(res: object) -> str:
    """El motivo si el agente dijo que no es el código del bloque lo que falla; vacío si no."""
    motivo = res.get("bloqueado") if isinstance(res, dict) else None
    return motivo.strip()[:500] if isinstance(motivo, str) else ""


def resultado_invalido(res: object, revisando: bool) -> str:
    """Frontera de `result.json`: el motivo exacto si no cumple la forma, vacío si cumple."""
    if not isinstance(res, dict):
        return f"result.json no es un objeto (es {type(res).__name__})"

    def falta(clave: str, tipo: str) -> str:
        return f"result.json: '{clave}' tiene que ser {tipo}"

    if not isinstance(res.get("sha"), str):
        return falta("sha", "texto")
    if not revisando:
        return "" if isinstance(res.get("resumen"), str) else falta("resumen", "texto")
    if res.get("veredicto") not in ("APROBADO", "CAMBIOS"):
        return "result.json: 'veredicto' tiene que ser APROBADO o CAMBIOS"
    bloqueantes = res.get("bloqueantes")
    if not isinstance(bloqueantes, list):
        return falta("bloqueantes", "una lista")
    for i, b in enumerate(bloqueantes):
        if not isinstance(b, dict):
            return f"result.json: bloqueantes[{i}] tiene que ser un objeto con 'texto'"
        for clave, obligatoria in (
            ("texto", True),
            ("repro", False),
            ("arreglo", False),
        ):
            if clave in b and not isinstance(b[clave], str):
                return f"result.json: bloqueantes[{i}].{clave} tiene que ser texto"
            if obligatoria and clave not in b:
                return f"result.json: bloqueantes[{i}] no trae 'texto'"
    notas = res.get("notas")
    if not isinstance(notas, list) or not all(isinstance(n, str) for n in notas):
        return falta("notas", "una lista de textos")
    return ""


def ruta(d: Path) -> Path:
    """`entrega.json` si ya se congeló una entrega válida; si no, `result.json`."""
    fija = d / ENTREGA
    return fija if fija.is_file() else d / "result.json"


def _escribe(destino: Path, data: bytes) -> None:
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, destino)


def es_valida(
    res: object, *, revisando: bool, sha_encargo: str = "", punta: str = ""
) -> bool:
    """El resultado cierra al agente. `punta` es la de la rama del bloque en origin."""
    if not revisando and bloqueado(res):
        return True
    if resultado_invalido(res, revisando):
        return False
    visto = res["sha"]
    if revisando:
        return bool(sha_encargo) and visto == sha_encargo
    return len(visto) >= 7 and bool(punta) and punta.startswith(visto)


class Espera:
    """Lo que el motor espera de este papel: la rama del bloque, o el sha del encargo."""

    def __init__(self, rama: str = "", sha: str = ""):
        self.rama = rama
        self.sha = sha
        self._punta = ""
        self._consulta = 0.0

    @property
    def revisando(self) -> bool:
        return bool(self.sha) and not self.rama

    def punta(self, cwd: Path) -> str:
        """SHA de `rama` en origin. Vacío si la rama no está, o si el remoto no contestó."""
        if not self.rama:
            return ""
        ahora = time.monotonic()
        if ahora - self._consulta < 1.0:
            return self._punta
        self._consulta = ahora
        r = git("ls-remote", "origin", f"refs/heads/{self.rama}", cwd=cwd)
        texto = r.stdout.strip() if r.returncode == 0 else ""
        self._punta = texto.split()[0] if texto else ""
        return self._punta

    def acepta(self, data: bytes, cwd: Path) -> bool:
        try:
            res = json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError):
            return False
        punta = ""
        if self.rama and not bloqueado(res) and not resultado_invalido(res, False):
            punta = self.punta(cwd)
        return es_valida(
            res, revisando=self.revisando, sha_encargo=self.sha, punta=punta
        )

    def vigila(self, d: Path, cwd: Path) -> float:
        """mtime de la última entrega válida ya congelada, o 0 si todavía no hay.

        La primera entrega válida se copia a `entrega.json`. Un `result.json` válido y
        distinto de la congelada la reemplaza (temporal y `os.replace`): el revisor puede
        corregir su veredicto hasta que el motor lo cierra. Uno que no es válido nunca
        reemplaza al válido y queda en `result.ajeno.json`.

        Riesgo aceptado: un subagente del propio CLI del revisor hereda su entorno y podría
        escribir un veredicto válido. Trabaja para ese revisor, y el motor no puede saber qué
        proceso escribió el archivo.
        """
        resultado = d / "result.json"
        fija = d / ENTREGA
        try:
            data = resultado.read_bytes()
        except OSError:
            data = None
        if fija.is_file():
            try:
                previa = fija.read_bytes()
            except OSError:
                previa = b""
            if data is not None and data != previa:
                if self.acepta(data, cwd):
                    _escribe(fija, data)
                else:
                    ajeno = d / AJENO
                    try:
                        ya = ajeno.read_bytes()
                    except OSError:
                        ya = None
                    if ya != data:
                        _escribe(ajeno, data)
            try:
                return fija.stat().st_mtime
            except OSError:
                return 0.0
        if data is None or not self.acepta(data, cwd):
            return 0.0
        _escribe(fija, data)
        try:
            return fija.stat().st_mtime
        except OSError:
            return 0.0
