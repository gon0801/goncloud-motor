"""Clases de espera y sus límites, separados de los escritores.

Cada espera tiene una fila: su tipo, desde dónde cuenta el tope, cómo la nombra
el aviso y dos conductas. `hoy` es lo que hace el motor desde f30430f;
`conducta` es lo que manda con `politica.ESPERAS_V5` encendido. Quien escribe
una espera pide aquí su intervalo y su tope; no guarda copias de esos números.
"""

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
        Conducta(1800, None, None, "seguir"),
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


def conducta(clase: ClaseEspera) -> Conducta:
    """La conducta que manda ahora: la de hoy si el interruptor está apagado."""
    fila = ESPERAS[clase]
    return fila.conducta if politica.ESPERAS_V5 else fila.hoy


def escalera(clase: ClaseEspera) -> tuple[int, ...]:
    """Los segundos de cada intento de una fila con escalera."""
    intervalo = conducta(clase).intervalo
    return intervalo if isinstance(intervalo, tuple) else (intervalo,)


def intervalo_s(clase: ClaseEspera, intentos: int = 1) -> int:
    """Segundos entre dos lecturas de la fila.

    Una escalera gasta un paso por intento y repite el último. Las filas que
    decide quien escribe la espera (`pasada`, `rutas`, `orden`) no tienen una
    cadencia propia: devuelven 0, que significa «cada pasada».
    """
    intervalo = conducta(clase).intervalo
    if isinstance(intervalo, tuple):
        intervalo = intervalo[max(0, min(intentos, len(intervalo)) - 1)]
    return intervalo if isinstance(intervalo, int) else 0


def reanuda_en(clase: ClaseEspera, base: str, intentos: int = 1) -> str:
    """Cuándo vuelve a mirarse: `base` más el intervalo de la clase.

    Una escalera gasta un paso por intento y repite el último. Las filas
    `pasada`, `rutas` y `orden` las decide quien escribe la espera: ahí la base
    ya es la hora elegida y se devuelve tal cual.
    """
    intervalo = conducta(clase).intervalo
    if isinstance(intervalo, tuple):
        intervalo = intervalo[max(0, min(intentos, len(intervalo)) - 1)]
    if not isinstance(intervalo, int):
        return base
    return _mas_segundos(base, intervalo)


def tope_de(clase: ClaseEspera, abierta: str, desde: str = "") -> str:
    """El tope de la fila: la hora en que vence, o los intentos que aguanta.

    Una fila que cuenta desde el primer envío usa `desde` cuando quien escribe
    lo conoce; si no, la apertura de la espera.
    """
    fila = ESPERAS[clase]
    # El tope se escribe siempre con la conducta nueva: es el dato que el
    # reductor compara, y él no mira el interruptor. El interruptor decide
    # cuándo se actúa, no qué tope se guarda.
    patas = fila.conducta
    if patas.tope_intentos is not None:
        return f"{patas.tope_intentos} intentos"
    if patas.tope_s is None:
        return ""
    base = (desde or abierta) if fila.desde == "primer_envio" else abierta
    return _mas_segundos(base, patas.tope_s)


def tope_intentos_de_hoy(clase: ClaseEspera) -> int:
    """Los intentos que aguantaba la fila en f30430f, para los topes ya escritos."""
    return int(ESPERAS[clase].hoy.tope_intentos or 0)


def hora_canonica(texto: str) -> str:
    """La hora en UTC canónico (`...Z`), que es como el barrido compara cadenas.

    Acepta un desplazamiento (`+02:00`, `-07:00`) y devuelve el mismo instante en
    UTC. Devuelve "" si no se lee como hora.
    """
    from datetime import datetime, timezone

    try:
        instante = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def es_hora_utc(texto: str) -> bool:
    """La cadena es una hora que el barrido puede ordenar."""
    return bool(hora_canonica(texto))


def _mas_segundos(base: str, segundos: int) -> str:
    from datetime import datetime, timedelta, timezone

    try:
        instante = datetime.fromisoformat(str(base).replace("Z", "+00:00"))
        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        instante = datetime.now(timezone.utc)
    return (instante + timedelta(seconds=segundos)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
