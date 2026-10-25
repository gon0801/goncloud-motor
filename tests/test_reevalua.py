"""MOTOR 02, bloque F: el plan y la máquina pueden corregirse a media corrida."""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import unittest
import unittest.mock
from pathlib import Path

from motor import claves, huella
from motor import machine as m
from motor.conf import RepoConf
from motor.diagnostico import Causa, diagnostico, encargo
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

    def _espera_aparte(self, llama, kind: str, n: int = 1):
        import time

        for _ in range(300):
            llama()
            if len(self.eventos(kind)) >= n:
                return
            time.sleep(0.1)
        self.fail(f"no llegó {kind} aparte")

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
        self._espera_aparte(
            lambda: d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha)), "Verified"
        )
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
        self._espera_aparte(
            lambda: d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha)),
            "Verified",
            2,
        )
        vs = self.eventos("Verified")
        self.assertEqual(len(vs), 2, "otra Acepta sobre el mismo SHA es otro resultado")
        self.assertNotEqual(vs[0].data["huella"], vs[1].data["huella"])
        self.assertTrue(vs[1].data["ok"])

    # ------------------------------------------------------------------ F.2: prove y deploy

    def _merged(self, plan, campo, ok, **extra):
        """Entrega ya mergeada y con su último paso (proved | deployed) fallando."""
        evs = entregado() + [
            verificado(plan=plan),
            ev("PrOpened", "A", {"numero": 1}),
            ev("Merged", "A", {"sha": SHA, "commit": "c" * 40}),
        ]
        kind = {"proved": "Proved", "deployed": "Deployed"}[campo]
        return evs + [
            ev(
                kind,
                "A",
                {
                    "sha": SHA,
                    "ok": ok,
                    "detalle": "x",
                    "huella": huella.insumo_de(campo, plan.block("A"), {}),
                    "huella_maquina": MAQUINA,
                    **extra,
                },
            )
        ]

    def _plan_release(self, desplegar="d.sh"):
        texto = PLAN.replace("lane: gate", "lane: release", 1).replace(
            "prueba-real: true",
            f"prueba-real: true\ndespliegue: desplegar={desplegar} checklist=c.sh rollback=r.sh",
            1,
        )
        return parse_plan(texto)

    def _espera(self, plan, evs, paso):
        snap, acs = acciones(plan, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        self.assertNotIn("StartAttempt", tipos(acs), "el bloque no vuelve al agente")
        d = diagnostico(snap, "A", pol(), 1e10)
        self.assertIs(d.causa, Causa.FALTA_COMANDO)
        self.assertTrue(d.atorado)
        self.assertIn(paso, d.motivo)
        self.assertIn("sin-este-comando", d.motivo)
        (aviso,) = [
            a for a in acs if isinstance(a, m.Notify) and a.template == "atorado"
        ]
        despues = evs + [ev("NoticeQueued", "A", {"key": aviso.key})]
        _, acs = acciones(plan, despues)
        self.assertEqual(
            [a for a in acs if isinstance(a, m.Notify) and a.template == "atorado"],
            [],
            "avisa una vez",
        )

    def test_la_prueba_real_que_pide_un_comando_que_falta_no_vuelve_al_agente(self):
        plan = plan_con(prueba="sin-este-comando --x")
        evs = self._merged(plan, "proved", False, falta_comando=["sin-este-comando"])
        self._espera(plan, evs, "la prueba real")

    def test_el_despliegue_que_pide_un_comando_que_falta_no_vuelve_al_agente(self):
        plan = self._plan_release()
        evs = self._merged(plan, "deployed", False, falta_comando=["sin-este-comando"])
        self._espera(plan, evs, "el despliegue")

    def test_la_verificacion_tampoco_vuelve_al_agente(self):
        plan = plan_con("sin-este-comando")
        evs = entregado() + [
            verificado(plan=plan, ok=False, falta_comando=["sin-este-comando"])
        ]
        self._espera(plan, evs, "la verificación")

    def test_una_prueba_real_que_falla_sin_falta_de_comando_si_vuelve_al_agente(self):
        plan = plan_con(prueba="false")
        evs = self._merged(plan, "proved", False, falta_comando=[])
        _, acs = acciones(plan, evs)
        self.assertIn("StartAttempt", tipos(acs))

    def test_el_bloque_espera_a_que_cambie_el_plan_o_la_maquina(self):
        plan = plan_con(prueba="sin-este-comando")
        evs = self._merged(plan, "proved", False, falta_comando=["sin-este-comando"])
        self.assertNotIn("StartAttempt", tipos(acciones(plan, evs)[1]))
        corregido = plan_con(prueba="true")
        snap, acs = acciones(corregido, evs)
        self.assertEqual(tipos(acs), ["Prove"], "el plan corregido prueba el mismo SHA")
        snap, acs = acciones(plan, evs, maquina="otra")
        self.assertEqual(
            tipos(acs), ["Verify"], "otra máquina repite desde el primer paso"
        )

    def _fx_con_mergeado(self, plan_dict):
        d = self.daemon()
        plan = Plan.from_dict(plan_dict)
        repo = plan.block("A").repo
        sha = subprocess.run(
            ["git", "rev-parse", "main"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        evs = [
            ev(
                "AttemptStarted",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "glm",
                    "proc": {},
                    "dir": "d",
                },
            ),
            ev(
                "AttemptEnded",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "outcome": {"kind": "entregado", "sha": sha},
                },
            ),
            ev("Merged", "A", {"sha": sha, "commit": sha}),
        ]
        return d, plan, sha, m.fold(plan, evs)

    def test_prove_anota_el_comando_que_falta(self):
        self.plan["blocks"][0]["prueba_real"] = "sin-este-comando-xyz --x"
        d, plan, sha, snap = self._fx_con_mergeado(self.plan)
        self._espera_aparte(lambda: d.fx.prove(plan, snap, m.Prove("A", sha)), "Proved")
        (p,) = self.eventos("Proved")
        self.assertFalse(p.data["ok"])
        self.assertEqual(p.data["falta_comando"], ["sin-este-comando-xyz"])

    def test_deploy_anota_el_comando_que_falta(self):
        self.plan["blocks"][0]["lane"] = "release"
        self.plan["blocks"][0]["release"] = {
            "desplegar": "-c sin-este-comando-xyz",
            "checklist": "-c true",
            "rollback": "-c true",
        }
        d, plan, sha, snap = self._fx_con_mergeado(self.plan)
        self._espera_aparte(
            lambda: d.fx.deploy(plan, snap, m.Deploy("A", sha)), "Deployed"
        )
        (r,) = self.eventos("Deployed")
        self.assertFalse(r.data["ok"])
        self.assertEqual(r.data["falta_comando"], ["sin-este-comando-xyz"])

    def test_deploy_tras_fallo_y_rollback_vuelve_a_desplegar_con_el_plan_corregido(
        self,
    ):
        prod = self.tmp / "prod"
        bloque = self.plan["blocks"][0]
        bloque["lane"] = "release"
        bloque["release"] = {
            "desplegar": f"-c 'sin-este-comando-xyz && echo nuevo > {prod}'",
            "checklist": f"-c 'test -f {prod}'",
            "rollback": f"-c 'echo viejo > {prod}'",
        }
        d = self.daemon()
        plan = Plan.from_dict(self.plan)
        sha = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=plan.block("A").repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        base = [
            ev(
                "AttemptStarted",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "glm",
                    "proc": {},
                    "dir": "d",
                },
            ),
            ev(
                "AttemptEnded",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "outcome": {"kind": "entregado", "sha": sha},
                },
            ),
            ev("Merged", "A", {"sha": sha, "commit": sha}),
        ]
        self._espera_aparte(
            lambda: d.fx.deploy(plan, m.fold(plan, base), m.Deploy("A", sha)),
            "Deployed",
        )
        primero = self.eventos("Deployed")[-1].data
        self.assertFalse(primero["ok"])
        self.assertEqual(primero["falta_comando"], ["sin-este-comando-xyz"])
        self.assertEqual(prod.read_text(encoding="utf-8").strip(), "viejo")
        bloque["release"]["desplegar"] = f"-c 'echo nuevo > {prod}'"
        corregido = Plan.from_dict(self.plan)
        guardados = [
            e for e in d.store.events("demo") if e.kind in ("DeployStarted", "Deployed")
        ]
        snap = m.fold(corregido, base + guardados)
        self.assertIs(m.estado(snap, "A"), m.Estado.DESPLEGAR)
        self._espera_aparte(
            lambda: d.fx.deploy(corregido, snap, m.Deploy("A", sha)), "Deployed", 2
        )
        segundo = self.eventos("Deployed")[-1].data
        self.assertTrue(segundo["ok"])
        self.assertNotIn("ya estaba desplegado", segundo["detalle"])
        self.assertEqual(prod.read_text(encoding="utf-8").strip(), "nuevo")

    def test_deploy_cortado_sin_deployed_ya_no_toma_el_atajo(self):
        prod = self.tmp / "prod"
        prod.write_text("viejo\n", encoding="utf-8")
        bloque = self.plan["blocks"][0]
        bloque["lane"] = "release"
        bloque["release"] = {
            "desplegar": f"-c 'echo nuevo > {prod}'",
            "checklist": f"-c 'test -f {prod}'",
            "rollback": "-c true",
        }
        d, plan, sha, _snap = self._fx_con_mergeado(self.plan)
        evs = [
            ev(
                "AttemptStarted",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "glm",
                    "proc": {},
                    "dir": "d",
                },
            ),
            ev(
                "AttemptEnded",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "outcome": {"kind": "entregado", "sha": sha},
                },
            ),
            ev("Merged", "A", {"sha": sha, "commit": sha}),
            ev("DeployStarted", "A", {"sha": sha}),
        ]
        aparte_viejo = self.tmp / "aparte-viejo"
        aparte_viejo.mkdir()
        (aparte_viejo / "desplegando").write_text(sha, encoding="utf-8")
        d.store.append(
            "demo",
            "A",
            "DeployStarted",
            {"sha": sha, "dir": str(aparte_viejo), "proc": {}, "n": 1},
        )
        self._espera_aparte(
            lambda: d.fx.deploy(plan, m.fold(plan, evs), m.Deploy("A", sha)),
            "Deployed",
        )
        (r,) = self.eventos("Deployed")
        self.assertTrue(r.data["ok"])
        self.assertNotIn("ya estaba desplegado", r.data["detalle"])
        self.assertEqual(prod.read_text(encoding="utf-8").strip(), "nuevo")

    def test_prove_que_pasa_no_trae_comandos_faltantes(self):
        d, plan, sha, snap = self._fx_con_mergeado(self.plan)
        self._espera_aparte(lambda: d.fx.prove(plan, snap, m.Prove("A", sha)), "Proved")
        (p,) = self.eventos("Proved")
        self.assertEqual((p.data["ok"], p.data["falta_comando"]), (True, []))


def carga(plan: Plan, version: int, **extra):
    return ev(
        "PlanLoaded", "", {"plan": plan.to_dict(), "por": "t", "version": version}
    )


def sin_bloque_c() -> Plan:
    texto = PLAN.split("## Bloque C")[0]
    return parse_plan(texto)


def hecho_en(plan: Plan, version: int = 1) -> list:
    """El bloque A entregado, verificado, mergeado y probado con lo que el plan pedía."""
    b = plan.block("A")
    return [
        carga(plan, version),
        *entregado(),
        ev(
            "Verified",
            "A",
            {
                "sha": SHA,
                "ok": True,
                "detalle": "",
                "huella": huella.insumo_de("verified", b, {}),
            },
        ),
        ev("PrOpened", "A", {"numero": 1}),
        ev("Merged", "A", {"sha": SHA, "commit": "c" * 40}),
        ev(
            "Proved",
            "A",
            {
                "sha": SHA,
                "ok": True,
                "detalle": "",
                "huella": huella.insumo_de("proved", b, {}),
            },
        ),
    ]


def entregar(d, cmd_id, plan):
    from motor.plan import Comando

    cmd = Comando.from_dict(
        {"cmd_id": cmd_id, "tipo": "plan", "plan": plan, "por": "t"}
    )
    return d._procesa(cmd)


class ReentregaTest(Banco):
    def _entrega(self, d, cmd_id, plan):
        return entregar(d, cmd_id, plan)

    def test_cada_entrega_distinta_es_una_version_y_el_recibo_la_dice(self):
        d = self.daemon()
        v1 = self.plan
        v2 = {**self.plan, "titulo": "otro título"}
        v2 = {
            **self.plan,
            "blocks": [
                {**b, "rows": [{**r, "acepta": "true"} for r in b["rows"]]}
                if b["id"] == "A"
                else b
                for b in self.plan["blocks"]
            ],
        }
        self.assertNotEqual(v1, v2)
        self.assertEqual(self._entrega(d, "c1", v1), "tomado (versión 1)")
        self.assertEqual(
            self._entrega(d, "c1b", v1),
            "tomado (versión 1)",
            "el mismo texto no hace nada",
        )
        self.assertEqual(self._entrega(d, "c2", v2), "tomado (versión 2)")
        self.assertEqual(self._entrega(d, "c3", v1), "tomado (versión 3)")
        cargas = self.eventos("PlanLoaded")
        self.assertEqual([e.data["version"] for e in cargas], [1, 2, 3])

    def test_reentregar_una_version_anterior_la_reactiva(self):
        d = self.daemon()
        v1 = self.plan
        v2 = {
            **self.plan,
            "blocks": [
                {**b, "prueba_real": "test -f otra-cosa"} if b["id"] == "A" else b
                for b in self.plan["blocks"]
            ],
        }
        self._entrega(d, "c1", v1)
        self._entrega(d, "c2", v2)
        self.assertEqual(d.plans()[0].block("A").prueba_real, "test -f otra-cosa")
        self.assertEqual(self._entrega(d, "c3", v1), "tomado (versión 3)")
        vigente = d.plans()[0]
        self.assertEqual(vigente.to_dict(), Plan.from_dict(v1).to_dict())
        snap = d._fold(vigente)
        self.assertEqual(snap.version, 3)

    def test_un_plan_con_eventos_viejos_sin_version_sigue_contando(self):
        d = self.daemon()
        d.store.append(
            "demo", "", "PlanLoaded", {"plan": self.plan, "por": "t"}, ukey="viejo"
        )
        v2 = {**self.plan, "titulo": "nuevo"}
        v2["blocks"] = [
            {**b, "aviso": "otro"} if b["id"] == "A" else b for b in self.plan["blocks"]
        ]
        self.assertEqual(self._entrega(d, "c2", v2), "tomado (versión 2)")

    def test_un_bloque_hecho_cuya_acepta_cambia_se_reabre_y_el_encargo_lo_dice(self):
        v1 = plan_con("true")
        v2 = plan_con("test -f README.md")
        base = hecho_en(v1)
        snap = m.fold(v1, base)
        self.assertIs(m.estado(snap, "A"), m.Estado.HECHO)
        evs = base + [carga(v2, 2)]
        snap = m.fold(v2, evs)
        self.assertIs(
            m.estado(snap, "A"), m.Estado.VERIFICAR, "se verifica el mismo SHA"
        )
        self.assertEqual(tipos(m.decide(snap, pol(), 1e10)), ["Verify"])
        fallo = evs + [
            ev(
                "Verified",
                "A",
                {
                    "sha": SHA,
                    "ok": False,
                    "detalle": "A.1 salió 1",
                    "huella": huella.insumo_de("verified", v2.block("A"), {}),
                },
            )
        ]
        snap = m.fold(v2, fallo)
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        texto = "\n".join(encargo(snap, "A", pol(), 1e10))
        self.assertIn("El plan cambió (versión 2)", texto)
        self.assertIn("la Acepta de A.1 cambió de `true` a `test -f README.md`", texto)
        ok = evs + [fallo[-1]]
        ok[-1] = ev("Verified", "A", {**fallo[-1].data, "ok": True})
        self.assertIs(
            m.estado(m.fold(v2, ok), "A"), m.Estado.HECHO, "pasa y vuelve a estar hecho"
        )

    def test_un_bloque_hecho_cuya_fila_cambia_vuelve_a_un_agente_con_lo_que_cambio(
        self,
    ):
        v1 = plan_con("true")
        v2 = parse_plan(
            PLAN.replace("| A.1 | algo | true |", "| A.1 | algo distinto | true |")
        )
        snap = m.fold(v2, hecho_en(v1) + [carga(v2, 2)])
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        (inicio,) = [
            a
            for a in m.decide(snap, pol(), 1e10)
            if isinstance(a, m.StartAttempt) and a.block == "A"
        ]
        self.assertIn(
            "la fila A.1 ahora pide: algo distinto", "\n".join(inicio.feedback)
        )
        self.assertIn("El plan cambió (versión 2)", "\n".join(inicio.feedback))
        entrega = hecho_en(v1) + [carga(v2, 2)] + entregado(sha="b" * 40, n=2)
        snap = m.fold(v2, entrega)
        self.assertIs(
            m.estado(snap, "A"), m.Estado.VERIFICAR, "lo nuevo sigue el camino normal"
        )
        self.assertIsNone(snap.blocks["A"].reapertura())

    def test_un_bloque_hecho_cuya_prueba_real_cambia_se_vuelve_a_probar(self):
        v1 = plan_con(prueba="true")
        v2 = plan_con(prueba="test -f README.md")
        snap = m.fold(v2, hecho_en(v1) + [carga(v2, 2)])
        self.assertIs(m.estado(snap, "A"), m.Estado.PROBAR)
        self.assertIn(
            "la prueba real cambió",
            "\n".join(snap.blocks["A"].reapertura()["cambios"]),
        )

    def test_lo_que_no_es_filas_acepta_ni_prueba_real_no_reabre(self):
        v1 = plan_con()
        con_aviso = PLAN.replace(
            "## Bloque A — uno\nlane: gate",
            "## Bloque A — uno\nlane: gate\naviso: otro aviso",
        )
        v3 = parse_plan(con_aviso)
        self.assertNotEqual(v1.block("A").aviso, v3.block("A").aviso)
        snap = m.fold(v3, hecho_en(v1) + [carga(v3, 2)])
        self.assertIs(m.estado(snap, "A"), m.Estado.HECHO)
        self.assertIsNone(snap.blocks["A"].reabierto)

    def test_un_bloque_que_no_estaba_hecho_no_se_reabre(self):
        v1 = plan_con("true")
        v2 = parse_plan(PLAN.replace("| A.1 | algo | true |", "| A.1 | otra | true |"))
        base = hecho_en(v1)[:4]  # entregado y verificado, sin merge
        snap = m.fold(v2, base + [carga(v2, 2)])
        self.assertIsNone(snap.blocks["A"].reabierto)

    def test_quitar_un_bloque_con_un_intento_en_curso_lo_detiene(self):
        v1, v2 = parse_plan(PLAN), sin_bloque_c()
        marca = ev(
            "AttemptStarted",
            "C",
            {"n": 1, "role": "implementar", "modelo": "glm", "proc": {}, "dir": "d"},
        )
        evs = [carga(v1, 1), marca, carga(v2, 2)]
        snap = m.fold(v2, evs)
        self.assertEqual(snap.version, 2)
        acs = m.decide(snap, pol(), 1e10)
        self.assertEqual(
            [a for a in acs if isinstance(a, m.Interrumpe)],
            [m.Interrumpe("C", "implementar", 1, "el bloque salió del plan")],
        )
        terminado = evs + [
            ev(
                "AttemptEnded",
                "C",
                {
                    "n": 1,
                    "role": "implementar",
                    "outcome": {"kind": "entregado", "sha": SHA},
                },
            )
        ]
        acs = m.decide(m.fold(v2, terminado), pol(), 1e10)
        self.assertEqual([a for a in acs if isinstance(a, m.Interrumpe)], [])

    def test_el_motor_detiene_el_proceso_del_bloque_quitado(self):
        import os
        import sys

        from motor import plataforma

        d = self.daemon()
        self._entrega(d, "c1", self.plan)
        quitado = {
            **self.plan,
            "blocks": [b for b in self.plan["blocks"] if b["id"] != "C"],
        }
        carpeta = self.tmp / "intento-c"
        carpeta.mkdir()
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            **plataforma.kwargs_grupo(),
        )
        proc = {
            "pid": p.pid,
            "boot_id": plataforma.boot_id(),
            "inicio": plataforma.hora_inicio(p.pid),
        }
        d.store.append(
            "demo",
            "C",
            "AttemptStarted",
            {
                "n": 1,
                "role": "implementar",
                "modelo": "glm",
                "proc": proc,
                "dir": str(carpeta),
            },
            ukey="s",
        )
        self.assertEqual(self._entrega(d, "c2", quitado), "tomado (versión 2)")
        try:
            d.tick()
            p.wait(timeout=10)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, 9)
        (fin,) = [e for e in self.eventos("AttemptEnded") if e.block == "C"]
        self.assertEqual(fin.data["outcome"]["kind"], "interrumpido")
        self.assertIn("salió del plan", fin.data["outcome"]["detalle"])
        d.tick()
        self.assertEqual(
            len([e for e in self.eventos("AttemptEnded") if e.block == "C"]), 1
        )


def _git(*a, cwd=None):
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@l", *a],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class MergeTicketTest(Banco):
    def setUp(self):
        super().setUp()
        self.d = self.daemon()
        self.bare = Path(self.plan["blocks"][0]["repo"])
        self.seed = self.tmp / "b" / "seed-app"
        self.base0 = _git("rev-parse", "main", cwd=self.bare)

    def commit(self, rama: str, archivo: str) -> str:
        """Un commit nuevo sobre la base, empujado a `rama` del repo del bloque."""
        _git("checkout", "-q", "-B", f"w-{archivo}", self.base0, cwd=self.seed)
        (self.seed / archivo).write_text("x\n")
        _git("add", "-A", cwd=self.seed)
        _git("commit", "-q", "-m", archivo, cwd=self.seed)
        sha = _git("rev-parse", "HEAD", cwd=self.seed)
        _git("push", "-q", str(self.bare), f"HEAD:refs/heads/{rama}", cwd=self.seed)
        return sha

    def entrega_en_rama(self, plan_id: str, sha: str) -> None:
        rama = self.d.cx.ws.rama(plan_id, "A")
        _git("update-ref", f"refs/heads/{rama}", sha, cwd=self.bare)

    def collect(self, plan_id: str, sha: str, n: int = 1):
        plan = next(p for p in self.d.plans() if p.id == plan_id)
        carpeta = self.tmp / f"intento-{plan_id}-{n}"
        carpeta.mkdir()
        (carpeta / "outcome.json").write_text(
            json.dumps(
                {"exit": 0, "timed_out": False, "no_progress": False, "tail": ""}
            )
        )
        (carpeta / "result.json").write_text(
            json.dumps({"sha": sha, "resumen": "listo"})
        )
        att = m.Attempt(
            n=n,
            role="implementar",
            modelo="glm",
            started=0.0,
            proc={},
            dir=str(carpeta),
        )
        return self.d.fx.collect(plan, self.d._fold(plan), "A", att)

    def carga(self, plan_id="demo"):
        entregar(self.d, f"c-{plan_id}", {**self.plan, "id": plan_id})
        return next(p for p in self.d.plans() if p.id == plan_id)

    # ------------------------------------------------------------------ sin_cambios

    def test_entregar_la_punta_de_la_base_al_cargar_el_plan_es_sin_cambios(self):
        self.carga()
        self.entrega_en_rama("demo", self.base0)
        out = self.collect("demo", self.base0)
        self.assertEqual(out["kind"], "sin_cambios", out)

    def test_entregar_algo_que_ya_estaba_en_la_base_al_cargar_tambien(self):
        nuevo = self.commit("main", "uno.txt")
        self.base0 = nuevo
        self.carga("otro")
        self.entrega_en_rama("otro", _git("rev-parse", f"{nuevo}^", cwd=self.bare))
        out = self.collect("otro", _git("rev-parse", f"{nuevo}^", cwd=self.bare))
        self.assertEqual(out["kind"], "sin_cambios", out)

    def test_entregar_trabajo_nuevo_sigue_siendo_entregado(self):
        self.carga()
        sha = self.commit(self.d.cx.ws.rama("demo", "A"), "saludo.txt")
        out = self.collect("demo", sha)
        self.assertEqual((out["kind"], out["sha"]), ("entregado", sha))

    def test_lo_que_otro_plan_mergeo_despues_de_cargar_no_es_sin_cambios(self):
        self.carga()
        ajeno = self.commit("main", "ajeno.txt")
        self.entrega_en_rama("demo", ajeno)
        self.assertEqual(self.collect("demo", ajeno)["kind"], "entregado")

    # ------------------------------------------------------------------ Merged

    def test_merged_solo_nace_de_merge_con_ticket_o_de_adoptar_uno_externo(self):
        permitidas = {"merge", "adopta_externo"}
        vistas = set()
        for f in sorted((RAIZ / "motor").rglob("*.py")):
            arbol = ast.parse(f.read_text(encoding="utf-8"))
            for fn in ast.walk(arbol):
                if not isinstance(fn, ast.FunctionDef):
                    continue
                for c in ast.walk(fn):
                    if (
                        isinstance(c, ast.Call)
                        and isinstance(c.func, ast.Attribute)
                        and c.func.attr == "append"
                        and any(
                            isinstance(x, ast.Constant) and x.value == "Merged"
                            for x in c.args
                        )
                    ):
                        vistas.add((f.name, fn.name))
        self.assertEqual(
            vistas, {("effects.py", n) for n in permitidas}, "otra parte anota Merged"
        )

    def test_merge_sin_ticket_no_anota_nada(self):
        plan = self.carga()
        snap = self.d._fold(plan)
        with self.assertRaises(TypeError):
            self.d.fx.merge(plan, snap, m.Merge("a" * 40))
        self.assertEqual(self.eventos("Merged"), [])

    def test_la_entrega_ya_en_la_base_se_adopta_y_se_avisa_una_vez(self):
        plan = self.carga()
        ajeno = self.commit("main", "ajeno.txt")
        snap = self.d._fold(plan)
        self.d.fx.open_pr(plan, snap, m.OpenPr("A", ajeno))
        (merged,) = self.eventos("Merged")
        self.assertEqual(
            (merged.data["sha"], merged.data["externo"], merged.data["ya_en_base"]),
            (ajeno, True, True),
        )
        (aviso,) = self.avisos("externo")
        self.assertIn("Alguien integró el bloque", aviso.read_text())
        self.assertIn("por fuera; sigo con eso", aviso.read_text())
        self.d.fx.open_pr(plan, self.d._fold(plan), m.OpenPr("A", ajeno))
        self.assertEqual(len(self.eventos("Merged")), 1)
        self.assertEqual(len(self.avisos("externo")), 1, "avisa una vez")

    def test_un_pr_mergeado_por_fuera_se_adopta_y_se_avisa_una_vez(self):
        from motor.github import PrView

        plan = self.carga()
        sha = "a" * 40
        self.d.store.append(
            "demo", "A", "PrOpened", {"numero": 7, "rama": "x"}, ukey="pr"
        )

        class Gh:
            def ver(self, repo, numero):
                return PrView(numero, "x", "main", False, sha, "mergeado", "c" * 40)

        self.d.fx.cx.gh = Gh()
        snap = self.d._fold(plan)
        for _ in range(2):  # un reintento con la misma foto del log
            self.d.fx.poll_checks(plan, snap, m.PollChecks("A", sha))
        (merged,) = self.eventos("Merged")
        self.assertTrue(merged.data["externo"])
        self.assertEqual(len(self.avisos("externo")), 1)


def inicia(n=1, block="A", **extra):
    return ev(
        "AttemptStarted",
        block,
        {
            "n": n,
            "role": "implementar",
            "modelo": "glm",
            "proc": {},
            "dir": f"d{n}",
            **extra,
        },
    )


def termina(kind, n=1, block="A", **extra):
    return ev(
        "AttemptEnded",
        block,
        {"n": n, "role": "implementar", "outcome": {"kind": kind, **extra}},
    )


def interrupciones(acs, bloque="A"):
    return [a for a in acs if isinstance(a, m.Interrumpe) and a.block == bloque]


class InterrumpeTest(Banco):
    def test_una_nota_con_un_intento_en_curso_lo_detiene(self):
        evs = [inicia(), ev("Note", "A", {"texto": "usa la API nueva"})]
        acs = m.decide(m.fold(parse_plan(PLAN), evs), pol(), 1e10)
        (i,) = interrupciones(acs)
        self.assertEqual((i.role, i.n), ("implementar", 1))
        self.assertIn("usa la API nueva", i.motivo)
        self.assertNotIn(
            "StartAttempt", tipos(acs), "el relevo sale cuando el intento ya terminó"
        )

    def test_la_nota_que_ya_iba_en_el_encargo_no_detiene_nada(self):
        evs = [ev("Note", "A", {"texto": "x"}), inicia()]
        acs = m.decide(m.fold(parse_plan(PLAN), evs), pol(), 1e10)
        self.assertEqual(interrupciones(acs), [])

    def test_una_nota_con_el_intento_terminado_no_detiene_nada(self):
        evs = [inicia(), termina("entregado", sha=SHA), ev("Note", "A", {"texto": "x"})]
        acs = m.decide(m.fold(parse_plan(PLAN), evs), pol(), 1e10)
        self.assertEqual(interrupciones(acs), [])

    def test_una_nota_de_otro_bloque_no_detiene_este(self):
        evs = [inicia(), ev("Note", "C", {"texto": "x"})]
        acs = m.decide(m.fold(parse_plan(PLAN), evs), pol(), 1e10)
        self.assertEqual(interrupciones(acs), [])

    def test_un_intento_de_revisar_no_se_detiene(self):
        revisar = ev(
            "AttemptStarted",
            "A",
            {
                "n": 1,
                "role": "revisar",
                "modelo": "opus",
                "proc": {},
                "dir": "r",
                "sha": SHA,
            },
        )
        evs = [revisar, ev("Note", "A", {"texto": "x"})]
        acs = m.decide(m.fold(parse_plan(PLAN), evs), pol(), 1e10)
        self.assertEqual(interrupciones(acs), [])

    def test_una_version_nueva_que_cambia_el_bloque_detiene_el_intento(self):
        v1 = plan_con("true")
        v2 = plan_con("test -f README.md")
        evs = [carga(v1, 1), inicia(), carga(v2, 2)]
        acs = m.decide(m.fold(v2, evs), pol(), 1e10)
        (i,) = interrupciones(acs)
        self.assertIn(
            "la Acepta de A.1 cambió de `true` a `test -f README.md`", i.motivo
        )

    def test_una_version_que_no_toca_el_bloque_lo_deja_trabajar(self):
        v1 = parse_plan(PLAN)
        v2 = parse_plan(PLAN.replace("| C.1 | algo | true |", "| C.1 | otra | true |"))
        evs = [carga(v1, 1), inicia(), carga(v2, 2)]
        acs = m.decide(m.fold(v2, evs), pol(), 1e10)
        self.assertEqual(interrupciones(acs), [])

    def test_el_intento_siguiente_lleva_la_nota_y_el_cambio_en_el_encargo(self):
        v1 = plan_con("true")
        v2 = plan_con("test -f README.md")
        evs = [
            carga(v1, 1),
            inicia(),
            ev("Note", "A", {"texto": "usa la API nueva"}),
            carga(v2, 2),
        ]
        (i,) = interrupciones(m.decide(m.fold(v2, evs), pol(), 1e10))
        evs.append(termina("interrumpido", detalle=i.motivo, foto="subida"))
        snap = m.fold(v2, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.ARREGLAR)
        (inicio,) = [
            a
            for a in m.decide(snap, pol(), 1e10)
            if isinstance(a, m.StartAttempt) and a.block == "A"
        ]
        texto = "\n".join(inicio.feedback)
        self.assertIn("Nota del lead: usa la API nueva", texto)
        self.assertIn("la Acepta de A.1 cambió", texto)
        self.assertIn("se detuvo", texto)

    def test_el_intento_detenido_no_cuenta_como_falla_del_modelo(self):
        evs = [
            inicia(1),
            termina("interrumpido", 1, detalle="x"),
            inicia(2),
            termina("interrumpido", 2, detalle="y"),
            inicia(3),
            termina("interrumpido", 3, detalle="z"),
        ]
        snap = m.fold(parse_plan(PLAN), evs)
        policy = pol()
        for mo in ("sonnet", "glm"):
            motivo, _ = m.bloqueo(
                snap,
                policy,
                1e10,
                snap.blocks["A"],
                "implementar",
                mo,
                policy.implementadores,
            )
            self.assertEqual(motivo, "", f"{mo} sigue disponible")
        self.assertIsNot(
            diagnostico(snap, "A", policy, 1e10).causa, Causa.ESCALERA_AGOTADA
        )

    # ------------------------------------------------------------------ el efecto

    def _con_intento(self):
        import sys

        from motor import plataforma

        d = self.daemon()
        lanzados = []
        d.fx.start_attempt = lambda plan, snap, a: lanzados.append(a)
        entregar(d, "c1", self.plan)
        plan = d.plans()[0]
        d.cx.ws.prepara(plan.block("A").repo, plan.id, "A", "main")
        w = self.home / "wt" / "demo" / "A"
        (w / "a_medias.txt").write_text("sin commitear\n")
        carpeta = self.tmp / "intento-a"
        carpeta.mkdir()
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            **plataforma.kwargs_grupo(),
        )
        proc = {
            "pid": p.pid,
            "boot_id": plataforma.boot_id(),
            "inicio": plataforma.hora_inicio(p.pid),
        }
        d.store.append(
            "demo",
            "A",
            "AttemptStarted",
            {
                "n": 1,
                "role": "implementar",
                "modelo": "glm",
                "proc": proc,
                "dir": str(carpeta),
            },
            ukey="s",
        )
        return d, p, lanzados

    def test_el_motor_detiene_el_proceso_guarda_la_foto_y_relanza_con_la_nota(self):
        import os

        d, p, lanzados = self._con_intento()
        entregar(d, "c1", self.plan)
        from motor.plan import Comando

        d._procesa(
            Comando.from_dict(
                {
                    "cmd_id": "n1",
                    "tipo": "nota",
                    "plan": "demo",
                    "block": "A",
                    "texto": "usa la API nueva",
                }
            )
        )
        try:
            d.tick()
            p.wait(timeout=10)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, 9)
        (fin,) = self.eventos("AttemptEnded")
        self.assertEqual(fin.data["outcome"]["kind"], "interrumpido")
        self.assertIn("usa la API nueva", fin.data["outcome"]["detalle"])
        self.assertIn(fin.data["outcome"]["foto"], ("subida", "guardada"))
        rama = d.cx.ws.rama_wip("demo", "A")
        foto = subprocess.run(
            ["git", "ls-remote", self.plan["blocks"][0]["repo"], f"refs/heads/{rama}"],
            capture_output=True,
            text=True,
        ).stdout
        self.assertTrue(foto.strip(), "la foto wip quedó en el remoto")
        self.assertEqual(
            [a for a in lanzados if a.block == "A"],
            [],
            "este tick solo detuvo el intento",
        )
        d.tick()
        (nuevo,) = [a for a in lanzados if a.block == "A"]
        self.assertEqual((nuevo.block, nuevo.role, nuevo.n), ("A", "implementar", 2))
        self.assertIn("Nota del lead: usa la API nueva", "\n".join(nuevo.feedback))
        d.tick()
        self.assertEqual(
            len(self.eventos("AttemptEnded")), 1, "no se detiene dos veces"
        )


MOTIVO = "el servicio de pagos no contesta desde esta máquina"


def bloqueado(n=1, maquina="m1"):
    return [
        inicia(n),
        termina("bloqueado", n, detalle=MOTIVO, huella_maquina=maquina),
    ]


class BloqueadoTest(Banco):
    def collect(self, resultado):
        d = self.daemon()
        d.store.append("demo", "", "PlanLoaded", {"plan": self.plan, "por": "t"})
        carpeta = self.tmp / "intento-bloqueado"
        carpeta.mkdir(exist_ok=True)
        (carpeta / "outcome.json").write_text(
            json.dumps(
                {"exit": 0, "timed_out": False, "no_progress": False, "tail": ""}
            )
        )
        (carpeta / "result.json").write_text(json.dumps(resultado))
        att = m.Attempt(
            n=1,
            role="implementar",
            modelo="glm",
            started=0.0,
            proc={},
            dir=str(carpeta),
        )
        plan = d.plans()[0]
        return d.fx.collect(plan, d._fold(plan), "A", att)

    def test_el_resultado_bloqueado_termina_el_intento_con_su_motivo(self):
        out = self.collect({"bloqueado": f"  {MOTIVO}  "})
        self.assertEqual(out["kind"], "bloqueado")
        self.assertEqual(out["detalle"], MOTIVO)
        self.assertTrue(out["huella_maquina"])

    def test_un_bloqueado_vacio_o_que_no_es_texto_no_cuenta(self):
        for malo in ("", "   ", None, 3, ["x"]):
            with self.subTest(malo=malo):
                self.assertEqual(
                    self.collect({"bloqueado": malo})["kind"], "sin_resultado"
                )

    def test_el_motor_no_regresa_el_bloque_a_otro_agente_y_avisa_una_vez(self):
        snap = m.fold(parse_plan(PLAN), bloqueado())
        acs = m.decide(snap, pol(), 1e10)
        self.assertNotIn("StartAttempt", tipos(acs))
        d = diagnostico(snap, "A", pol(), 1e10)
        self.assertIs(d.causa, Causa.BLOQUEADO)
        self.assertTrue(d.atorado)
        self.assertIn(MOTIVO, d.motivo)
        (aviso,) = [
            a
            for a in acs
            if isinstance(a, m.Notify) and a.template == "atorado" and a.block == "A"
        ]
        self.assertIn(MOTIVO, dict(aviso.campos)["motivo"])
        evs = bloqueado() + [ev("NoticeQueued", "A", {"key": aviso.key})]
        otra = m.decide(m.fold(parse_plan(PLAN), evs), pol(), 1e10)
        self.assertEqual(
            [
                a
                for a in otra
                if isinstance(a, m.Notify)
                and a.template == "atorado"
                and a.block == "A"
            ],
            [],
        )
        self.assertNotIn("StartAttempt", tipos(otra))

    def test_bloqueado_no_cuenta_como_falla_del_modelo(self):
        evs = bloqueado(1) + bloqueado(2) + bloqueado(3)
        snap = m.fold(parse_plan(PLAN), evs)
        policy = pol()
        for mo in ("sonnet", "glm"):
            motivo, _ = m.bloqueo(
                snap,
                policy,
                1e10,
                snap.blocks["A"],
                "implementar",
                mo,
                policy.implementadores,
            )
            self.assertEqual(motivo, "")

    def test_una_nota_lo_destraba_y_el_encargo_lleva_el_motivo_y_la_nota(self):
        evs = bloqueado() + [ev("Note", "A", {"texto": "ya abrí el acceso"})]
        snap = m.fold(parse_plan(PLAN), evs)
        (inicio,) = [
            a
            for a in m.decide(snap, pol(), 1e10)
            if isinstance(a, m.StartAttempt) and a.block == "A"
        ]
        texto = "\n".join(inicio.feedback)
        self.assertIn(MOTIVO, texto)
        self.assertIn("Nota del lead: ya abrí el acceso", texto)

    def test_un_plan_corregido_del_bloque_lo_destraba(self):
        v1, v2 = plan_con("true"), plan_con("test -f README.md")
        evs = [carga(v1, 1), *bloqueado(), carga(v2, 2)]
        acs = m.decide(m.fold(v2, evs), pol(), 1e10)
        self.assertIn("StartAttempt", tipos(acs))

    def test_un_plan_que_cambia_otro_bloque_no_lo_destraba(self):
        v1 = parse_plan(PLAN)
        v2 = parse_plan(PLAN.replace("| C.1 | algo | true |", "| C.1 | otra | true |"))
        evs = [carga(v1, 1), *bloqueado(), carga(v2, 2)]
        self.assertNotIn("StartAttempt", tipos(m.decide(m.fold(v2, evs), pol(), 1e10)))

    def test_una_maquina_corregida_lo_destraba(self):
        plan = parse_plan(PLAN)
        mismo = m.decide(m.fold(plan, bloqueado(maquina="m1"), "m1"), pol(), 1e10)
        self.assertNotIn("StartAttempt", tipos(mismo))
        otra = m.decide(m.fold(plan, bloqueado(maquina="m1"), "m2"), pol(), 1e10)
        self.assertIn("StartAttempt", tipos(otra))

    def test_en_el_tick_no_lanza_agente_y_avisa_una_vez(self):
        d = self.daemon()
        lanzados = []
        d.fx.start_attempt = lambda plan, snap, a: lanzados.append(a)
        entregar(d, "c1", self.plan)
        carpeta = self.tmp / "intento"
        carpeta.mkdir()
        d.store.append(
            "demo",
            "A",
            "AttemptStarted",
            {
                "n": 1,
                "role": "implementar",
                "modelo": "glm",
                "proc": {},
                "dir": str(carpeta),
            },
            ukey="s",
        )
        d.store.append(
            "demo",
            "A",
            "AttemptEnded",
            {
                "n": 1,
                "role": "implementar",
                "outcome": {"kind": "bloqueado", "detalle": MOTIVO},
            },
            ukey="e",
        )
        d.tick()
        d.tick()
        self.assertEqual([a for a in lanzados if a.block == "A"], [])
        atorados = [
            e
            for e in self.eventos("NoticeQueued")
            if e.block == "A" and e.data["key"].startswith("atorado:A:bloqueado")
        ]
        self.assertEqual(len(atorados), 1)


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
