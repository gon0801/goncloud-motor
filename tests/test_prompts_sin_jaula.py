"""Los prompts del motor no prohiben trabajar como el usuario y el juicio
acepta una inspeccion vacia como ausente."""

import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import ahora_iso, recibir
from motor.encargos.comprobacion import (
    DefectoJuicio,
    AlcanceRevision,
    instrucciones_responsable,
    instrucciones_revision,
    validar_juicio,
)
from motor.encargos.dominio import prompt_responsable, prompt_revisor
from motor.encargos.registro import Registro

_PROHIBIDAS = (
    "fuera de este espacio se pide",
    "No tienes acceso",
    "no agregues credenciales",
    "busques credenciales",
)

_ALCANCE = AlcanceRevision(
    clase="completa",
    version="v1",
    base_version="",
    manifiesto=(("dato.txt", "h1"),),
)


class PromptsSinJaula(unittest.TestCase):
    def test_los_prompts_no_prohiben_trabajar_como_el_usuario(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            enc = reg.fold(recibir(reg, "pedido", "sin-jaula", ahora_iso())["encargo"])
            alcance = AlcanceRevision(clase="completa", version="v1", base_version="")
            textos = (
                prompt_responsable(enc),
                prompt_revisor(enc, alcance),
                instrucciones_responsable(Path(tmp) / ".interno"),
                instrucciones_revision(alcance, Path(tmp) / "juicio.json"),
            )
            for texto in textos:
                for prohibida in _PROHIBIDAS:
                    self.assertNotIn(prohibida, texto)

    def test_el_responsable_sigue_con_las_reglas_de_flujo(self):
        texto = instrucciones_responsable(Path("/espacio/.interno"))
        self.assertIn("No cambies el objetivo ni despliegues", texto)
        self.assertIn("pedido-efecto.json", texto)
        self.assertIn("repitas efectos con recibo", texto)

    def test_el_canal_nombra_solo_el_uso_real_del_motor(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            enc = reg.fold(recibir(reg, "pedido", "sin-jaula", ahora_iso())["encargo"])
            textos = (
                prompt_responsable(enc),
                instrucciones_responsable(Path(tmp) / ".interno"),
            )
            for texto in textos:
                self.assertNotIn("publicar el PR", texto)
                self.assertNotIn("un aviso", texto)
                self.assertIn("desplegado", texto)
                self.assertIn("pedido-efecto.json", texto)

    def test_el_canal_no_afirma_unicidad_y_el_prompt_conserva_la_higiene_de_secretos(self):
        texto = instrucciones_responsable(Path("/espacio/.interno"))
        self.assertNotIn("unico efecto que el propio motor", texto)
        self.assertIn("Nunca subas credenciales", texto)

    def test_el_revisor_sigue_con_su_papel(self):
        texto = instrucciones_revision(_ALCANCE, Path("/tmp/juicio.json"))
        self.assertIn("No modifiques el producto", texto)
        self.assertIn("declara el campo inspeccion", texto)


class InspeccionVacia(unittest.TestCase):
    def _aprobado(self, **extra):
        juicio = {
            "veredicto": "aprobado",
            "version": "v1",
            "evidencia": "lei dato.txt y contiene 42",
            "inspeccion_faltante": "",
            "inspeccion": {},
        }
        juicio.update(extra)
        return validar_juicio(juicio, _ALCANCE)

    def test_aprobado_con_inspeccion_vacia_es_valido(self):
        juicio = self._aprobado()
        self.assertEqual(juicio.veredicto, "aprobado")
        self.assertIsNone(juicio.inspeccion)
        self.assertEqual(juicio.inspeccion_faltante, "")

    def test_aprobado_con_inspeccion_nula_es_valido(self):
        juicio = self._aprobado(inspeccion=None)
        self.assertEqual(juicio.veredicto, "aprobado")
        self.assertIsNone(juicio.inspeccion)

    def test_instrumento_vacio_en_aprobado_cuenta_como_ausente(self):
        juicio = self._aprobado(instrumento={"causa": ""})
        self.assertEqual(juicio.veredicto, "aprobado")
        self.assertEqual(juicio.instrumento, "")

    def test_no_concluyente_con_inspeccion_real_sigue_funcionando(self):
        juicio = validar_juicio(
            {
                "veredicto": "no_concluyente",
                "version": "v1",
                "evidencia": "falta el log del job",
                "inspeccion_faltante": "el log del job jaula-linux",
                "inspeccion": {
                    "operacion": "ci.log",
                    "recurso": "ci-linux",
                    "parametros": {"pr": "309", "job": "jaula", "sha": "abc"},
                },
            },
            _ALCANCE,
        )
        self.assertEqual(juicio.veredicto, "no_concluyente")
        self.assertIsNotNone(juicio.inspeccion)
        self.assertEqual(juicio.inspeccion.operacion, "ci.log")
        self.assertEqual(juicio.inspeccion_faltante, "el log del job jaula-linux")

    def test_inspeccion_con_contenido_en_aprobado_sigue_rechazandose(self):
        with self.assertRaises(DefectoJuicio):
            self._aprobado(
                inspeccion={"operacion": "ci.log", "recurso": "ci-linux"},
            )


if __name__ == "__main__":
    unittest.main()
