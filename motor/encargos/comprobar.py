"""Comprobador contenido en un intento y visible a la parada del encargo."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from motor import plataforma
from motor.encargos.comprobacion import interno_de, observar_comprobaciones
from motor.encargos.frontera import leer_parada_local, turno
from motor.encargos.registro import Registro
from motor.runwrap import _publica


def main(path: Path) -> int:
    spec = json.loads(path.read_text(encoding="utf-8"))
    candado = plataforma.candado(path.parent / "arranque.lock")
    if candado is None:
        return 1
    ident = {
        "pid": os.getpid(),
        "inicio": plataforma.hora_inicio(os.getpid()),
        "boot_id": plataforma.boot_id(),
        "host": spec["host"],
        "token": spec["token"],
    }
    _publica(path.parent / "proc.json", json.dumps(ident))
    plataforma.sincroniza_directorio(path.parent)
    reg = Registro(Path(spec["base"]))
    try:
        with turno(reg.base):
            enc = reg.fold(spec["encargo"])
            if (
                not enc
                or enc.conductor != spec["conductor"]
                or not enc.intentos_nuevos
                or leer_parada_local(reg.base)
            ):
                return 0
        os.environ["MOTOR_COMPROBADOR_GRUPO"] = str(os.getpid())
        espacio = Path(spec["cwd"])
        observacion = observar_comprobaciones(
            espacio, interno_de(espacio), Path(spec["directorio"]), path.parent
        )
        if observacion is None:
            from motor.encargos.comprobacion import firma_producto

            firma = firma_producto(espacio)
            observacion = {
                "sin_comprobaciones": True,
                "ejecuciones": [],
                "producto": firma,
                "version": firma,
            }
        _publica(
            Path(spec["directorio"]) / "observaciones.json",
            json.dumps(observacion, ensure_ascii=False),
        )
        return 0
    finally:
        reg.close()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
