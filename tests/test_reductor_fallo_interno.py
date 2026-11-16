"""Un `fallo_interno` que guardó la versión siguiente pliega con esta.

La versión siguiente detiene un encargo por fallo interno cuando el registro
rechaza un hecho de un paso. Esta no lo escribe; lo sabe plegar, para que volver
a ella no deje un registro que la compuerta de plegado del despliegue rechace.
"""

from __future__ import annotations
from motor.encargos.encargo import evolucionar

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import ordenar, recibir
from motor.encargos.dominio import reducir
from motor.encargos.registro import Registro

HORA = "2026-11-14T10:00:00Z"
DESPUES = "2026-11-14T10:10:00Z"
# Como lo guarda la versión siguiente: el rechazo sin valores y la hora.
RECHAZO_AJENO = {
    "rechazo": "ajena", "kind": "plazo", "ukey": "plazo:*", "campos": "@kind,nota", "hora": HORA,
}


def _guardar_como_la_version_siguiente(reg: Registro, encargo: str, data: dict) -> None:
    ukey = f"fallo-interno:{encargo}:{reg.ultimo_evento(encargo)}"
    db = sqlite3.connect(reg.base / "registro.sqlite")
    with db:
        db.execute(
            "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?, ?, 'fallo_interno', ?, ?)",
            (HORA, encargo, json.dumps(data), ukey),
        )
    db.close()


class FalloInternoGuardado(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.reg = Registro(Path(tmp) / "base")
        self.addCleanup(self.reg.close)
        self.enc = recibir(self.reg, "pedido que se traba", "malo", HORA)["encargo"]
        _guardar_como_la_version_siguiente(self.reg, self.enc, RECHAZO_AJENO)

    def test_el_encargo_queda_detenido_con_la_causa_sin_valores(self):
        enc = self.reg.fold(self.enc)

        self.assertEqual(enc.estado, "detenido")
        for dicho in ("fallo interno", "plazo (plazo:*)", "su clave ya guarda otro hecho", "continuar"):
            self.assertIn(dicho, enc.motivo)

    def test_continuar_lo_reanuda_sin_la_causa(self):
        salida = ordenar(self.reg, self.enc, "continuar", "sigue-1", DESPUES)

        self.assertFalse(salida.get("rechazada"), salida)
        enc = self.reg.fold(self.enc)
        self.assertEqual(enc.estado, "recibido")
        self.assertEqual((enc.motivo, enc.orden_parada), ("", ""))


class ConUnProcesoVivo(unittest.TestCase):
    def test_se_detiene_al_cerrarse_el_arbol_y_conserva_la_causa(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        reg = Registro(Path(tmp) / "base")
        self.addCleanup(reg.close)
        enc = reg.fold(recibir(reg, "pedido con proceso", "vivo", HORA)["encargo"])
        fallo = {"kind": "fallo_interno", "data": {
            "rechazo": "ilegal", "kind": "estrategia", "ukey": "estrategia:*", "hora": HORA,
        }}

        deteniendo = reducir(evolucionar(enc, reserva=True), fallo, nuevo=False)
        detenido = reducir(
            evolucionar(deteniendo, reserva=False),
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": DESPUES}},
            nuevo=False,
        )

        self.assertEqual(deteniendo.estado, "deteniendo")
        self.assertTrue(deteniendo.intento_interrumpido)
        self.assertEqual(detenido.estado, "detenido")
        self.assertEqual(detenido.motivo, deteniendo.motivo)
        for dicho in ("fallo interno", "estrategia (estrategia:*)", "no lo admite", "continuar"):
            self.assertIn(dicho, detenido.motivo)


if __name__ == "__main__":
    unittest.main()
