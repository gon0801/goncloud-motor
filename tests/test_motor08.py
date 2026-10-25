from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from motor import claves
from motor import machine as m
from motor.criterios import Aclarar, Criterio, Expediente, Nota, Sugerencia, valorar
from motor.effects import Effects
from motor.formato import activa, puede_volver
from motor.machine import _es_falla_maquina
from motor.previa import faltantes_de_salida
from motor.store import Store
from tests.test_puro import PLAN, entregado, ev, parse_plan, pol


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
    def test_peligro_sin_criterio_pide_aclarar(self):
        j = valorar(
            Sugerencia("s", "revisor", None, "produccion", "expone token", "test ! -f token"),
            Expediente((), (), (), ()),
        )
        self.assertIsInstance(j, Aclarar)
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
        vacio = E("EfectoObservado", {"intencion": "i1", "resultado": ""})
        self.assertFalse(puede_volver(home, [pedido, vacio])[0])
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


class AclararLlegaADecideTest(unittest.TestCase):
    """Un Aclarar juzgado no vuelve a ser una nota: decide arranca antes del efecto."""

    def _reviewed(self, bloqueantes):
        d = Path(tempfile.mkdtemp(prefix="m08-acl-"))
        self.addCleanup(shutil.rmtree, d, True)
        sha = "a" * 40
        (d / "outcome.json").write_text(
            json.dumps(
                {"exit": 0, "timed_out": False, "no_progress": False, "tail": ""}
            )
        )
        (d / "result.json").write_text(
            json.dumps(
                {
                    "sha": sha,
                    "veredicto": "CAMBIOS",
                    "notas": [],
                    "bloqueantes": bloqueantes,
                }
            )
        )
        eventos = []
        fx = Effects(
            SimpleNamespace(
                store=SimpleNamespace(append=lambda *a, **k: eventos.append(a))
            )
        )
        fx._proveedor = lambda mod: ""
        r = fx.collect(
            SimpleNamespace(id="demo", block=lambda b: SimpleNamespace(id=b)),
            None,
            "A",
            SimpleNamespace(dir=str(d), role="revisar", sha=sha, modelo="opus", n=1),
        )
        self.assertEqual(r["kind"], "veredicto")
        reviewed = next(a[3] for a in eventos if a[2] == "Reviewed")
        return sha, reviewed

    def _listo_para_merge(self, sha, reviewed):
        return entregado(sha=sha) + [
            ev("Verified", "A", {"sha": sha, "ok": True}),
            ev("PrOpened", "A", {"numero": 7}),
            ev("MarkedReady", "A", {"sha": sha}),
            ev("ChecksResult", "A", {"sha": sha, "green": True}),
            ev("Reviewed", "A", reviewed),
        ]

    def test_el_peligro_abre_aclarar_antes_del_merge(self):
        sha, reviewed = self._reviewed(
            [
                {
                    "texto": "en produccion expone token",
                    "repro": "test ! -f token",
                    "arreglo": "quitarlo",
                },
                {"texto": "estilo", "repro": ""},
            ]
        )
        self.assertTrue(reviewed.get("aclarar"))
        self.assertEqual(
            reviewed["aclarar"][0]["mandato"],
            "caso concreto pertinente que la receta vigente no cubre",
        )
        self.assertNotIn("token", " ".join(reviewed["notas"]))
        self.assertTrue(
            any("asesoria, no es una orden" in n for n in reviewed["notas"])
        )
        ahora = time.time()
        acciones = m.decide(
            m.fold(parse_plan(PLAN), self._listo_para_merge(sha, reviewed)),
            pol(),
            ahora,
        )
        arranques = [
            a for a in acciones if isinstance(a, m.StartAttempt) and a.block == "A"
        ]
        self.assertEqual([a.modalidad for a in arranques], ["aclarar"])
        self.assertEqual(arranques[0].role, "implementar")
        self.assertTrue(any("Aclarar:" in x for x in arranques[0].feedback))
        self.assertFalse(
            any(isinstance(a, m.Merge) and a.ticket.block == "A" for a in acciones)
        )
        base = self._listo_para_merge(sha, reviewed)
        inicio = ev(
            "AttemptStarted",
            "A",
            {
                "n": 2,
                "role": "implementar",
                "modelo": "sonnet",
                "proc": {},
                "dir": "d2",
                "sha": sha,
                "modalidad": "aclarar",
            },
        )
        for kind in ("cuota", "auth", "perdido", "error", "sin_resultado"):
            with self.subTest(kind=kind):
                falla = ev(
                    "AttemptEnded",
                    "A",
                    {
                        "n": 2,
                        "role": "implementar",
                        "outcome": {"kind": kind},
                    },
                )
                snap = m.fold(parse_plan(PLAN), base + [inicio, falla])
                self.assertTrue(snap.blocks["A"].aclarar)
                self.assertFalse(
                    m._cuenta_falla(snap.blocks["A"].attempts[-1], snap.blocks["A"])
                )
                siguientes = m.decide(snap, pol(), ahora)
                self.assertFalse(
                    any(
                        isinstance(a, m.Merge) and a.ticket.block == "A"
                        for a in siguientes
                    )
                )
                self.assertIsInstance(m.merge_gate(snap.blocks["A"]), str)
                recuperado = m.decide(snap, pol(), falla.ts + 4000)
                self.assertTrue(
                    any(
                        isinstance(a, m.StartAttempt)
                        and a.block == "A"
                        and a.modalidad == "aclarar"
                        for a in recuperado
                    )
                )
        despues = base + [
            ev(
                "AttemptStarted",
                "A",
                {
                    "n": 2,
                    "role": "implementar",
                    "modelo": "sonnet",
                    "proc": {},
                    "dir": "d2",
                    "sha": sha,
                    "modalidad": "aclarar",
                },
            ),
            ev(
                "AttemptEnded",
                "A",
                {
                    "n": 2,
                    "role": "implementar",
                    "outcome": {
                        "kind": "sin_cambios",
                        "aclaracion": {
                            "resuelta": True,
                            "fundamento": "la excepcion vigente cubre el caso",
                            "evidencia": "criterio A.1",
                        },
                    },
                },
            ),
        ]
        sin_evidencia = ev(
            "AttemptEnded",
            "A",
            {
                "n": 2,
                "role": "implementar",
                "outcome": {"kind": "sin_cambios"},
            },
        )
        snap = m.fold(parse_plan(PLAN), base + [inicio, sin_evidencia])
        self.assertTrue(snap.blocks["A"].aclarar)
        self.assertFalse(
            any(
                isinstance(a, m.Merge) and a.ticket.block == "A"
                for a in m.decide(snap, pol(), ahora)
            )
        )
        self.assertEqual(
            m.bloqueo(
                snap,
                pol(),
                ahora,
                snap.blocks["A"],
                "implementar",
                "sonnet",
                pol().implementadores,
            )[0],
            "aclaracion",
        )
        nota = ev("Note", "A", {"texto": "criterio aclarado en el plan"})
        snap = m.fold(parse_plan(PLAN), base + [inicio, sin_evidencia, nota])
        self.assertEqual(
            m.bloqueo(
                snap,
                pol(),
                ahora,
                snap.blocks["A"],
                "implementar",
                "sonnet",
                pol().implementadores,
            )[0],
            "",
        )
        siguientes = m.decide(m.fold(parse_plan(PLAN), despues), pol(), ahora)
        self.assertTrue(
            any(isinstance(a, m.Merge) and a.ticket.block == "A" for a in siguientes)
        )
        self.assertFalse(
            any(
                isinstance(a, m.StartAttempt)
                and a.block == "A"
                and a.modalidad == "aclarar"
                for a in siguientes
            )
        )

    def test_una_nota_no_abre_aclarar(self):
        sha, reviewed = self._reviewed([{"texto": "estilo", "repro": ""}])
        self.assertNotIn("aclarar", reviewed)
        acciones = m.decide(
            m.fold(parse_plan(PLAN), self._listo_para_merge(sha, reviewed)),
            pol(),
            time.time(),
        )
        self.assertFalse(
            any(
                isinstance(a, m.StartAttempt)
                and a.block == "A"
                and a.modalidad == "aclarar"
                for a in acciones
            )
        )
        self.assertTrue(
            any(isinstance(a, m.Merge) and a.ticket.block == "A" for a in acciones)
        )

    def test_no_borra_el_trabajo_de_un_aclarador_vivo(self):
        plan = parse_plan(PLAN.split("## Bloque C")[0])
        sha = "a" * 40
        reviewed = {
            "sha": sha,
            "veredicto": "CAMBIOS",
            "bloqueantes": [],
            "aclarar": [{"id": "peligro", "mandato": "comprobar el peligro"}],
        }
        base = entregado(sha=sha) + [
            ev("Verified", "A", {"sha": sha, "ok": True}),
            ev("Merged", "A", {"sha": sha, "commit": sha}),
            ev("Proved", "A", {"sha": sha, "ok": True}),
        ]
        pendiente = ev("Reviewed", "A", reviewed)
        with self.subTest(caso="revision_vieja"):
            vieja = ev("Reviewed", "A", {**reviewed, "sha": "b" * 40})
            snap = m.fold(plan, base + [vieja])
            acciones = m.decide(snap, pol(), time.time())
            self.assertFalse(snap.blocks["A"].aclarar)
            self.assertFalse(any(isinstance(a, m.StartAttempt) for a in acciones))
        with self.subTest(caso="arranque_y_cierre"):
            snap = m.fold(plan, base + [pendiente])
            acciones = m.decide(snap, pol(), time.time())
            self.assertTrue(
                any(
                    isinstance(a, m.StartAttempt) and a.modalidad == "aclarar"
                    for a in acciones
                )
            )
            self.assertFalse(
                any(
                    isinstance(a, m.Notify) and a.template in ("entregado", "terminado")
                    for a in acciones
                )
            )
        activo = base + [
            pendiente,
            ev(
                "AttemptStarted",
                "A",
                {
                    "n": 2,
                    "role": "implementar",
                    "modelo": "sonnet",
                    "proc": {},
                    "dir": "d2",
                    "sha": sha,
                    "modalidad": "aclarar",
                },
            ),
            ev("NoticeQueued", "", {"key": "terminado:demo"}),
        ]
        snap = m.fold(plan, activo)
        with self.subTest(caso="limpieza_con_consumidor"):
            self.assertNotEqual(m.estado(snap, "A"), m.Estado.HECHO)
            self.assertFalse(
                any(isinstance(a, m.Limpia) for a in m.decide(snap, pol(), time.time()))
            )
        with (
            self.subTest(caso="efecto_no_borra"),
            tempfile.TemporaryDirectory(prefix="m08-no-borra-") as tmp,
        ):
            home = Path(tmp)
            trabajo = home / "wt" / "demo" / "A"
            trabajo.mkdir(parents=True)
            archivo = trabajo / "trabajo.txt"
            archivo.write_text("trabajo del aclarador")
            ws = SimpleNamespace(
                espejo=lambda repo: home,
                rama=lambda *a: "rama",
                rama_wip=lambda *a: "foto",
            )
            fx = Effects(
                SimpleNamespace(
                    home=home, ws=ws, store=SimpleNamespace(append=lambda *a, **k: None)
                )
            )
            with patch(
                "motor.effects.git",
                return_value=SimpleNamespace(stdout="", returncode=0),
            ):
                fx.limpia(plan, snap, m.Limpia(plan.id))
            self.assertTrue(archivo.exists())
        fin = ev(
            "AttemptEnded",
            "A",
            {
                "n": 2,
                "role": "implementar",
                "outcome": {
                    "kind": "sin_cambios",
                    "aclaracion": {
                        "resuelta": True,
                        "fundamento": "la excepcion vigente cubre el caso",
                        "evidencia": "criterio A.1",
                    },
                },
            },
        )
        with self.subTest(caso="resuelto_permite_limpiar"):
            snap = m.fold(plan, activo + [fin])
            self.assertEqual(m.estado(snap, "A"), m.Estado.HECHO)
            self.assertTrue(
                any(isinstance(a, m.Limpia) for a in m.decide(snap, pol(), time.time()))
            )
