"""Una respuesta grande no se declara resuelta hasta entregar su archivo publicado."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import capacidades as c
from motor.encargos import necesidades as n
from motor.encargos.registro import Registro

HORA = "2026-10-29T22:00:00Z"


class PublicacionGrande(unittest.TestCase):
    def test_falla_la_publicacion_deja_pendiente_y_recupera_sin_repetir_la_consulta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reg = Registro(root / "base")
            self.addCleanup(reg.close)
            (root / "fuente.db").touch()
            (reg.base / "capacidades.json").write_text(
                json.dumps(
                    {"recursos": {"fuente": {"tipo": "sqlite", "ruta": str(root / "fuente.db")}}}
                )
            )
            for kind, data in (
                ("entrega", {"id": "enc", "clave": "pedido", "huella": "h", "texto": "Consultar datos"}),
                ("fases", {"fases": [{"id": "fase1", "resultado": "datos"}]}),
                ("reserva", {"conductor": "intento1", "generacion": 1}),
                ("intento_abierto", {"id": "intento1", "pid": os.getpid(), "inicio": "test"}),
            ):
                reg.anotar(
                    "enc",
                    {"kind": kind, "data": {**data, "hora": HORA}},
                    kind,
                )
            enc = reg.fold("enc")
            espacio, directorio = root / "espacio", root / "intento"
            n.preparar(espacio, enc, "intento1", c.cargar(reg.base))
            peticion = espacio / n.BUZON / n.PETICIONES / "sol1.json"
            peticion.write_text(
                json.dumps(
                    {
                        "id": "sol1",
                        "encargo": enc.id,
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "generacion": enc.generacion,
                        "conductor": enc.conductor,
                        "operacion": c.CONSULTAR,
                        "recurso": "fuente",
                        "motivo": "consulta de ensayo",
                        "parametros": {"sql": "SELECT contenido FROM dato"},
                    }
                )
            )
            datos = {"columnas": ["contenido"], "filas": [["x" * 4000] for _ in range(100)]}
            destino = espacio / n.ENTREGAS / "sol1"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text("archivo que ocupa el destino de la entrega")
            ejecuciones = []

            def herramienta(registro, encargo, sol, recursos, esp, dir_, autorizado, hora):
                ejecuciones.append(sol.id)
                host = n._destino_host(Path(dir_), sol.id)
                host.mkdir(parents=True, exist_ok=True)
                (host / "resultado.json").write_text(
                    json.dumps({"estado": "ok", "datos": datos, "evidencia": {}})
                )
                return {"estado": "ok", "datos": datos, "evidencia": {}}

            with mock.patch.object(n, "_ejecutar", side_effect=herramienta):
                primera = n.atender(reg, enc, espacio, directorio, HORA)
                bloqueada = json.loads(
                    (espacio / n.BUZON / n.RESPUESTAS / "sol1.json").read_text()
                )
                self.assertEqual(primera.get("pendientes"), 1)
                self.assertEqual(primera.get("atendidas"), 0)
                self.assertEqual(bloqueada["estado"], "pendiente")
                self.assertIn("no se pudo publicar el archivo de datos", bloqueada["motivo"])
                self.assertIn(str(destino), bloqueada["motivo"])
                self.assertEqual(reg.fold("enc").solicitudes[0].estado, "pendiente")
                self.assertTrue(peticion.is_file())
                self.assertFalse((destino / "datos.json").is_file())
                self.assertEqual(ejecuciones, ["sol1"])

                destino.unlink()
                segunda = n.atender(reg, reg.fold("enc"), espacio, directorio, HORA)
                entregada = json.loads(
                    (espacio / n.BUZON / n.RESPUESTAS / "sol1.json").read_text()
                )
                self.assertEqual(segunda.get("atendidas"), 1)
                self.assertEqual(entregada["estado"], "resuelta")
                self.assertEqual(entregada.get("datos_archivo"), str(destino / "datos.json"))
                publicadas = json.loads((destino / "datos.json").read_text())
                self.assertEqual(len(publicadas["filas"]), 100)
                self.assertTrue(peticion.with_suffix(".hecha").is_file())
                self.assertEqual(reg.fold("enc").solicitudes[0].estado, "resuelta")
                self.assertEqual(ejecuciones, ["sol1"])


if __name__ == "__main__":
    unittest.main()
