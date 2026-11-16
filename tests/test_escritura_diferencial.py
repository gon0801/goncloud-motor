"""La escritura diferencial detecta cambios que el corpus de plegado no ve."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "escritura_diferencial.py"


class EscrituraDiferencial(unittest.TestCase):
    def setUp(self) -> None:
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.corpus = self.raiz / "corpus"
        registros = self.corpus / "registros"
        registros.mkdir(parents=True)
        eventos = [
            ("entrega", {"id": "ejemplo", "clave": "k", "huella": "h", "texto": "pedido"}),
            ("fases", {"fases": [
                {"id": "a", "resultado": "primero"},
                {"id": "b", "resultado": "segundo"},
            ]}),
            ("ajuste", {"texto": "corrección secreta"}),
            ("comprobacion", {
                "clase": "confirmado", "evidencia": "evidencia secreta",
                "fase": "a", "ajustes_vistos": 0,
            }),
            ("solicitud", {
                "id": "s", "fase": "b", "intento": "i", "operacion": "leer",
            }),
        ]
        with sqlite3.connect(registros / "uno.sqlite") as db:
            db.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT,"
                " encargo TEXT, kind TEXT, data TEXT, ukey TEXT UNIQUE)"
            )
            db.executemany(
                "INSERT INTO events VALUES (?, '', 'ejemplo', ?, ?, ?)",
                [(i, kind, json.dumps(data), f"{kind}:{i}")
                 for i, (kind, data) in enumerate(eventos, 1)],
            )
        (self.corpus / "meta.json").write_text(
            json.dumps({"eventos": len(eventos)}), encoding="utf-8",
        )

    def correr(self, *opciones: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HERRAMIENTA), "--base", "origin/main",
             "--corpus", str(self.corpus), "--max-casos", "900", *opciones],
            cwd=RAIZ, capture_output=True, text=True, check=False,
        )

    def mutar(self, original: str, mutado: str, modulo: str) -> Path:
        arbol = self.raiz / "mutado"
        shutil.copytree(RAIZ / "motor", arbol / "motor")
        archivo = arbol / "motor" / "encargos" / "reductor" / modulo
        contenido = archivo.read_text(encoding="utf-8")
        self.assertIn(original, contenido)
        archivo.write_text(contenido.replace(original, mutado, 1), encoding="utf-8")
        return arbol

    def test_misma_escritura_pasa_con_casos_acotados(self) -> None:
        hecho = self.correr("--kinds", "comprobacion,solicitud")
        self.assertEqual(hecho.returncode, 0, hecho.stderr + hecho.stdout)
        self.assertIn("comprobacion", hecho.stdout)
        self.assertIn("solicitud", hecho.stdout)
        self.assertIn("diferencias: 0", hecho.stdout)
        self.assertNotIn("corrección secreta", hecho.stdout)
        self.assertNotIn("evidencia secreta", hecho.stdout)

    def test_cambio_del_orden_de_guardias_detecta_escritura_aceptada(self) -> None:
        arbol = self.mutar(
            "if (disponible is not None or pendientes and queda_trabajo(fases)",
            "if False and (disponible is not None or pendientes and queda_trabajo(fases)",
            "validacion_esperas.py",
        )
        hecho = self.correr("--kinds", "comprobacion", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("base_acepta_arbol_rechaza", hecho.stdout)
        self.assertIn("comprobacion", hecho.stdout)
        self.assertNotIn("corrección secreta", hecho.stdout)

    def test_cambio_de_mensaje_detecta_rechazo_sin_publicarlo(self) -> None:
        arbol = self.mutar(
            "la solicitud necesita id", "mensaje privado cambiado",
            "validacion_necesidades.py",
        )
        hecho = self.correr("--kinds", "solicitud", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("mensaje_transicion", hecho.stdout)
        self.assertNotIn("mensaje privado cambiado", hecho.stdout)

    def test_entrega_se_prueba_con_estado_vacio(self) -> None:
        arbol = self.raiz / "mutado"
        shutil.copytree(RAIZ / "motor", arbol / "motor")
        archivo = arbol / "motor" / "encargos" / "dominio.py"
        contenido = archivo.read_text(encoding="utf-8")
        original = 'if kind == "entrega":\n        if estado is not None:'
        self.assertIn(original, contenido)
        archivo.write_text(
            contenido.replace(original, original.replace("not None", "not None or nuevo"), 1),
            encoding="utf-8",
        )
        hecho = self.correr("--kinds", "entrega", "--arbol", str(arbol))
        self.assertEqual(hecho.returncode, 1, hecho.stderr + hecho.stdout)
        self.assertIn("base_acepta_arbol_rechaza", hecho.stdout)


if __name__ == "__main__":
    unittest.main()
