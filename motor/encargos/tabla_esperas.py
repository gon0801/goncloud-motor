"""Clases de espera y sus límites, separados de los escritores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import politica

ClaseEspera = Literal[
    "revision_deepseek", "lectura_deepseek_fallida", "checks_merge",
    "despliegue_en_curso", "despliegue_sin_receta",
    "despliegue_sin_receta_otra_fase", "autoridad", "conductor_ocupado",
    "observacion_comprobador", "efecto_incierto", "sin_puerto_github",
    "cabeza_desviada", "impedimento_trabajo", "deteniendo", "base_movida",
    "tropiezo_merge", "destino_ocupado", "proveedor", "acceso_kimi",
    "sesion_local", "repo", "empuje", "empuje_transitorio", "github_pasajero",
    "preparacion_lanzamiento", "aprobado_sin_novedad", "orden_espera",
]


@dataclass(frozen=True)
class Conducta:
    intervalo: int | tuple[int, ...] | Literal["pasada", "rutas", "orden"]
    tope_s: int | None
    tope_intentos: int | None
    al_tope: Literal[
        "detener", "avisar", "omitir", "pendientes", "devolver", "impedimento", "seguir"
    ]


@dataclass(frozen=True)
class FilaEspera:
    clase: ClaseEspera
    tipo: Literal["por_hecho", "por_hora"]
    desde: Literal["abierta", "primer_envio"]
    legible: str
    conducta: Conducta
    hoy: Conducta


_NORMAL = politica.TOPE_ESPERA_MIN * 60
_PASAJERA = politica.TOPE_ESPERA_PASAJERA_MIN * 60
_DEEPSEEK = politica.TOPE_DEEPSEEK_MIN * 60
_OCUPADO = (900, 1800, 3600, 7200, 14400)

ESPERAS: dict[ClaseEspera, FilaEspera] = {
    "revision_deepseek": FilaEspera(
        "revision_deepseek", "por_hecho", "primer_envio", "revisión de DeepSeek",
        Conducta(60, _DEEPSEEK, None, "omitir"),
        Conducta(60, _DEEPSEEK, None, "omitir"),
    ),
    "lectura_deepseek_fallida": FilaEspera(
        "lectura_deepseek_fallida", "por_hecho", "primer_envio", "lectura de DeepSeek",
        Conducta(120, _DEEPSEEK, None, "omitir"),
        Conducta(120, _DEEPSEEK, None, "omitir"),
    ),
    "checks_merge": FilaEspera(
        "checks_merge", "por_hecho", "abierta", "checks del merge",
        Conducta(120, _NORMAL, None, "detener"), Conducta(120, None, None, "seguir"),
    ),
    "despliegue_en_curso": FilaEspera(
        "despliegue_en_curso", "por_hecho", "abierta", "despliegue en curso",
        Conducta(30, _NORMAL, None, "avisar"), Conducta(30, None, None, "seguir"),
    ),
    "despliegue_sin_receta": FilaEspera(
        "despliegue_sin_receta", "por_hecho", "abierta", "receta de despliegue",
        Conducta(300, _NORMAL, None, "detener"), Conducta(300, None, None, "seguir"),
    ),
    "despliegue_sin_receta_otra_fase": FilaEspera(
        "despliegue_sin_receta_otra_fase", "por_hora", "abierta",
        "receta de otra fase", Conducta(30, None, 5, "impedimento"),
        Conducta(30, None, 5, "impedimento"),
    ),
    "autoridad": FilaEspera(
        "autoridad", "por_hecho", "abierta", "autoridad",
        Conducta(30, _PASAJERA, None, "detener"), Conducta(30, None, None, "seguir"),
    ),
    "conductor_ocupado": FilaEspera(
        "conductor_ocupado", "por_hora", "abierta", "conductor ocupado",
        Conducta(30, _PASAJERA, None, "detener"), Conducta(30, None, None, "seguir"),
    ),
    "observacion_comprobador": FilaEspera(
        "observacion_comprobador", "por_hecho", "abierta", "observación del comprobador",
        Conducta(30, _NORMAL, None, "detener"), Conducta(30, None, None, "seguir"),
    ),
    "efecto_incierto": FilaEspera(
        "efecto_incierto", "por_hecho", "abierta", "efecto incierto",
        Conducta(30, _NORMAL, None, "detener"), Conducta(30, None, None, "seguir"),
    ),
    "sin_puerto_github": FilaEspera(
        "sin_puerto_github", "por_hecho", "abierta", "puerto de GitHub",
        Conducta(300, _NORMAL, None, "detener"), Conducta(300, None, None, "seguir"),
    ),
    "cabeza_desviada": FilaEspera(
        "cabeza_desviada", "por_hecho", "abierta", "cabeza del PR",
        Conducta(60, _NORMAL, None, "detener"), Conducta(60, None, None, "seguir"),
    ),
    "impedimento_trabajo": FilaEspera(
        "impedimento_trabajo", "por_hecho", "abierta", "impedimento del trabajo",
        Conducta("pasada", _NORMAL, None, "detener"),
        Conducta("pasada", None, None, "seguir"),
    ),
    "deteniendo": FilaEspera(
        "deteniendo", "por_hecho", "abierta", "cierre del proceso",
        Conducta("pasada", _NORMAL, None, "avisar"),
        Conducta("pasada", None, None, "seguir"),
    ),
    "base_movida": FilaEspera(
        "base_movida", "por_hora", "abierta", "base movida",
        Conducta(120, _NORMAL, None, "detener"), Conducta(120, None, None, "seguir"),
    ),
    "tropiezo_merge": FilaEspera(
        "tropiezo_merge", "por_hora", "abierta", "merge pasajero",
        Conducta((120, 300, 900, 1800), _PASAJERA, None, "detener"),
        Conducta(120, None, 5, "pendientes"),
    ),
    "destino_ocupado": FilaEspera(
        "destino_ocupado", "por_hora", "abierta", "destino ocupado",
        Conducta(_OCUPADO, None, 6, "pendientes"),
        Conducta(_OCUPADO, None, 6, "pendientes"),
    ),
    "proveedor": FilaEspera(
        "proveedor", "por_hora", "abierta", "proveedor de modelos",
        Conducta("rutas", _PASAJERA, None, "detener"),
        Conducta("rutas", None, None, "seguir"),
    ),
    "acceso_kimi": FilaEspera(
        "acceso_kimi", "por_hora", "abierta", "acceso a Kimi",
        Conducta(0, _NORMAL, None, "detener"), Conducta(0, None, None, "seguir"),
    ),
    "sesion_local": FilaEspera(
        "sesion_local", "por_hora", "abierta", "sesión local",
        Conducta((0, 1800), _NORMAL, None, "detener"),
        Conducta((0, 1800), None, None, "seguir"),
    ),
    "repo": FilaEspera(
        "repo", "por_hora", "abierta", "repositorio",
        Conducta((300, 900, 1800), _PASAJERA, None, "detener"),
        Conducta(300, None, 3, "impedimento"),
    ),
    "empuje": FilaEspera(
        "empuje", "por_hora", "abierta", "empuje",
        Conducta(120, None, 2, "devolver"), Conducta(120, None, 2, "devolver"),
    ),
    "empuje_transitorio": FilaEspera(
        "empuje_transitorio", "por_hora", "abierta", "empuje transitorio",
        Conducta(120, _PASAJERA, None, "detener"),
        Conducta(120, None, None, "seguir"),
    ),
    "github_pasajero": FilaEspera(
        "github_pasajero", "por_hora", "abierta", "fallo pasajero de GitHub",
        Conducta(120, _PASAJERA, None, "detener"),
        Conducta(120, None, None, "seguir"),
    ),
    "preparacion_lanzamiento": FilaEspera(
        "preparacion_lanzamiento", "por_hora", "abierta", "preparación del lanzamiento",
        Conducta(300, _NORMAL, None, "detener"), Conducta(300, None, None, "seguir"),
    ),
    "aprobado_sin_novedad": FilaEspera(
        "aprobado_sin_novedad", "por_hora", "abierta", "aprobado sin novedad",
        Conducta(300, _NORMAL, None, "detener"), Conducta(300, None, None, "seguir"),
    ),
    "orden_espera": FilaEspera(
        "orden_espera", "por_hora", "abierta", "espera ordenada",
        Conducta("orden", None, 1, "seguir"),
        Conducta("orden", None, 1, "seguir"),
    ),
}
