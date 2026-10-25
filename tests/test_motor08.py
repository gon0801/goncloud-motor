from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from motor import claves
from motor import machine as m
from motor.criterios import Aclarar, Criterio, Expediente, Nota, Sugerencia, valorar
from motor.effects import Effects
from motor.formato import activa, puede_volver
from motor.machine import _es_falla_maquina
from motor.previa import faltantes_de_salida
from motor.store import Store
from tests.test_puro import PLAN, ev, parse_plan

def _criterio(cid, receta, conducta):
    return Criterio(cid, "v", "fila", "comportamiento", conducta, (), receta, "acepta", None, "A")
class ValorarTest(unittest.TestCase):
    def test_otra_receta_queda_en_nota(self):
        exp = Expediente((_criterio("A.1", "true", "sale bien"),), (), (), ())
        s = Sugerencia("s", "d", "A.1", "otro", "sale mal", "false")
        self.assertIsInstance(valorar(s, exp), Nota)
    def test_misma_receta_pide_aclarar(self):
        exp = Expediente((_criterio("A.1", "true", "uno"), _criterio("A.2", "true", "dos")), (), (), ())
        s = Sugerencia("s", "d", "A.1", "esc", "uno", "true")
        j = valorar(s, exp)
        self.assertIsInstance(j, Aclarar)
        self.assertEqual(set(j.criterios), {"A.1", "A.2"})
class E:
    def __init__(self, kind, data):
        self.kind, self.data = kind, data
class FormatoTest(unittest.TestCase):
    def test_incierto_no_deja_volver_y_aplicado_si(self):
        home = Path(tempfile.mkdtemp(prefix="m08-fmt-"))
        self.assertTrue(puede_volver(home, [])[0])
        activa(home)
        pedido = E("Intencion", {"id": "i1"})
        ok, motivo = puede_volver(home, [pedido])
        self.assertFalse(ok)
        self.assertIn("i1", motivo)
        dudoso = E("EfectoObservado", {"intencion": "i1", "resultado": "incierto"})
        self.assertFalse(puede_volver(home, [pedido, dudoso])[0])
        hecho = E("EfectoObservado", {"intencion": "i1", "resultado": "aplicado"})
        self.assertTrue(puede_volver(home, [pedido, dudoso, hecho])[0])
class ModuloTest(unittest.TestCase):
    def test_modulo_que_falta_no_es_falla_de_maquina(self):
        salida = "ModuleNotFoundError: No module named modulo_que_no_existe"
        self.assertEqual(faltantes_de_salida(salida), [])
        self.assertFalse(_es_falla_maquina({"ok": False, "detalle": salida}))
        tom = "cannot import name 'tomllib'"
        self.assertIn("tomllib", faltantes_de_salida(tom))
        self.assertTrue(_es_falla_maquina({"ok": False, "detalle": tom}))
class MergeNoRepiteTest(unittest.TestCase):
    def test_un_merge_aplicado_no_vuelve_a_escribir(self):
        home = Path(tempfile.mkdtemp(prefix="m08-merge-"))
        store = Store(home / "motor.db")
        plan = parse_plan(PLAN)
        sha = "a" * 40
        ident = "A:merge:" + sha + ":7"
        store.append("demo", "A", "Intencion", {"id": ident, "tipo": "merge"}, ukey=claves.intencion("demo", ident))
        store.append("demo", "A", "EfectoObservado", {"intencion": ident, "resultado": "aplicado"}, ukey=claves.efecto_observado("demo", ident, "aplicado"))
        llamadas = []
        fx = Effects(SimpleNamespace(home=home, store=store))
        fx.notify = lambda *a, **k: None
        fx._ya_mergeado = lambda *a, **k: False
        fx.cx.gh = SimpleNamespace(merge=lambda *a, **k: llamadas.append("x"))
        snap = m.fold(plan, [ev("Verified", "A", {"sha": sha, "ok": True}), ev("PrOpened", "A", {"numero": 7})])
        fx.merge(plan, snap, m.Merge(m.MergeTicket("A", sha, 7)))
        self.assertEqual(llamadas, [])
