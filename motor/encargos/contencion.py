"""El error al atender un encargo se queda en ese encargo.

`barrer` atiende cada encargo por separado y, si atenderlo lanza, llama a
`contener`. El error se anota como `instrumento_fallo` y, tras
`TOPE_FALLOS_BARRIDO` vueltas iguales, deja el encargo aparcado y entrega con
pendientes.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from . import instrumento
from .dominio import Encargo, TransicionIlegal, vista
from .lote import Directo
from .registro import Registro

# Veces que el mismo error al atender un encargo se anota antes de dejar de
# atenderlo: reintentar cada vuelta lo que no cambia solo llena el registro.
TOPE_FALLOS_BARRIDO = 3
_FUENTE_BARRIDO = "barrido"
_CLASE_BARRIDO = "barrido:"
_SIN_REGISTRO: set[tuple[str, str]] = set()
# Vueltas seguidas que falló cada encargo, con la clase del último error. Vive en el
# proceso del supervisor: una vuelta sana la borra, y un registro de eventos no
# distingue una vuelta sana de otra que falló antes de anotar nada.
_RACHA_BARRIDO: dict[str, tuple[str, int]] = {}


def causa_de_excepcion(exc: BaseException) -> str:
    return "; causa: ".join(
        f"{type(e).__name__}: {e} en {Path(marco.filename).name}:{marco.lineno} ({marco.name})"
        for e in (exc, exc.__cause__) if e for marco in traceback.extract_tb(e.__traceback__, -1)
    )[:2000]


def contener(registro: Registro, encargo_id: str, exc: Exception, hora: str) -> dict | None:
    """Devuelve la vista del encargo, o None si no se pudo leer ni anotar."""
    clase = _CLASE_BARRIDO + type(exc).__name__
    causa = causa_de_excepcion(exc)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    previa, vueltas = _RACHA_BARRIDO.get(encargo_id, ("", 0))
    seguidos = vueltas if previa == clase else 0
    _RACHA_BARRIDO[encargo_id] = (clase, seguidos + 1)
    try:
        enc = registro.fold(encargo_id)
        if enc is None:
            return None
        if any(i.clase == clase for i in enc.impedimentos):
            return vista_o_minima(enc)  # ya aparcado: no se llena el registro
        total = enc.fallos_barrido
        Directo(registro, enc).con(instrumento.fallo(
            enc, _FUENTE_BARRIDO, causa,
            "reintentar en la vuelta siguiente"
            if seguidos + 1 < TOPE_FALLOS_BARRIDO
            else "se sigue sin esa herramienta y se entrega con pendientes",
            hora, str(total + 1), clase,
        ))
        if seguidos + 1 >= TOPE_FALLOS_BARRIDO:
            registro.anotar(
                enc.id,
                {
                    "kind": "instrumento_no_disponible",
                    "data": {"clase": clase, "causa": causa, "hora": hora},
                },
                f"instrumento-no-disponible:{enc.id}:{clase}",
            )
            registro.anotar(
                enc.id,
                {
                    "kind": "nota_pr",
                    "data": {
                        "texto": (
                            "herramienta no disponible para este encargo: "
                            f"{clase} ({str(causa)[:200]})"
                        ),
                        "hora": hora,
                    },
                },
                f"nota-herramienta:{enc.id}:{clase}",
            )
            registro.anotar(
                enc.id,
                {
                    "kind": "pendiente_entrega",
                    "data": {
                        "texto": (
                            "el supervisor no pudo atender ese paso tras "
                            f"{seguidos + 1} fallos iguales: {str(causa)[:400]}"
                        ),
                        "hora": hora,
                    },
                },
                f"pendiente-barrido:{enc.id}:{clase}",
            )
            _RACHA_BARRIDO.pop(encargo_id, None)
        return vista_o_minima(registro.fold(encargo_id) or enc)
    except Exception as segundo:
        if (encargo_id, clase) not in _SIN_REGISTRO:
            _SIN_REGISTRO.add((encargo_id, clase))
            print(
                f"no se pudo anotar el fallo de {encargo_id} ({clase}): "
                f"{type(segundo).__name__}: {segundo}",
                file=sys.stderr,
            )
        return None


def vista_o_minima(enc: Encargo) -> dict:
    try:
        return vista(enc)
    except TransicionIlegal:
        return {"encargo": enc.id, "estado": enc.estado}


def aparcado_por_barrido(enc: Encargo | None) -> bool:
    """Aparcado no se atiende, salvo que se esté deteniendo: parar o cancelar un
    encargo aparcado tiene que poder cerrar su proceso."""
    return (
        enc is not None
        and enc.estado != "deteniendo"
        and any(i.clase.startswith(_CLASE_BARRIDO) for i in enc.impedimentos)
    )
