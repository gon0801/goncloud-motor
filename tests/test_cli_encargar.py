"""La CLI conserva el pedido completo y la identidad de cada entrega."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from motor import __main__ as motor_cli
from motor.encargos.dominio import prompt_responsable
from motor.encargos.registro import Registro


class CliEncargar(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "base"
        self.archivo = Path(tmp.name) / "plan.md"
        self.archivo.write_text("Plan de Orbit.\nConservar la fase dos.\n", encoding="utf-8")
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)

    def encargar(self, *extra, clave="pedido"):
        with (
            contextlib.redirect_stdout(io.StringIO()) as salida,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            codigo = motor_cli.main([
                "encargar", "--base", str(self.base), "--clave", clave, *extra,
            ])
        self.assertEqual(error.getvalue(), "")
        return codigo, json.loads(salida.getvalue())

    def test_texto_y_archivo_llegan_al_prompt_y_al_evento_con_adjunto(self):
        codigo, salida = self.encargar(
            "--texto", "Partir de la rama previa. Revisar hallazgos de integración.",
            "--archivo", str(self.archivo),
        )

        self.assertEqual(codigo, 0)
        encargo = self.reg.fold(salida["encargo"])
        esperado = (
            "Partir de la rama previa. Revisar hallazgos de integración.\n\n"
            "Plan de Orbit.\nConservar la fase dos.\n"
        )
        self.assertIn("Pedido:\n" + esperado, prompt_responsable(encargo))
        entrega = self.reg.por_ukey("entrega:pedido")
        self.assertEqual(entrega["kind"], "entrega")
        self.assertEqual(entrega["data"]["texto"], esperado)
        self.assertEqual(entrega["data"]["adjuntos"], [str(self.archivo.resolve())])

    def test_repetir_ambos_argumentos_conserva_recibo_y_eventos(self):
        argumentos = ("--texto", "Instrucción del director.", "--archivo", str(self.archivo))
        codigo, primera = self.encargar(*argumentos)
        eventos = self.reg.eventos(primera["encargo"])

        repetido, segunda = self.encargar(*argumentos)

        self.assertEqual((codigo, repetido), (0, 0))
        self.assertEqual(segunda, primera)
        self.assertEqual(self.reg.eventos(primera["encargo"]), eventos)

    def test_cambiar_texto_con_la_misma_clave_es_conflicto(self):
        codigo, primera = self.encargar(
            "--texto", "Usar la rama uno.", "--archivo", str(self.archivo),
        )
        eventos = self.reg.eventos(primera["encargo"])

        conflicto, segunda = self.encargar(
            "--texto", "Usar la rama dos.", "--archivo", str(self.archivo),
        )

        self.assertEqual(codigo, 0)
        self.assertEqual(conflicto, 2)
        self.assertEqual(segunda, {**primera, "conflicto": True})
        self.assertEqual(self.reg.eventos(primera["encargo"]), eventos)

    def test_entrega_previa_de_solo_archivo_no_se_reescribe_al_agregar_texto(self):
        codigo, previa = self.encargar("--archivo", str(self.archivo))
        entrega = self.reg.por_ukey("entrega:pedido")
        eventos = self.reg.eventos(previa["encargo"])
        prompt = prompt_responsable(self.reg.fold(previa["encargo"]))

        conflicto, salida = self.encargar(
            "--texto", "Instrucción que antes se perdía.", "--archivo", str(self.archivo),
        )

        self.assertEqual(codigo, 0)
        self.assertEqual(conflicto, 2)
        self.assertEqual(salida, {**previa, "conflicto": True})
        self.assertEqual(self.reg.por_ukey("entrega:pedido"), entrega)
        self.assertEqual(self.reg.eventos(previa["encargo"]), eventos)
        self.assertEqual(prompt_responsable(self.reg.fold(previa["encargo"])), prompt)

    def test_argumentos_individuales_conservan_texto_y_reintento(self):
        casos = (
            ("texto", ("--texto", "  Pedido del director.\n"), "  Pedido del director.\n", []),
            (
                "archivo", ("--archivo", str(self.archivo)),
                "Plan de Orbit.\nConservar la fase dos.\n", [str(self.archivo.resolve())],
            ),
            (
                "texto-vacio", ("--texto", "", "--archivo", str(self.archivo)),
                "Plan de Orbit.\nConservar la fase dos.\n", [str(self.archivo.resolve())],
            ),
        )
        for clave, argumentos, esperado, adjuntos in casos:
            with self.subTest(clave=clave):
                codigo, primera = self.encargar(*argumentos, clave=clave)
                entrega = self.reg.por_ukey(f"entrega:{clave}")
                eventos = self.reg.eventos(primera["encargo"])
                repetido, segunda = self.encargar(*argumentos, clave=clave)

                self.assertEqual((codigo, repetido), (0, 0))
                self.assertEqual(entrega["data"]["texto"], esperado)
                self.assertEqual(entrega["data"]["adjuntos"], adjuntos)
                self.assertIn("Pedido:\n" + esperado, prompt_responsable(self.reg.fold(primera["encargo"])))
                self.assertEqual(segunda, primera)
                self.assertEqual(self.reg.por_ukey(f"entrega:{clave}"), entrega)
                self.assertEqual(self.reg.eventos(primera["encargo"]), eventos)


if __name__ == "__main__":
    unittest.main()
