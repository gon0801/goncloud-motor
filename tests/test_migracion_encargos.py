"""La importación de un almacén vivo no duplica encargos ni habilita escrituras."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from motor.encargos.dominio import decision_proceso
from motor.encargos.migracion import crear_motor06, importar_encargos
from motor.encargos.registro import Registro
from motor.store import Store

HORA = "2026-10-28T00:00:00Z"


class MigracionEncargos(unittest.TestCase):
    def test_importar_dos_planes_repetido_queda_detenido_sin_escrituras(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            viejo = Store(raiz / "viejo" / "motor.db")
            viejo.append("plan-a", "A", "PlanLoaded", {"plan": "plan-a"})
            viejo.append("plan-a", "A", "Merged", {"sha": "a" * 40})
            viejo.append("plan-a", "A", "Verified", {"huella": "h"})
            viejo.append("plan-b", "B", "PlanLoaded", {"plan": "plan-b"})
            viejo.append("plan-b", "B", "AttemptStarted", {"n": 1})
            viejo.append("", "", "CommandReceived", {"cmd_id": "x"})
            viejo.db.close()
            antes = len(Store.solo_lectura(raiz / "viejo" / "motor.db").events())

            reg = Registro(raiz / "nuevo")
            self.addCleanup(reg.close)
            primera = importar_encargos(raiz / "viejo" / "motor.db", reg, "mac", HORA)
            segunda = importar_encargos(raiz / "viejo" / "motor.db", reg, "mac", HORA)

            self.assertEqual(primera, segunda)
            self.assertEqual(
                [r["origen"] for r in primera],
                [
                    {"host": "mac", "plan": "plan-a"},
                    {"host": "mac", "plan": "plan-b"},
                ],
            )
            for entrada in primera:
                enc = reg.fold(entrada["encargo"])
                self.assertIsNotNone(enc)
                self.assertFalse(enc.escrituras)
                self.assertIn(enc.estado, ("deteniendo", "detenido"))
                migraciones = [
                    e for e in reg.eventos(entrada["encargo"]) if e["kind"] == "migracion"
                ]
                self.assertEqual(len(migraciones), 1)
                self.assertEqual(
                    migraciones[0]["data"]["origen"]["plan"], entrada["origen"]["plan"]
                )
            self.assertEqual(
                len(Store.solo_lectura(raiz / "viejo" / "motor.db").events()), antes
            )

            motor06 = crear_motor06(reg, HORA)
            self.assertEqual(motor06, crear_motor06(reg, HORA))
            enc06 = reg.fold(motor06["encargo"])
            self.assertFalse(enc06.escrituras)
            self.assertIn(enc06.estado, ("deteniendo", "detenido"))
            self.assertIn("bloques B y C", enc06.texto)

    def test_importado_nace_detenido_aunque_se_corte_despues(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            viejo = Store(raiz / "viejo" / "motor.db")
            viejo.append("plan-a", "A", "Merged", {"sha": "a" * 40})
            viejo.db.close()
            reg = Registro(raiz / "nuevo")
            self.addCleanup(reg.close)
            with patch.object(
                Registro, "anotar", side_effect=RuntimeError("corte inyectado")
            ):
                with self.assertRaises(RuntimeError):
                    importar_encargos(raiz / "viejo" / "motor.db", reg, "mac", HORA)
            enc = reg.fold(reg.ids()[0])
            self.assertEqual(enc.estado, "detenido")
            self.assertFalse(enc.escrituras)
            self.assertFalse(enc.intentos_nuevos)
            self.assertFalse(
                decision_proceso(enc, "ausente", None, False, True, HORA)["lanzar"]
            )

    def test_host_y_plan_semejantes_no_se_fusionan(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            uno_v = Store(raiz / "uno" / "motor.db")
            uno_v.append("plan-a", "A", "Merged", {"sha": "a" * 40})
            uno_v.db.close()
            dos_v = Store(raiz / "dos" / "motor.db")
            dos_v.append("mini-plan-a", "B", "Merged", {"sha": "b" * 40})
            dos_v.db.close()
            reg = Registro(raiz / "nuevo")
            self.addCleanup(reg.close)
            uno = importar_encargos(raiz / "uno" / "motor.db", reg, "mac-mini", HORA)
            dos = importar_encargos(raiz / "dos" / "motor.db", reg, "mac", HORA)
            self.assertNotEqual(uno[0]["encargo"], dos[0]["encargo"])
            self.assertEqual(len(reg.ids()), 2)


if __name__ == "__main__":
    unittest.main()
