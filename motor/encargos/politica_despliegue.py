"""Contrato de despliegue por fase, leído sin validar el resto del plan legado."""

from __future__ import annotations
from motor.io import leer_texto

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

from motor.conf import RepoConf

from .dominio import Encargo, FASE_IMPLICITA
from .registro import Registro

_BLOQUE = re.compile(r"^##\s+(?:Bloque|Fase)\s+([\w-]+)(?:\s|$)")
_RUTA = re.compile(r"[^\s`\"'()<>\[\],;]+\.md\b")
_RECETA = ("desplegar", "checklist", "rollback")


@dataclass(frozen=True)
class PoliticaDespliegue:
    requiere_despliegue: bool | None = None
    desplegar: str = ""
    checklist: str = ""
    rollback: str = ""
    fuente: str = ""
    huella: str = ""
    correspondencia: tuple[tuple[str, str], ...] = ()
    archivo_plan: str = ""

    def datos(self) -> dict:
        datos = {
            "requiere_despliegue": self.requiere_despliegue,
            "despliegue": {k: getattr(self, k) for k in _RECETA},
            "fuente": self.fuente,
            "huella": self.huella,
        }
        if self.correspondencia:
            datos["correspondencia"] = {
                "archivo": self.archivo_plan,
                "fases": [list(par) for par in self.correspondencia],
            }
        return datos


def _huella(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _revisiones(enc: Encargo, fase=None) -> tuple[str, ...]:
    fase = fase or enc.fase_actual
    if fase is None:
        return ()
    return tuple(dict.fromkeys(sha for sha in (
        fase.integrado_sha, fase.producto.sha_git if fase.producto else "",
    ) if sha))


def _leer(
    espacio: Path, archivo: Path, enc: Encargo, fase=None
) -> tuple[str, str] | None:
    """Los archivos del repo se leen del producto integrado cuando existe."""
    relativo = archivo.relative_to(espacio).as_posix() if archivo.is_relative_to(espacio) else ""
    revisiones = _revisiones(enc, fase) if relativo else ()
    try:
        for revision in revisiones:
            resultado = subprocess.run(
                ["git", "-C", str(espacio), "show", f"{revision}:{relativo}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10, check=False,
            )
            if resultado.returncode == 0:
                return resultado.stdout, f"{relativo}@{revision}"
            existe = subprocess.run(
                ["git", "-C", str(espacio), "cat-file", "-e", f"{revision}^{{commit}}"],
                capture_output=True, timeout=10, check=False,
            )
            if existe.returncode == 0:
                # El commit existe pero no contiene el archivo: no se rescata
                # una declaración que se haya quitado de la versión integrada.
                return None
        if revisiones:
            return None
        return leer_texto(archivo), relativo or str(archivo)
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None


def _fuentes(espacio: Path, enc: Encargo) -> tuple[Path, ...]:
    fuentes = []
    for texto in _RUTA.findall(enc.texto):
        if ":" in texto or texto.startswith("~"):
            continue
        archivo = Path(texto)
        archivo = (archivo if archivo.is_absolute() else espacio / archivo).resolve()
        if archivo.is_relative_to(espacio):
            fuentes.append(archivo)
    for texto in enc.adjuntos:
        archivo = Path(texto).expanduser()
        archivo = (archivo if archivo.is_absolute() else espacio / archivo).resolve()
        fuentes.append(archivo)
    return tuple(dict.fromkeys(fuentes))


def _lineas_plan(texto: str):
    cerca = ""
    for linea in texto.splitlines():
        limpia = linea.strip()
        if limpia.startswith(("```", "~~~")):
            marca = limpia[:3]
            cerca = "" if cerca == marca else marca if not cerca else cerca
            continue
        if not cerca:
            yield linea


def _correspondencia_por_posicion(texto: str, enc: Encargo) -> tuple[tuple[str, str], ...]:
    bloques = tuple(
        bloque.group(1) for linea in _lineas_plan(texto)
        if (bloque := _BLOQUE.match(linea))
    )
    ids = tuple(fase.id for fase in enc.fases)
    if (not bloques or len(bloques) != len(ids) or enc.fase_activa not in ids
            or len(set(bloques)) != len(bloques) or len(set(ids)) != len(ids)):
        return ()
    # Un nombre que ya coincide es un ancla: no se admite otra posición para él.
    if any(ident in bloques and bloques[i] != ident for i, ident in enumerate(ids)):
        return ()
    return tuple(zip(ids, bloques))


def _del_plan(texto: str, fase_id: str, fuente: str) -> PoliticaDespliegue | None:
    campos: dict[str, str] = {}
    activa = False
    encontrados = 0
    ambiguo = False
    for linea in _lineas_plan(texto):
        limpia = linea.strip()
        bloque = _BLOQUE.match(linea)
        if bloque:
            activa = bloque.group(1) == fase_id
            encontrados += int(activa)
        elif linea.startswith("## "):
            activa = False
        elif activa and ":" in limpia:
            clave, valor = limpia.split(":", 1)
            if clave in ("lane", "despliegue"):
                ambiguo |= clave in campos
                campos[clave] = valor.strip()
    if not encontrados:
        return None
    politica = PoliticaDespliegue(fuente=fuente, huella=_huella(texto))
    if encontrados != 1 or ambiguo:
        return politica
    lane = campos.get("lane", "")
    if lane and lane not in ("fast", "gate", "release"):
        return politica
    if "despliegue" in campos:
        if lane == "fast":
            return politica
        try:
            partes = [p.split("=", 1) for p in shlex.split(campos["despliegue"])]
            if any(len(p) != 2 for p in partes):
                return politica
            rutas = dict(partes)
            if len(rutas) != len(partes) or set(rutas) != set(_RECETA):
                return politica
            if all(ruta.strip() for ruta in rutas.values()):
                return replace(politica, requiere_despliegue=True, **rutas)
        except ValueError:
            pass
        # Una declaración defectuosa no permite sustituir la receta elegida
        # por la configuración del repo. Sólo la ausencia admite ese fallback.
        return politica
    if lane in ("fast", "gate"):
        return replace(politica, requiere_despliegue=False)
    if lane == "release":
        return replace(politica, requiere_despliegue=True)
    return politica


def _politica_asociada(texto: str, fuente: str, fase_id: str,
                      mapa: tuple[tuple[str, str], ...], archivo: str) -> PoliticaDespliegue:
    for posicion, (ident, bloque) in enumerate(mapa, 1):
        if ident == fase_id:
            fuente += f"#Bloque {bloque} (posición {posicion}/{len(mapa)})"
            politica = _del_plan(texto, bloque, fuente)
            if politica is not None:
                return replace(politica, correspondencia=mapa, archivo_plan=archivo)
    return PoliticaDespliegue()


def _con_receta_repo(
    politica: PoliticaDespliegue, espacio: Path, enc: Encargo, fase=None
) -> PoliticaDespliegue:
    if politica.requiere_despliegue is not True or any(getattr(politica, k) for k in _RECETA):
        return politica
    lectura = _leer(espacio, espacio / ".motor.toml", enc, fase)
    if lectura is None:
        return politica
    texto, fuente = lectura
    try:
        conf = RepoConf.parse(texto)
    except (ValueError, TypeError, AttributeError):
        return politica
    rutas = {k: getattr(conf, k) for k in _RECETA}
    if not all(isinstance(v, str) and v.strip() for v in rutas.values()):
        return politica
    return replace(politica, **rutas, fuente=f"{politica.fuente}; {fuente}",
                   huella=_huella(politica.huella + texto))


def resolver(espacio: Path, enc: Encargo, fase=None) -> PoliticaDespliegue:
    """Desconocido conserva la exigencia global; nunca significa sin despliegue."""
    espacio = espacio.resolve()
    fase = fase or enc.fase_actual
    if fase is not None and fase.despliegue_fuente and fase.requiere_despliegue is not None:
        politica = PoliticaDespliegue(
            fase.requiere_despliegue, fase.desplegar, fase.checklist, fase.rollback,
            fase.despliegue_fuente, fase.despliegue_huella,
            enc.despliegue_mapa, enc.despliegue_archivo,
        )
        return _con_receta_repo(politica, espacio, enc, fase)
    if fase is None or fase.id == FASE_IMPLICITA:
        politica = PoliticaDespliegue(enc.entrega.nivel == "desplegado", fuente="entrega del encargo",
                                     huella=_huella(enc.entrega.nivel))
    else:
        politicas = []
        lecturas = []
        for archivo in _fuentes(espacio, enc):
            lectura = _leer(espacio, archivo, enc, fase)
            if lectura is not None:
                lecturas.append((archivo, *lectura))
                candidata = _del_plan(lectura[0], fase.id, lectura[1])
                if candidata is not None:
                    politicas.append(candidata)
        if not politicas and enc.despliegue_mapa:
            if {f.id for f in enc.fases} != {ident for ident, _ in enc.despliegue_mapa}:
                return PoliticaDespliegue()
            lectura = _leer(espacio, Path(enc.despliegue_archivo), enc, fase)
            if lectura is None:
                return PoliticaDespliegue()
            politica = _politica_asociada(
                *lectura, fase.id, enc.despliegue_mapa, enc.despliegue_archivo,
            )
            return _con_receta_repo(politica, espacio, enc, fase)
        if not politicas:
            posicionales = [
                (archivo, texto, fuente, mapa) for archivo, texto, fuente in lecturas
                if (mapa := _correspondencia_por_posicion(texto, enc))
            ]
            if len(posicionales) > 1:
                return PoliticaDespliegue()
            if posicionales:
                archivo, texto, fuente, mapa = posicionales[0]
                politicas.append(_politica_asociada(texto, fuente, fase.id, mapa, str(archivo)))
        if politicas:
            politica = politicas[0]
            contrato = lambda p: (p.requiere_despliegue, p.desplegar, p.checklist, p.rollback)
            if any(contrato(p) != contrato(politica) for p in politicas[1:]):
                return PoliticaDespliegue()
        elif fase.requiere_despliegue is not None:
            politica = PoliticaDespliegue(fase.requiere_despliegue, fase.desplegar,
                                         fase.checklist, fase.rollback, ".interno/fases.json")
            politica = replace(politica, huella=_huella(json.dumps(politica.datos(), sort_keys=True)))
        else:
            return PoliticaDespliegue()
    if enc.despliegue_mapa:
        politica = replace(politica, correspondencia=enc.despliegue_mapa,
                           archivo_plan=enc.despliegue_archivo)
    return _con_receta_repo(politica, espacio, enc, fase)


def adoptar(
    registro: Registro, enc: Encargo, espacio: Path, hora: str, fase=None
) -> Encargo:
    """Persiste el contrato antes de que el nivel permita cerrar esta fase."""
    if enc.entrega.nivel != "desplegado":
        return enc
    fase = fase or enc.fase_actual
    politica = resolver(espacio, enc, fase)
    if politica.requiere_despliegue is None:
        return enc
    if fase is not None and fase.estado == "preparada":
        return enc
    fase_id = fase.id if fase else FASE_IMPLICITA
    registro.anotar(enc.id, {
        "kind": "politica_despliegue",
        "data": {"fase": fase_id, "hora": hora, **politica.datos()},
    }, f"politica-despliegue:{enc.id}:{fase_id}:{politica.huella}")
    return registro.fold(enc.id) or enc
