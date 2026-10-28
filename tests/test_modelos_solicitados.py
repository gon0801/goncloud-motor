"""El director conserva los modelos pedidos aunque el servicio use otro agente."""

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from motor.encargos import cli
from motor.encargos.registro import Registro
from motor.integraciones import cli as director_cli


class ModelosSolicitados(unittest.TestCase):
    def test_pedido_explicito_prevalece_sobre_el_default_del_servicio(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            argv = [
                "encargar",
                "--base",
                str(base),
                "--director",
                "hermes",
                "--via",
                "durable",
                "--canal",
                "hermes",
                "--clave",
                "modelo-pedido",
                "--texto",
                "Muse implementa y Kimi revisa",
                "--responsable",
                "meta/muse-wonder-1.0",
                "--revisor",
                "kimi-code/k3.1-pro",
            ]
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                self.assertEqual(director_cli.main(argv), 0)
            recibo = json.loads(captured.getvalue())
            reg = Registro(base)
            self.addCleanup(reg.close)
            enc = reg.fold(recibo["encargo"])
            self.assertEqual(
                enc.modelos_solicitados,
                (
                    ("responsable", "muse", "meta/muse-wonder-1.0"),
                    ("revisor", "kimi", "kimi-code/k3.1-pro"),
                ),
            )
            elegidas = []

            def construir(agente, modelo, modo):
                def lanzar(actual, prompt, espacio, sesion):
                    elegidas.append((actual.rol, agente, modelo))
                    return [agente, modelo]

                return lanzar

            def paso(registro, hora, epoch, lanzador, **kwargs):
                actual = registro.fold(enc.id)
                self.assertEqual(actual.agentes, ("codex",))
                autor = replace(actual, rol="responsable")
                comando, _ = lanzador(autor, base / "pedido", base / "espacio", "s1")
                self.assertEqual(comando, ["muse", "meta/muse-wonder-1.0"])
                revision = replace(
                    actual,
                    rol="revisor",
                    modelos=(("responsable", "muse", "meta/muse-wonder-1.0"),),
                )
                comando, _ = lanzador(revision, base / "pedido", base / "espacio", "s2")
                self.assertEqual(comando, ["kimi", "kimi-code/k3.1-pro"])
                with patch.object(
                    cli,
                    "_disponible",
                    side_effect=lambda agente, gen: agente == "codex",
                ):
                    self.assertIsNone(
                        lanzador(autor, base / "pedido", base / "espacio", "s3")
                    )
                return []

            args = SimpleNamespace(
                agente="codex", respaldo=[], una_vez=True, umbral=30, intervalo=1
            )
            with (
                patch.object(cli, "_disponible", return_value=True),
                patch.object(cli, "_lanzador", side_effect=construir),
                patch.object(cli, "barrer", side_effect=paso),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli._supervisor(reg, args), 0)
            self.assertEqual(
                elegidas,
                [
                    ("responsable", "muse", "meta/muse-wonder-1.0"),
                    ("revisor", "kimi", "kimi-code/k3.1-pro"),
                ],
            )
            self.assertIn("meta/muse-wonder-1.0", reg.fold(enc.id).motivo)
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                self.assertEqual(director_cli.main(argv), 0)
            self.assertEqual(
                json.loads(captured.getvalue())["recibo"], recibo["recibo"]
            )
            cambiado = list(argv)
            cambiado[cambiado.index("meta/muse-wonder-1.0")] = "openai/gpt-6.1-sol"
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                director_cli.main(cambiado)
            self.assertTrue(json.loads(captured.getvalue())["conflicto"])
            self.assertEqual(len(reg.ids()), 1)
