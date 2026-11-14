"""Un hecho que el registro rechaza fuera de la frontera detiene ese encargo y dice por qué.

Decisión de David del 2026-11-13: la quinta causa de `detenido` es «fallo
interno». El reporte nombra el kind del evento y la clase de su ukey, sin
valores, y dice que se reanuda con `continuar`. Los demás encargos siguen.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo, necesidades
from motor.encargos.ciclo import (
    barrer,
    conceder_conductor,
    marcar_espera,
    ordenar,
    recibir,
)
from motor.encargos.dominio import reducir
from motor.encargos.registro import ClaveOcupada, Registro
from tests.dobles import FakeGitHub
from tests.test_flujo_revisiones import _entrega_pr, sonda_sin_aislamiento_del_host


def setUpModule():
    sonda_sin_aislamiento_del_host()


HORA = "2026-11-14T10:00:00Z"
REANUDA = "2026-11-14T10:05:00Z"
DESPUES = "2026-11-14T10:10:00Z"
EPOCH = time.mktime(time.strptime(DESPUES, "%Y-%m-%dT%H:%M:%SZ"))


class Banco:
    """Dos encargos en espera; la marca de fin de espera del malo la ocupa otro hecho."""

    def __init__(self, tmp: str):
        self.reg = Registro(Path(tmp) / "base")
        self.malo = recibir(self.reg, "pedido que se traba", "malo", HORA)["encargo"]
        self.bueno = recibir(self.reg, "pedido sano", "bueno", HORA)["encargo"]
        for ident in (self.malo, self.bueno):
            marcar_espera(self.reg, ident, "una espera con hora", REANUDA, HORA)
        self.reg.anotar(
            self.malo,
            {"kind": "procedimiento", "data": {"nota": "dato privado", "hora": HORA}},
            f"plazo:{self.malo}:{REANUDA}",
        )

    def vuelta(self) -> list[dict]:
        return barrer(self.reg, DESPUES, EPOCH, None)

    def eventos(self, ident: str, kind: str) -> list[dict]:
        return [e for e in self.reg.eventos(ident) if e["kind"] == kind]


class FalloInterno(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        ciclo._RACHA_BARRIDO.clear()
        self.b = Banco(self.tmp)
        self.addCleanup(self.b.reg.close)

    def test_una_escritura_rechazada_en_un_paso_detiene_el_encargo_con_la_causa(self):
        informes = self.b.vuelta()

        malo = self.b.reg.fold(self.b.malo)
        self.assertEqual(malo.estado, "detenido")
        for dicho in ("fallo interno", "plazo", "plazo:*", "continuar"):
            self.assertIn(dicho, malo.motivo)
        for privado in (self.b.malo, "dato privado", REANUDA):
            self.assertNotIn(privado, malo.motivo)
        (fallo,) = self.b.eventos(self.b.malo, "fallo_interno")
        self.assertEqual(
            {k: v for k, v in fallo["data"].items() if k != "hora"},
            {"rechazo": "ajena", "kind": "plazo", "ukey": "plazo:*", "campos": "@kind,nota"},
        )
        self.assertEqual({i["encargo"] for i in informes}, {self.b.malo, self.b.bueno})
        self.assertEqual(self.b.reg.fold(self.b.bueno).estado, "recibido")
        self.assertEqual(self.b.eventos(self.b.bueno, "fallo_interno"), [])

    def test_detenido_no_vuelve_a_intentarlo_hasta_continuar(self):
        for _ in range(3):
            self.b.vuelta()

        self.assertEqual(len(self.b.eventos(self.b.malo, "fallo_interno")), 1)
        self.assertEqual(self.b.eventos(self.b.malo, "instrumento_fallo"), [])
        self.assertTrue(necesidades.detenida_ahora(self.b.reg, self.b.malo))

        salida = ordenar(self.b.reg, self.b.malo, "continuar", "sigue-1", DESPUES)

        self.assertFalse(salida.get("rechazada"), salida)
        malo = self.b.reg.fold(self.b.malo)
        self.assertEqual(malo.estado, "recibido")
        self.assertNotIn("fallo interno", malo.motivo)
        self.assertFalse(necesidades.detenida_ahora(self.b.reg, self.b.malo))

    def test_un_proceso_vivo_se_cierra_antes_de_quedar_detenido(self):
        enc = replace(self.b.reg.fold(self.b.bueno), estado="recibido", reserva=True)
        fallo = {
            "kind": "fallo_interno",
            "data": {"rechazo": "ilegal", "kind": "estrategia", "ukey": "estrategia:*", "hora": HORA},
        }

        deteniendo = reducir(enc, fallo)
        detenido = reducir(
            replace(deteniendo, reserva=False),
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": DESPUES}},
        )

        self.assertEqual(deteniendo.estado, "deteniendo")
        self.assertTrue(deteniendo.intento_interrumpido)
        self.assertEqual(detenido.estado, "detenido")
        self.assertIn("estrategia:*", detenido.motivo)
        self.assertIn("continuar", detenido.motivo)

    def test_un_encargo_cerrado_no_cambia_de_causa_y_el_barrido_sigue(self):
        ordenar(self.b.reg, self.b.malo, "cancelar", "fin-1", HORA)
        self.b.reg.anotar(
            self.b.malo,
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": HORA}},
            f"ausente:{self.b.malo}",
        )
        original = ciclo._limpiar_encargo_cerrado
        rechazo = self.b.reg.anotar

        def limpiar(registro, enc, hora):
            if enc.id == self.b.malo:
                rechazo(enc.id, {"kind": "plazo", "data": {"hora": hora}}, f"plazo:{enc.id}:{REANUDA}")
            return original(registro, enc, hora)

        with mock.patch.object(ciclo, "_limpiar_encargo_cerrado", side_effect=limpiar):
            informes = self.b.vuelta()

        self.assertEqual(self.b.reg.fold(self.b.malo).estado, "cancelado")
        self.assertEqual(self.b.eventos(self.b.malo, "fallo_interno"), [])
        (contenido,) = self.b.eventos(self.b.malo, "instrumento_fallo")
        self.assertEqual(contenido["data"]["clase"], "barrido:ClaveOcupada")
        self.assertIn(self.b.bueno, {i["encargo"] for i in informes})


class ElPrAbiertoRechazado(unittest.TestCase):
    """GitHub abrió el PR y el registro rechaza el hecho que lo guarda: no es un fallo de GitHub."""

    def test_detiene_el_encargo_y_continuar_sigue_con_el_mismo_pr(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "pr-rechazado", FakeGitHub)
            (r.espacio / "saludo.txt").write_text("hola")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
            r.incorporar()
            anotar = r.reg.anotar
            rechazos = []

            def rechaza_el_pr_abierto(encargo, evento, ukey):
                if evento["data"].get("paso") == "pr_abierto" and not rechazos:
                    rechazos.append(ClaveOcupada(0, ukey, ("sha",), evento["kind"]))
                    raise rechazos[-1]
                return anotar(encargo, evento, ukey)

            with mock.patch.object(r.reg, "anotar", side_effect=rechaza_el_pr_abierto):
                for _ in range(2):
                    barrer(r.reg, ciclo.ahora_iso(), time.time(), None, gh=vuelta.gh)

            enc = r.fold()
            self.assertEqual(len(rechazos), 1)
            self.assertEqual(enc.estado, "detenido")
            self.assertEqual(
                enc.motivo,
                "fallo interno: el registro rechazó el evento entrega_externa "
                "(entrega:*) porque su clave ya guarda otro hecho; se reanuda con continuar",
            )
            self.assertEqual(len(r.eventos("fallo_interno")), 1)
            self.assertEqual(r.eventos("instrumento_fallo"), [])

            ordenar(r.reg, r.enc, "continuar", "sigue-pr", ciclo.ahora_iso())
            barrer(r.reg, ciclo.ahora_iso(), time.time(), None, gh=vuelta.gh)

            prs = json.loads((vuelta.gh.d / "github.json").read_text())["prs"]
            pasos = [e["data"]["paso"] for e in r.eventos("entrega_externa")]
            self.assertEqual(len(prs), 1)
            self.assertEqual(r.fold().pr_numero, 1)
            self.assertEqual(pasos[:4], ["commit", "push", "pr_abierto", "pr_listo"])
            self.assertEqual(pasos.count("pr_abierto"), 1)
            self.assertEqual(r.eventos("instrumento_fallo"), [])


class LaFronteraResponde(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.b = Banco(self.tmp)
        self.addCleanup(self.b.reg.close)

    def test_una_reserva_que_el_registro_rechaza_no_concede_el_conductor(self):  # no-roja-en-base: la frontera responde igual que en la base; fija que el anotar estricto no la cambia
        salida = conceder_conductor(self.b.reg, self.b.bueno, "tok", "host", DESPUES)

        self.assertEqual(salida, {"concedido": False})
        self.assertEqual(self.b.eventos(self.b.bueno, "fallo_interno"), [])

    def test_una_orden_ilegal_queda_rechazada_con_su_motivo(self):  # no-roja-en-base: la frontera responde igual que en la base; fija que el anotar estricto no la cambia
        salida = ordenar(self.b.reg, self.b.bueno, "continuar", "sigue-2", DESPUES)

        self.assertTrue(salida["rechazada"])
        self.assertIn("continuar espera el estado detenido", salida["motivo"])
        self.assertEqual(self.b.reg.fold(self.b.bueno).estado, "esperando_reintento")


if __name__ == "__main__":
    unittest.main()
