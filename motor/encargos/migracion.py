"""Importa planes del motor anterior como encargos detenidos.

El almacén viejo se lee solo lectura desde una foto coherente. No se
reinsertan eventos viejos en el registro nuevo. Cada plan enlaza su
historial anterior y queda sin escrituras hasta que un director lo reanude.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from motor.store import Store

from .ciclo import recibir
from .registro import Registro

TEXTO_MOTOR_06 = (
    "MOTOR 06. Objetivo pendiente real. Completar los bloques B y C de la "
    "actualización coordinada de goncloud-motor-conf y de las herramientas. "
    "Los PR 26 y 27 ya están integrados y conservados. No los reapliques. "
    "El grep con Invalid range end es un instrumento defectuoso y no es "
    "puerta de entrada ni de cierre. No declares completos los bloques B y C "
    "por haber importado esta descripción. Investiga qué falta del objetivo "
    "completo, comprueba el resultado con un instrumento válido y conserva "
    "los efectos ya confirmados. Este encargo queda detenido sin escrituras "
    "hasta que un director lo reanude tras conciliar los efectos."
)


def _foto_coherente(desde: Path) -> Path:
    origen = sqlite3.connect(f"{desde.resolve().as_uri()}?mode=ro", uri=True)
    try:
        fd, nombre = tempfile.mkstemp(prefix="motor-viejo-", suffix=".db")
        os.close(fd)
        Path(nombre).unlink(missing_ok=True)
        destino = sqlite3.connect(nombre)
        try:
            origen.backup(destino)
        finally:
            destino.close()
        return Path(nombre)
    finally:
        origen.close()


def _detener(registro: Registro, encargo: str, clave: str, hora: str) -> dict:
    from motor.integraciones.directores import parar

    return parar(registro, encargo, clave, hora)


def _relacion(registro: Registro, host: str, plan: str, encargo: str) -> dict:
    enc = registro.fold(encargo)
    return {
        "origen": {"host": host, "plan": plan},
        "encargo": encargo,
        "estado": enc.estado if enc else "",
    }


def importar_encargos(
    desde: Path, registro: Registro, host: str, hora: str
) -> list[dict]:
    desde = Path(desde).expanduser()
    if desde.is_dir():
        desde = desde / "motor.db"
    foto = _foto_coherente(desde)
    try:
        viejo = Store.solo_lectura(foto)
        try:
            eventos = viejo.events()
        finally:
            viejo.db.close()
    finally:
        foto.unlink(missing_ok=True)
        Path(f"{foto}-wal").unlink(missing_ok=True)
        Path(f"{foto}-shm").unlink(missing_ok=True)

    por_plan: dict[str, list] = {}
    for evento in eventos:
        if evento.plan:
            por_plan.setdefault(evento.plan, []).append(evento)

    relacion: list[dict] = []
    for plan in sorted(por_plan):
        lista = por_plan[plan]
        conteo = dict(sorted(Counter(e.kind for e in lista).items()))
        ultimo_kind = lista[-1].kind if lista else ""
        merged = "Merged" in conteo
        verified = "Verified" in conteo
        texto = (
            f"Migración desde el motor anterior. Origen host={host} "
            f"plan={plan} almacén={desde}. Historial anterior enlazado con "
            f"{len(lista)} eventos y último hecho {ultimo_kind}. Los Aceptas "
            "y los comandos antiguos quedan como antecedentes en ese "
            "historial. No son condiciones de admisión de este encargo. "
            "Este encargo es historial o un pendiente retenido. No es "
            "trabajo nuevo. Queda detenido sin escrituras hasta que un "
            "director concilie los efectos confirmados y lo reanude."
        )
        salida = recibir(
            registro,
            texto,
            f"migracion-{host}-{plan}",
            hora,
            director="migracion",
            canal="",
            via="durable",
        )
        encargo = salida["encargo"]
        registro.anotar(
            encargo,
            {
                "kind": "migracion",
                "data": {
                    "hora": hora,
                    "origen": {
                        "host": host,
                        "plan": plan,
                        "almacen": str(desde),
                    },
                    "conteo_por_kind": conteo,
                    "total_eventos": len(lista),
                    "ultimo_kind": ultimo_kind,
                    "merged": merged,
                    "verified": verified,
                    "nota": (
                        f"migración desde {host}/{plan} con "
                        f"{len(lista)} eventos; último {ultimo_kind}"
                    ),
                },
            },
            f"migracion:{host}:{plan}",
        )
        _detener(registro, encargo, f"migracion-parar-{host}-{plan}", hora)
        relacion.append(_relacion(registro, host, plan, encargo))
    return relacion


def crear_motor06(registro: Registro, hora: str) -> dict:
    salida = recibir(
        registro,
        TEXTO_MOTOR_06,
        "migracion-motor-06",
        hora,
        director="migracion",
        canal="",
        via="durable",
    )
    encargo = salida["encargo"]
    registro.anotar(
        encargo,
        {
            "kind": "migracion",
            "data": {
                "hora": hora,
                "origen": {"host": "", "plan": "motor-06", "almacen": ""},
                "conteo_por_kind": {},
                "total_eventos": 0,
                "ultimo_kind": "",
                "merged": True,
                "verified": False,
                "nota": (
                    "MOTOR 06 recuperado desde sus resultados reales; "
                    "PR 26 y 27 integrados y conservados"
                ),
            },
        },
        "migracion:motor-06",
    )
    _detener(registro, encargo, "migracion-parar-motor-06", hora)
    return _relacion(registro, "", "motor-06", encargo)
