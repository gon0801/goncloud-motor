"""MOTOR 02, bloque F: el plan y la máquina pueden corregirse a media corrida."""

from __future__ import annotations

import ast
import inspect
import subprocess
import unittest
import unittest.mock
from pathlib import Path

from motor import claves, huella
from motor import machine as m
from motor.plan import Plan, parse_plan
from tests.test_no_caerse import Banco
from tests.test_puro import PLAN, entregado, ev, pol

RAIZ = Path(__file__).resolve().parent.parent


def _llamadas_con_ukey():
    """(archivo, línea, tipo de evento o None, valor de ukey) de cada `x.append(..., ukey=...)` del motor."""
    for f in sorted((RAIZ / "motor").rglob("*.py")):
        if f.name == "claves.py":
            continue
        for nodo in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(nodo, ast.Call):
                continue
            for kw in nodo.keywords:
                if kw.arg == "ukey":
                    tipo = None
                    if len(nodo.args) >= 3 and isinstance(nodo.args[2], ast.Constant):
                        tipo = nodo.args[2].value
                    yield f.name, kw.value.lineno, tipo, kw.value


SHA = "a" * 40
MAQUINA = "m" * 16


def plan_con(acepta: str = "true", prueba: str = "true") -> Plan:
    """El PLAN de las pruebas puras con otra Acepta y otra prueba real en el bloque A."""
    texto = PLAN.replace("| A.1 | algo | true |", f"| A.1 | algo | {acepta} |", 1)
    return parse_plan(texto.replace("prueba-real: true", f"prueba-real: {prueba}", 1))


def verificado(ok=True, plan=None, maquina=MAQUINA, **extra) -> object:
    plan = plan or plan_con()
    return ev(
        "Verified",
        "A",
        {
            "sha": SHA,
            "ok": ok,
            "detalle": "",
            "huella": huella.insumo_de("verified", plan.block("A"), {}),
            "huella_maquina": maquina,
            **extra,
        },
    )


def acciones(plan, evs, maquina=MAQUINA):
    snap = m.fold(plan, evs, maquina)
    return snap, m.decide(snap, pol(), 1e10)


def de_a(acs, bloque="A"):
    """Las acciones del bloque (sin avisos)."""
    return [
        a
        for a in acs
        if not isinstance(a, m.Notify) and getattr(a, "block", bloque) == bloque
    ]


def tipos(acs):
    return [type(a).__name__ for a in de_a(acs)]


class ReevaluaTest(Banco):
    def test_el_plan_corregido_verifica_el_mismo_sha_sin_agente(self):
        malo = plan_con("sin-este-comando")
        evs = entregado() + [
            verificado(ok=False, plan=malo, falta_comando=["sin-este-comando"])
        ]
        snap, _ = acciones(malo, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        bueno = plan_con("test -f README.md")
        snap, acs = acciones(bueno, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.VERIFICAR)
        self.assertEqual(de_a(acs), [m.Verify("A", SHA)])

    def test_cambiar_la_maquina_vuelve_a_verificar_el_mismo_sha(self):
        evs = entregado() + [verificado(ok=False, maquina="vieja")]
        snap, _ = acciones(plan_con(), evs, maquina="vieja")
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        snap, acs = acciones(plan_con(), evs, maquina="nueva")
        self.assertIs(m.estado(snap, "A"), m.Estado.VERIFICAR)
        self.assertEqual(tipos(acs), ["Verify"])

    def test_la_huella_de_la_maquina_sale_de_path_extra_env_y_tiene(self):
        from motor.conf import Maquina

        base = Maquina(nombre="x", conf=Path("c"))
        h = huella.de_maquina(base)
        for cambio in (
            {"path_extra": ("/opt/bin",)},
            {"env": (("A", "1"),)},
            {"tiene": ("ssh-goncloud",)},
        ):
            self.assertNotEqual(
                h, huella.de_maquina(Maquina(**{**base.__dict__, **cambio}))
            )
        otro_nombre = Maquina(**{**base.__dict__, "nombre": "y", "buzones": ("b",)})
        self.assertEqual(h, huella.de_maquina(otro_nombre), "lo demás no mide nada")

    def test_un_resultado_sin_huella_vale_igual(self):
        viejo = ev("Verified", "A", {"sha": SHA, "ok": False, "detalle": "x"})
        snap, _ = acciones(
            plan_con("otra"), entregado() + [viejo], maquina="cualquiera"
        )
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        self.assertIn(SHA, snap.blocks["A"].verified)

    def test_sin_huella_de_maquina_en_el_fold_no_se_compara_la_maquina(self):
        evs = entregado() + [verificado(ok=False, maquina="vieja")]
        snap = m.fold(plan_con(), evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)

    def test_lo_que_el_resultado_no_midio_no_lo_invalida(self):
        evs = entregado() + [verificado(ok=True)]
        otra_fila = PLAN.replace("| A.1 | algo | true |", "| A.1 | otra cosa | true |")
        snap = m.fold(parse_plan(otra_fila), evs, MAQUINA)
        self.assertIn(SHA, snap.blocks["A"].verified)

    def test_la_prueba_real_corregida_vuelve_a_probar_el_mismo_sha(self):
        vieja = plan_con(prueba="falla-en-esta-maquina")
        base = entregado() + [
            verificado(plan=vieja),
            ev("PrOpened", "A", {"numero": 1}),
            ev("Merged", "A", {"sha": SHA, "commit": "c" * 40}),
            ev(
                "Proved",
                "A",
                {
                    "sha": SHA,
                    "ok": False,
                    "detalle": "x",
                    "huella": huella.insumo_de("proved", vieja.block("A"), {}),
                    "huella_maquina": MAQUINA,
                },
            ),
        ]
        snap, acs = acciones(vieja, base)
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        corregida = plan_con(prueba="true")
        evs = base[:2] + [verificado(plan=corregida)] + base[3:]
        snap, acs = acciones(corregida, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.PROBAR)
        self.assertEqual(tipos(acs), ["Prove"])

    def test_la_receta_corregida_vuelve_a_desplegar_el_mismo_sha(self):
        texto = PLAN.replace("lane: gate", "lane: release", 1).replace(
            "prueba-real: true",
            "prueba-real: true\ndespliegue: desplegar=d.sh checklist=c.sh rollback=r.sh",
            1,
        )
        vieja = parse_plan(texto)
        nueva = parse_plan(texto.replace("d.sh", "otro.sh"))
        evs = entregado() + [
            verificado(plan=vieja),
            ev("PrOpened", "A", {"numero": 1}),
            ev("Merged", "A", {"sha": SHA, "commit": "c" * 40}),
            ev(
                "Deployed",
                "A",
                {
                    "sha": SHA,
                    "ok": False,
                    "detalle": "x",
                    "huella": huella.insumo_de("deployed", vieja.block("A"), {}),
                    "huella_maquina": MAQUINA,
                },
            ),
        ]
        snap, _ = acciones(vieja, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        evs[2] = verificado(plan=nueva)
        snap, acs = acciones(nueva, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.DESPLEGAR)
        self.assertEqual(tipos(acs), ["Deploy"])

    def test_volver_al_plan_anterior_recupera_su_resultado(self):
        v1, v2 = plan_con("test -f a"), plan_con("test -f b")
        evs = entregado() + [
            verificado(ok=False, plan=v1),
            verificado(ok=True, plan=v2),
        ]
        snap, _ = acciones(v2, evs)
        self.assertTrue(snap.blocks["A"].verified[SHA]["ok"])
        snap, _ = acciones(v1, evs)
        self.assertFalse(
            snap.blocks["A"].verified[SHA]["ok"], "el de v1 sigue en el log"
        )

    def _fx_verifica(self, acepta: str):
        d = self.daemon()
        plan = Plan.from_dict(self.plan)
        repo = plan.block("A").repo
        sha = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        d_plan = self.plan["blocks"][0]
        d_plan["rows"] = [{"id": "A.1", "contenido": "x", "acepta": acepta}]
        plan = Plan.from_dict(self.plan)
        d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
        return d, sha

    def test_verify_anota_la_huella_y_repetirlo_con_otra_acepta_entra(self):
        d, sha = self._fx_verifica("sin-este-comando-xyz")
        d.fx.verify(
            Plan.from_dict(self.plan),
            m.fold(Plan.from_dict(self.plan), []),
            m.Verify("A", sha),
        )
        self.assertEqual(
            len(self.eventos("Verified")), 1, "la misma Acepta no se repite"
        )
        v = self.eventos("Verified")[0].data
        self.assertFalse(v["ok"])
        self.assertTrue(v["huella"] and v["huella_maquina"])
        self.plan["blocks"][0]["rows"][0]["acepta"] = "true"
        plan = Plan.from_dict(self.plan)
        d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
        vs = self.eventos("Verified")
        self.assertEqual(len(vs), 2, "otra Acepta sobre el mismo SHA es otro resultado")
        self.assertNotEqual(vs[0].data["huella"], vs[1].data["huella"])
        self.assertTrue(vs[1].data["ok"])


class ClavesTest(unittest.TestCase):
    def test_ninguna_otra_parte_arma_una_a_mano(self):
        llamadas = list(_llamadas_con_ukey())
        self.assertGreater(len(llamadas), 20, "el recorrido encontró las llamadas")
        for archivo, linea, _, valor in llamadas:
            es_de_claves = (
                isinstance(valor, ast.Call)
                and isinstance(valor.func, ast.Attribute)
                and isinstance(valor.func.value, ast.Name)
                and valor.func.value.id == "claves"
            )
            self.assertTrue(es_de_claves, f"{archivo}:{linea} arma su ukey a mano")

    def test_cada_tipo_de_evento_usa_la_funcion_de_su_tipo(self):
        vistos = set()
        for archivo, linea, tipo, valor in _llamadas_con_ukey():
            if tipo is None:
                continue
            vistos.add(tipo)
            self.assertIn(tipo, claves.POR_TIPO, f"{archivo}:{linea}: {tipo} sin clave")
            self.assertEqual(
                valor.func.attr,
                claves.POR_TIPO[tipo].__name__,
                f"{archivo}:{linea}: {tipo} usa la clave de otro tipo",
            )
        self.assertEqual(vistos, set(claves.POR_TIPO), "sobra o falta un tipo")

    def test_ninguna_depende_del_reloj(self):
        fuente = inspect.getsource(claves)
        for palabra in ("import time", "datetime", "uuid", "random"):
            self.assertNotIn(palabra, fuente)
        with unittest.mock.patch("time.time", side_effect=[1.0, 2.0] * 50):
            antes = [
                claves.falla_de_efecto("p", "A", "Verify", 3),
                claves.checks_resultado("p", "A", "a" * 40, rechazo=2),
                claves.efecto_ok("p", "A", "Verify", 3),
            ]
        with unittest.mock.patch("time.time", return_value=9e9):
            despues = [
                claves.falla_de_efecto("p", "A", "Verify", 3),
                claves.checks_resultado("p", "A", "a" * 40, rechazo=2),
                claves.efecto_ok("p", "A", "Verify", 3),
            ]
        self.assertEqual(antes, despues)

    def test_lo_que_se_repite_de_verdad_se_numera(self):
        s = "a" * 40
        self.assertNotEqual(
            claves.falla_de_efecto("p", "A", "Verify", 1),
            claves.falla_de_efecto("p", "A", "Verify", 2),
        )
        self.assertNotEqual(
            claves.checks_resultado("p", "A", s, rechazo=0),
            claves.checks_resultado("p", "A", s, rechazo=1),
        )
        self.assertNotEqual(
            claves.checks_resultado("p", "A", s),
            claves.checks_resultado("p", "A", s, 0),
        )

    def test_ni_gruesas_ni_finas(self):
        s, o = "a" * 40, "b" * 40
        self.assertEqual(claves.integrado("p", "A", s), claves.integrado("p", "A", s))
        for distinta in (
            claves.integrado("q", "A", s),
            claves.integrado("p", "B", s),
            claves.integrado("p", "A", o),
        ):
            self.assertNotEqual(claves.integrado("p", "A", s), distinta)
        self.assertNotEqual(
            claves.intento_iniciado("p", "A", "implementar", 1),
            claves.intento_iniciado("p", "A", "revisar", 1),
        )
        self.assertNotEqual(
            claves.checks_esperando("p", "A", s, ("x",), False),
            claves.checks_esperando("p", "A", s, ("x",), True),
        )
        self.assertNotEqual(
            claves.checks_esperando("p", "A", s, ("x",), False),
            claves.checks_esperando("p", "A", o, ("x",), False),
        )

    def test_un_resultado_de_otro_insumo_es_otra_clave(self):
        s = "a" * 40
        for f in (claves.verificado, claves.probado, claves.desplegado):
            self.assertNotEqual(f("p", "A", s, "h1", "m1"), f("p", "A", s, "h2", "m1"))
            self.assertNotEqual(f("p", "A", s, "h1", "m1"), f("p", "A", s, "h1", "m2"))
            self.assertEqual(f("p", "A", s, "h1", "m1"), f("p", "A", s, "h1", "m1"))
            self.assertEqual(
                f("p", "A", s), f("p", "A", s, "", ""), "sin huella, como antes"
            )


if __name__ == "__main__":
    unittest.main()
