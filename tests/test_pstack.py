"""B.1: huella, juicio e instrucciones, con un registro de ejemplo por CLI.

Los registros imitan la forma que `docs/arena/pstack/hechos.md` §4 midió en cada CLI. El bloque C los
reemplaza por registros reales de cada máquina. El juez solo cuenta subagentes: una llamada a la
herramienta de subagentes del CLI vale uno, y en Kimi `AgentSwarm` vale uno por elemento de `items`.
"""

from __future__ import annotations

import base64
import io
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor import effects as ef
from motor import machine as m
from motor import pstack as ps
from motor import relevo
from motor.__main__ import conf_valida
from motor.conf import load_conf
from motor.plan import Plan, parse_plan

from .test_guias import _conf_falsa


def jl(*objs) -> str:
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def claude(*llamadas, parent=None) -> str:
    return jl(
        {"type": "system", "subtype": "init"},
        *[
            {
                "type": "assistant",
                "parent_tool_use_id": parent,
                "message": {"content": [{"type": "tool_use", "name": n, "input": i}]},
            }
            for n, i in llamadas
        ],
    )


def kimi(*llamadas) -> str:
    return jl(
        *[
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": n, "arguments": json.dumps(i)}}],
            }
            for n, i in llamadas
        ]
    )


def opencode(*llamadas) -> str:
    return jl(
        *[
            {
                "type": "tool_use",
                "part": {"tool": n, "callID": f"c{k}", "state": {"input": i}},
            }
            for k, (n, i) in enumerate(llamadas)
        ]
    )


def grok(*llamadas) -> str:
    return jl(
        *[
            {"tool_name": n, "tool_call_id": f"t{k}", "args": i}
            for k, (n, i) in enumerate(llamadas)
        ]
    )


def muse(*llamadas) -> str:
    return jl(
        *[
            {
                "type": "assistant_tool_calls_committed",
                "calls": [{"name": n, "args": json.dumps(i)}],
            }
            for n, i in llamadas
        ]
    )


# por CLI: (fábrica, subagente, comando)
CASOS = {
    "claude": (
        claude,
        ("Agent", {"subagent_type": "poteto-agent"}),
        ("Bash", {"command": "git status"}),
    ),
    "kimi": (
        kimi,
        ("Agent", {"subagent_type": "poteto-agent"}),
        ("Bash", {"command": "git status"}),
    ),
    "opencode": (
        opencode,
        ("task", {"subagent_type": "poteto-agent"}),
        ("bash", {"command": "git status"}),
    ),
    "grok": (
        grok,
        ("spawn_subagent", {"agent": "poteto-agent"}),
        ("run_terminal_command", {"command": "git status"}),
    ),
    "muse": (
        muse,
        ("subagent_spawn", {"subagent_type": "poteto-agent"}),
        ("bash", {"command": "git status"}),
    ),
    "qwen": (
        claude,
        ("task", {"subagent_type": "poteto-agent"}),
        ("run_shell_command", {"command": "git status"}),
    ),
}


class TestFormatos(unittest.TestCase):
    def test_hay_una_fila_por_cli(self):
        self.assertEqual(
            set(ps.FORMATOS), {"claude", "kimi", "opencode", "grok", "muse", "qwen"}
        )

    def test_huella_cuenta_el_subagente_y_el_comando_en_cada_cli(self):
        for cli, (fab, sub, cmd) in CASOS.items():
            with self.subTest(cli=cli):
                h = ps.huella(fab(sub, cmd), cli)
                self.assertTrue(h.legible)
                self.assertEqual(h.subagentes, ("poteto-agent",))
                self.assertEqual(h.comandos, ("git status",))
                self.assertEqual(h.acciones, 2)

    def test_un_agentswarm_de_kimi_vale_uno_por_elemento_de_items(self):
        def cuenta(args):
            return len(ps.huella(kimi(("AgentSwarm", args)), "kimi").subagentes)

        self.assertEqual(
            cuenta({"prompt_template": "revisa {{item}}", "items": ["a", "b", "c"]}), 3
        )
        self.assertEqual(cuenta({"items": [{}, {}]}), 2)

    def test_un_agentswarm_sin_items_lanza_a_nadie(self):
        def cuenta(args):
            return len(ps.huella(kimi(("AgentSwarm", args)), "kimi").subagentes)

        self.assertEqual(cuenta({"items": []}), 0)
        self.assertEqual(cuenta({"resume_agent_ids": ["a1"]}), 0)
        self.assertEqual(cuenta({"items": 2}), 0, "un número no es una lista")
        self.assertEqual(cuenta({"items": "a,b"}), 0)

    def test_un_agent_de_claude_cuenta_uno_aunque_traiga_items(self):
        h = ps.huella(
            claude(("Agent", {"subagent_type": "x", "items": ["a", "b"]})), "claude"
        )
        self.assertEqual(len(h.subagentes), 1)

    def test_las_llamadas_de_un_subagente_no_son_del_agente(self):
        h = ps.huella(
            claude(("Agent", {"subagent_type": "x"}), parent="toolu_1"), "claude"
        )
        self.assertEqual(h.subagentes, ())

    def test_opencode_y_grok_cuentan_una_vez_la_llamada_repetida(self):
        dos = jl(
            *[{"tool_name": "spawn_subagent", "tool_call_id": "t1", "args": {}}] * 2
        )
        self.assertEqual(len(ps.huella(dos, "grok").subagentes), 1)

    def test_qwen_json_completo_en_un_arreglo(self):
        arreglo = json.dumps(
            [
                {"type": "system"},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "task",
                                "input": {"subagent_type": "x"},
                            }
                        ]
                    },
                },
            ],
            indent=2,
        )
        self.assertEqual(len(ps.huella(arreglo, "qwen").subagentes), 1)


class TestNoVerificable(unittest.TestCase):
    def test_registros_que_no_se_pueden_leer_pasan_como_no_verificable(self):
        ilegibles = [
            "",
            None,
            "texto suelto del CLI\nsin json\n",
            # `claude -p --output-format json`: un objeto final sin herramientas (hechos.md §4)
            json.dumps({"type": "result", "session_id": "x", "result": "listo"}),
        ]
        for fuente in ilegibles:
            with self.subTest(fuente=fuente):
                h = ps.huella(fuente, "claude")
                self.assertFalse(h.legible)
                r = ps.juzga(h, ps.Exigencia(playbook=True, min_subagentes=2))
                self.assertEqual(r, ps.Cumple(verificable=False))

    def test_objeto_final_de_grok_json_es_no_verificable(self):
        final = json.dumps(
            {
                "text": "listo",
                "thought": "",
                "sessionId": "s1",
                "cwd": "/w",
                "num_turns": 9,
                "usage": {},
                "total_cost_usd": 0.1,
            }
        )
        h = ps.huella(final, "grok")
        self.assertFalse(h.legible)
        self.assertEqual(ps.juzga(h, ps.Exigencia()), ps.Cumple(verificable=False))

    def test_registro_legible_sin_subagentes_si_faltan(self):
        h = ps.huella(jl({"type": "system"}), "claude")
        self.assertTrue(h.legible)
        self.assertIsInstance(ps.juzga(h, ps.Exigencia(min_subagentes=1)), ps.Falta)


class TestJuzga(unittest.TestCase):
    def test_cumple_con_los_subagentes_pedidos(self):
        h = ps.huella(
            claude(("Agent", {"subagent_type": "x"}), ("Agent", {"description": "y"})),
            "claude",
        )
        self.assertEqual(ps.juzga(h, ps.Exigencia(min_subagentes=2)), ps.Cumple())

    def test_falta_dice_cuantos_lanzo_y_cuantos_se_pedian(self):
        h = ps.huella(claude(("Bash", {"command": "ls"})), "claude")
        r = ps.juzga(h, ps.Exigencia(min_subagentes=2))
        self.assertEqual(r, ps.Falta(("lanzó 0 subagente(s) y se pedían al menos 2",)))

    def test_sin_exigencia_de_subagentes_cualquier_registro_legible_cumple(self):
        h = ps.huella(claude(("Bash", {"command": "ls"})), "claude")
        self.assertEqual(ps.juzga(h, ps.Exigencia()), ps.Cumple())


class TestInstrucciones(unittest.TestCase):
    def test_no_pide_cargar_nada_las_guias_van_en_el_anexo(self):
        t = ps.instrucciones(ps.Exigencia(), "claude")
        self.assertTrue(t.startswith("## Cómo trabajas"))
        self.assertIn("## Anexo: tus guías", t)
        self.assertIn("relativas a la carpeta de esa guía", t)
        self.assertIn("juzga lo que haces, no lo que lees", t)
        for prohibido in (
            "antes de tu acción",
            "ábrelo",
            "te detiene",
            "Carga la skill",
        ):
            self.assertNotIn(prohibido, t)

    def test_con_subagentes_dice_cuantos_y_como_cuenta_agentswarm_en_kimi(self):
        t = ps.instrucciones(ps.Exigencia(min_subagentes=2), "kimi")
        self.assertIn("al menos 2 subagente(s)", t)
        self.assertIn("AgentSwarm", t)
        for cli in ("claude", "opencode", "grok", "muse", "qwen"):
            c = ps.instrucciones(ps.Exigencia(min_subagentes=2), cli)
            self.assertIn("al menos 2 subagente(s)", c)
            self.assertNotIn("AgentSwarm", c, cli)
        self.assertNotIn("subagente", ps.instrucciones(ps.Exigencia(), "kimi"))


class TestConf(unittest.TestCase):
    def test_parse_acepta_carril_papel_o_papel_carril(self):
        a = ps.parse_conf(
            {
                "pstack": {"commit": "abc", "exigir": True},
                "exigencia": {"gate": {"implementar": {"playbook": True}}},
            }
        )
        b = ps.parse_conf({"exigencia": {"implementar": {"gate": {"playbook": True}}}})
        self.assertTrue(a.exigir)
        self.assertEqual(
            a.exigencia("gate", "implementar"), b.exigencia("gate", "implementar")
        )
        self.assertTrue(a.exigencia("gate", "implementar").playbook)
        self.assertEqual(a.exigencia("fast", "revisar").skills, ("poteto-mode",))

    def test_poteto_mode_siempre_va_primero(self):
        c = ps.parse_conf(
            {"exigencia": {"gate": {"revisar": {"skills": ["interrogate"]}}}}
        )
        self.assertEqual(
            c.exigencia("gate", "revisar").skills, ("poteto-mode", "interrogate")
        )

    def test_celda_mal_nombrada_se_rechaza(self):
        with self.assertRaises(ValueError):
            ps.parse_conf({"exigencia": {"turbo": {"revisar": {}}}})


class TestCopiaFija(unittest.TestCase):
    """B.2: la copia fija de pstack en ~/.motor/pstack/<id de árbol>/."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pstack-copia-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conf = self.tmp / "conf"
        sk = self.conf / "pstack" / "skills" / "poteto-mode"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("v1\n")
        (self.conf / "pstack" / "skills" / "interrogate").mkdir()
        (self.conf / "pstack" / "skills" / "interrogate" / "SKILL.md").write_text("i\n")
        (self.conf / "pstack" / "ORIGEN").write_text("origen: x abc1234567\n")
        arnes = self.conf / "pstack" / "docs" / "harness"
        arnes.mkdir(parents=True)
        (arnes / "claude-code.md").write_text("adaptador\n")
        (self.conf / "pstack-motor").mkdir()
        (self.conf / "pstack-motor" / "motor.md").write_text("m\n")
        self.raiz = self.tmp / "home" / "pstack"

    def test_materializa_una_vez_y_no_cambia_mientras_el_contenido_sea_el_mismo(self):
        id1, d1 = ef.materializa_pstack(self.conf, self.raiz)
        self.assertEqual(d1, self.raiz / id1)
        self.assertEqual(
            (d1 / "pstack/skills/poteto-mode/SKILL.md").read_text(), "v1\n"
        )
        self.assertTrue((d1 / "pstack-motor/motor.md").is_file())
        self.assertEqual(
            [p.name for p in self.raiz.iterdir()], [id1], "sin restos .tmp"
        )
        (d1 / "marca").write_text("no se toca")
        self.assertEqual(ef.materializa_pstack(self.conf, self.raiz), (id1, d1))
        self.assertTrue(
            (d1 / "marca").exists(), "una copia hecha no se vuelve a escribir"
        )

    def test_otro_contenido_es_otra_copia_y_la_vieja_sigue_ahi(self):
        id1, d1 = ef.materializa_pstack(self.conf, self.raiz)
        (self.conf / "pstack/skills/poteto-mode/SKILL.md").write_text("v2\n")
        id2, d2 = ef.materializa_pstack(self.conf, self.raiz)
        self.assertNotEqual(id1, id2)
        self.assertEqual(
            (d1 / "pstack/skills/poteto-mode/SKILL.md").read_text(), "v1\n"
        )
        self.assertEqual(
            (d2 / "pstack/skills/poteto-mode/SKILL.md").read_text(), "v2\n"
        )

    def test_sin_pstack_en_la_conf_no_hay_copia(self):
        shutil.rmtree(self.conf / "pstack")
        self.assertIsNone(ef.materializa_pstack(self.conf, self.raiz))

    def test_la_limpieza_de_14_dias_borra_las_copias_viejas_menos_la_vigente(self):
        vieja = self.raiz / "vieja"
        reciente = self.raiz / "reciente"
        vigente = self.raiz / "vigente"
        for d in (vieja, reciente, vigente):
            d.mkdir(parents=True)
        hace_20_dias = time.time() - 20 * 86400
        for d in (vieja, vigente):
            os.utime(d, (hace_20_dias, hace_20_dias))
        fx = ef.Effects.__new__(ef.Effects)
        fx._pstack = ("sha", ("vigente", vigente))
        fx.cx = SimpleNamespace(
            home=self.tmp / "home", conf=SimpleNamespace(tiempos={})
        )
        fx.purga_registros([], time.time())
        self.assertEqual(
            sorted(p.name for p in self.raiz.iterdir()), ["reciente", "vigente"]
        )

    def conf_cargada(self, exigir=True, commit="abc1234567"):
        (self.conf / "papeles.toml").write_text(
            '[papeles]\nimplementar = "c"\nrevisar = "r"\n'
        )
        (self.conf / "modelos.toml").write_text('[carriles]\nc = ["a"]\nr = ["a"]\n')
        (self.conf / "rutas.toml").write_text(
            '[rutas.a]\ncuenta = "x"\ncli = "claude"\nargv = ["claude", "{prompt}"]\n'
        )
        celdas = "".join(
            f'[exigencia.{lane}.{papel}]\nskills = ["poteto-mode"]\n'
            for lane in ps.LANES
            for papel in ps.PAPELES
        )
        (self.conf / "pstack-motor" / "claude.md").write_text("c\n")
        (self.conf / "pstack.toml").write_text(
            f'[pstack]\ncommit = "{commit}"\nexigir = {str(exigir).lower()}\n{celdas}'
        )
        return load_conf(self.conf)

    def test_conf_valida_exige_que_pstack_toml_coincida_con_la_copia_fija(self):
        self.assertEqual(ef.errores_pstack(self.conf, self.conf_cargada()), [])
        e = ef.errores_pstack(self.conf, self.conf_cargada(commit="9999999999"))
        self.assertEqual(len(e), 1)
        self.assertIn("la copia fija viene de otro", e[0])

    def test_conf_valida_pide_las_skills_y_los_mapas_que_nombra(self):
        c = self.conf_cargada()
        shutil.rmtree(self.conf / "pstack/skills/interrogate")
        pc = ps.parse_conf(
            {
                "pstack": {"commit": "abc1234567"},
                "exigencia": {
                    lane: {papel: {"skills": ["poteto-mode", "interrogate"]}}
                    for lane in ps.LANES
                    for papel in ps.PAPELES
                },
            }
        )
        errores = ef.errores_pstack(self.conf, replace(c, pstack=pc))
        self.assertTrue(any("interrogate" in x for x in errores), errores)
        os.remove(self.conf / "pstack-motor" / "claude.md")
        self.assertTrue(
            any("pstack-motor/claude.md" in x for x in ef.errores_pstack(self.conf, c))
        )

    def test_conf_valida_corre_desde_el_cli(self):
        self.conf_cargada()
        self.assertEqual(conf_valida(self.conf), [])
        self.conf_cargada(commit="9999999999")
        self.assertTrue(any("otro" in x for x in conf_valida(self.conf)))

    def test_sin_pstack_cuenta_como_falla_del_modelo(self):
        self.assertEqual(relevo.desde_kind("sin_pstack"), relevo.Falla.TASK_FAILURE)
        self.assertNotIn("sin_pstack", m.NO_ES_FALLA)


class TestBloqueDeBug(unittest.TestCase):
    PLAN = """# x
autopiloto: motor
plan: x
repo: /r

## Bloque A — {titulo}
lane: gate
{tipo}prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | algo | true |
"""

    def bloque(self, titulo="algo", tipo=""):
        texto = self.PLAN.format(titulo=titulo, tipo=f"tipo: {tipo}\n" if tipo else "")
        return parse_plan(texto).blocks[0]

    def test_es_de_bug_por_el_campo_tipo_o_por_el_titulo(self):
        self.assertTrue(self.bloque(tipo="bug").es_bug)
        self.assertTrue(self.bloque(tipo="BUG").es_bug)
        self.assertTrue(self.bloque(titulo="arregla el Bug del saludo").es_bug)
        self.assertFalse(
            self.bloque(titulo="depura el servicio").es_bug, "debug no es bug"
        )
        self.assertFalse(self.bloque(tipo="feature").es_bug)

    def test_el_tipo_sobrevive_al_log_de_eventos(self):
        plan = parse_plan(self.PLAN.format(titulo="algo", tipo="tipo: bug\n"))
        self.assertTrue(Plan.from_dict(plan.to_dict()).blocks[0].es_bug)

    def test_sin_repro_cuenta_como_falla_del_modelo(self):
        self.assertEqual(relevo.desde_kind("sin_repro"), relevo.Falla.TASK_FAILURE)
        self.assertNotIn("sin_repro", m.NO_ES_FALLA)


class TestGrokJson(unittest.TestCase):
    def test_grok_json_se_juzga_con_el_updates_jsonl_de_su_sesion(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ses = tmp / ".grok" / "sessions" / "-w" / "s1"
        ses.mkdir(parents=True)
        (ses / "updates.jsonl").write_text(
            jl(
                {"tool_name": "spawn_subagent", "args": {"agent": "a"}},
                {"tool_name": "spawn_subagent", "args": {"agent": "b"}},
            ),
            encoding="utf-8",
        )
        log = tmp / "log.txt"
        log.write_text(
            json.dumps({"text": "listo", "sessionId": "s1", "cwd": "/w"}),
            encoding="utf-8",
        )
        info = {"cli": "grok", "registros": [str(log)]}
        patcher = mock.patch.dict(os.environ, GROK_HOME=str(tmp / ".grok"))
        patcher.start()
        self.addCleanup(patcher.stop)
        regs = ef.Effects._registros_de(info)
        self.assertEqual(regs[0], str(ses / "updates.jsonl"))
        h = ps.huella(Path(regs[0]).read_text(encoding="utf-8"), "grok")
        self.assertEqual(h.subagentes, ("a", "b"))

    def test_grok_json_con_sangria_en_varias_lineas_tambien_encuentra_la_sesion(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ses = tmp / ".grok" / "sessions" / "-w" / "s2"
        ses.mkdir(parents=True)
        (ses / "updates.jsonl").write_text(
            jl({"tool_name": "spawn_subagent", "args": {"agent": "a"}}),
            encoding="utf-8",
        )
        final = {"text": "listo", "sessionId": "s2", "usage": {"a": 1, "b": [1, 2]}}
        log = tmp / "log.txt"
        log.write_text(
            "aviso del CLI\n" + json.dumps(final, indent=2) + "\n{ otro aviso\n",
            encoding="utf-8",
        )
        patcher = mock.patch.dict(os.environ, GROK_HOME=str(tmp / ".grok"))
        patcher.start()
        self.addCleanup(patcher.stop)
        regs = ef.Effects._registros_de({"cli": "grok", "registros": [str(log)]})
        self.assertEqual(regs[0], str(ses / "updates.jsonl"))

    def test_objetos_lee_un_objeto_con_sangria_y_sigue_con_las_lineas_de_despues(self):
        texto = (
            json.dumps(
                {"a": {"b": 1}, "c": [1, 2], "subs": [{"sessionId": "anidado"}]},
                indent=2,
            )
            + '\n{"d": 2}\n{"roto": \n'
        )
        self.assertEqual(
            list(ps._objetos(texto)),
            [
                {"a": {"b": 1}, "c": [1, 2], "subs": [{"sessionId": "anidado"}]},
                {"d": 2},
            ],
        )


class TestBloqueantesDelRevisorMalformados(unittest.TestCase):
    """B.4: el revisor es entrada externa; un bloqueante raro no debe tumbar `collect`."""

    def _collect(self, bloqueantes):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "outcome.json").write_text(
            json.dumps(dict(exit=0, timed_out=False, no_progress=False, tail=""))
        )
        (d / "result.json").write_text(
            json.dumps(
                dict(sha="abc", veredicto="CAMBIOS", notas=[], bloqueantes=bloqueantes)
            )
        )
        eventos = []
        N = SimpleNamespace
        fx = ef.Effects(N(store=N(append=lambda *a, **k: eventos.append(a))))
        fx._proveedor = lambda mod: ""
        fx._corre_en = lambda p, s, b, sha, n, c: SimpleNamespace(returncode=0)
        r = fx.collect(
            N(id="p", block=lambda b: N(id=b)),
            None,
            "A",
            N(dir=str(d), role="revisar", sha="abc", modelo="opus", n=1),
        )
        return r, eventos

    def test_bloqueante_sin_repro_no_revienta(self):
        r, ev = self._collect([dict(texto="a", repro="true"), dict(texto="b")])
        self.assertEqual(r["kind"], "veredicto")
        self.assertEqual(len(ev[0][3]["bajados"]), 1)

    def test_repro_null_y_bloqueante_no_objeto_son_veredicto_invalido(self):
        for malo in (
            [dict(texto="b", repro=None)],
            ["suelto"],
            None,
        ):
            with self.subTest(malo=malo):
                r, ev = self._collect(malo)
                self.assertEqual(r["kind"], "veredicto_invalido")
                self.assertEqual(ev, [])


DATOS = Path(__file__).resolve().parent / "datos" / "pstack"
MAQUINAS = ("mac", "mini", "pc")
EXIGENCIA = ps.Exigencia(min_subagentes=1)
# Lo que `huella` ve con los registros que mide.sh trajo de rutas.toml de origin/main de la conf. Estas
# rutas todavía no llevan los flags que hacen legible el registro de claude y opencode (los trae
# conf-ejemplo/rutas.toml): con ellos la medición dio Cumple en la Mac. Cuando la conf los tenga,
# mide.sh vuelve a medir, esta tabla pasa a Cumple y la prueba de la ruta avisa que ya no aplica.
FLAG_QUE_FALTA = {
    "claude": "--session-id",
    "opencode": "--format",
}
NO_VERIFICABLE_POR_RUTA = {
    (
        cli,
        mq,
    ): f"la ruta de rutas.toml no lleva {flag}: el motor no puede leer el registro de {cli}"
    for cli, flag in FLAG_QUE_FALTA.items()
    for mq in MAQUINAS
}
SIN_ENTRAR = {
    ("muse", "mac"): "missing meta credentials: falta `muse login` en la Mac",
    ("muse", "mini"): "missing meta credentials: falta `muse login` en la Mini",
}
SIN_TERMINAR = {("grok", "pc"): "pasó el tope de 900s"}
SIN_REGISTRO = {**NO_VERIFICABLE_POR_RUTA, **SIN_ENTRAR, **SIN_TERMINAR}


def _real(cli, maquina):
    d = DATOS / f"{cli}-{maquina}"
    return (
        json.loads((d / "meta.json").read_text(encoding="utf-8")),
        (d / "registro.jsonl").read_text(encoding="utf-8"),
    )


def _sin_subagentes(registro, cli):
    """El mismo registro real sin los eventos que lanzan un subagente."""
    f = ps.FORMATOS[cli]
    return "\n".join(
        json.dumps(o, ensure_ascii=False)
        for o in ps._objetos(registro)
        if not any(
            n.lower().rsplit(".", 1)[-1] in f.subagente for n, _ in f.extrae(o)[0]
        )
    )


class TestRegistrosReales(unittest.TestCase):
    """C.2: registros que ops/pstack/mide.sh trajo de cada CLI en cada máquina."""

    def _cada(self):
        for cli in ps.FORMATOS:
            for mq in MAQUINAS:
                with self.subTest(cli=cli, maquina=mq):
                    yield cli, mq

    def test_hay_un_registro_por_cli_y_maquina_de_la_misma_conf(self):
        shas = set()
        for cli, mq in self._cada():
            meta, _ = _real(cli, mq)
            self.assertEqual(meta["cli"], cli)
            self.assertEqual(len(meta["conf_sha"]), 40)
            shas.add(meta["conf_sha"])
        self.assertEqual(len(shas), 1, "los registros son de confs distintas")

    def test_huella_ve_el_subagente_en_cada_cli_y_maquina(self):
        for cli, mq in self._cada():
            if (cli, mq) in SIN_REGISTRO:
                continue
            _, registro = _real(cli, mq)
            h = ps.huella(registro, cli)
            self.assertTrue(h.legible)
            self.assertGreaterEqual(len(h.subagentes), 1)
            self.assertEqual(ps.juzga(h, EXIGENCIA), ps.Cumple())

    def test_un_registro_real_sin_subagentes_da_falta(self):
        for cli, mq in self._cada():
            if (cli, mq) in SIN_REGISTRO:
                continue
            _, registro = _real(cli, mq)
            h = ps.huella(_sin_subagentes(registro, cli), cli)
            self.assertTrue(h.legible, "un lector roto no puede saltarse la prueba")
            v = ps.juzga(h, EXIGENCIA)
            self.assertIsInstance(v, ps.Falta)
            self.assertIn("lanzó 0 subagente(s)", v.texto())

    def test_un_cli_sin_flag_de_registro_en_su_ruta_no_se_puede_verificar_y_lo_dice(
        self,
    ):
        for (cli, mq), motivo in NO_VERIFICABLE_POR_RUTA.items():
            with self.subTest(cli=cli, maquina=mq):
                meta, registro = _real(cli, mq)
                self.assertNotIn(FLAG_QUE_FALTA[cli], meta["plantilla"], motivo)
                self.assertEqual(meta["rc"], 0)
                h = ps.huella(registro, cli)
                self.assertFalse(h.legible, motivo)
                self.assertEqual(ps.juzga(h, EXIGENCIA), ps.Cumple(verificable=False))

    def test_un_cli_que_no_entra_a_la_maquina_lo_dice_con_el_motivo(self):
        for (cli, mq), motivo in SIN_ENTRAR.items():
            with self.subTest(cli=cli, maquina=mq):
                meta, registro = _real(cli, mq)
                self.assertIn(motivo.split(":")[0], meta["registro_cola"] + registro)
                self.assertNotEqual(meta["rc"], 0)
                h = ps.huella(registro, cli)
                self.assertNotEqual(ps.juzga(h, EXIGENCIA), ps.Cumple())

    def test_un_cli_que_pasa_el_tope_queda_sin_registro_y_no_cumple(self):
        for (cli, mq), motivo in SIN_TERMINAR.items():
            with self.subTest(cli=cli, maquina=mq):
                meta, registro = _real(cli, mq)
                self.assertIn(motivo, meta["error"])
                h = ps.huella(registro, cli)
                self.assertNotEqual(ps.juzga(h, EXIGENCIA), ps.Cumple())

    def test_ningun_cli_medido_vio_el_intento_del_motor(self):
        for cli, mq in self._cada():
            meta, registro = _real(cli, mq)
            self.assertNotIn("/.motor/intentos/", registro)
            self.assertNotIn("MOTOR_ATTEMPT_DIR", registro + meta["registro_cola"])


REPO = Path(__file__).resolve().parent.parent
MIDE_SH = (REPO / "ops" / "pstack" / "mide.sh").read_text(encoding="utf-8")


def _heredoc(i: int) -> str:
    return MIDE_SH.split("<<'PY'\n")[i].split("\nPY\n")[0]


FALSO = """#!/bin/sh
[ "$1" = --version ] && { echo 0; exit 0; }
%s
"""


class TestMide(unittest.TestCase):
    """mide.sh lanza cada CLI como lo haría el motor y no le pasa nada del motor que lo mide."""

    def _corre_mide(
        self,
        extra_env=None,
        rutas='[rutas.sonnet]\ncli = "claude"\nargv = ["claude", "-p", "{prompt}"]\n',
        modelos='[carriles]\ncodigo = ["sonnet"]\n',
        falsos=None,
        secretos="",
        tope=60,
    ):
        falsos = falsos or {"claude": "env\npwd"}
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            conf, home, bin_, tmpdir = t / "conf", t / "home", t / "bin", t / "tmp"
            for d in (
                conf / "pstack",
                conf / "pstack-motor",
                home / ".motor",
                bin_,
                tmpdir,
            ):
                d.mkdir(parents=True)
            (conf / "pstack" / "x").write_text("x")
            (conf / "pstack-motor" / "x").write_text("x")
            (conf / "rutas.toml").write_text(rutas)
            (conf / "modelos.toml").write_text(modelos)
            (home / ".motor" / "maquina.toml").write_text(f'path_extra = ["{bin_}"]\n')
            (home / ".motor" / "secretos.env").write_text(secretos)
            for cli, cuerpo in falsos.items():
                (bin_ / cli).write_text(FALSO % cuerpo)
                (bin_ / cli).chmod(0o755)
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("CLAUDE_CONFIG_DIR", "GROK_HOME")
                and not k.startswith("MOTOR_")
            }
            env.update(HOME=str(home), TMPDIR=str(tmpdir), **(extra_env or {}))
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                for n in ("pstack", "pstack-motor"):
                    tf.add(conf / n, arcname=n)
            prompt = ef.PLANTILLA_PROMPT.format(ruta="@@BRIEF@@", kb="@@KB@@")
            datos = {
                "conf_sha": "abc1234",
                "carriles": {
                    k: list(v) for k, v in tomllib.loads(modelos)["carriles"].items()
                },
                "raiz": "/RAIZ-LOCAL",
                "marca_brief": "@@BRIEF@@",
                "marca_kb": "@@KB@@",
                "tar": base64.b64encode(buf.getvalue()).decode(),
                "clis": {
                    cli: {"encargo": f"# encargo {cli}\n", "prompt": prompt}
                    for cli in ps.FORMATOS
                },
            }
            cabeza = base64.b64encode(json.dumps(datos).encode()).decode()
            p = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-",
                    "mac",
                    str(tope),
                    base64.b64encode(rutas.encode()).decode(),
                ],
                input=f'DATOS_B64 = "{cabeza}"\n' + _heredoc(1),
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
                check=False,
            )
            sobrante = sorted(x.name for x in tmpdir.iterdir())
        linea = [x for x in p.stdout.splitlines() if x.startswith("MIDE-JSON:")][-1]
        salida = json.loads(linea[len("MIDE-JSON:") :])
        salida["sobrante"] = sobrante
        return salida

    def _claude(self, **kw):
        s = self._corre_mide(**kw)
        self.assertEqual(s["error"], "")
        return next(c for c in s["corridas"] if c["cli"] == "claude")

    def test_un_cli_medido_no_ve_variables_motor(self):
        c = self._claude(
            extra_env={
                "MOTOR_ATTEMPT_DIR": "/tmp/intento",
                "MOTOR_RESULT": "/tmp/intento/result.json",
                "MOTOR_OTRA": "1",
            }
        )
        self.assertEqual(c["error"], "")
        self.assertIn("HOME=", c["registro"])
        self.assertNotIn("MOTOR_", c["registro"])

    def test_corre_en_su_carpeta_temporal(self):
        c = self._claude()
        self.assertIn("mide-pstack-claude-", c["registro"])

    def test_el_argv_es_la_ruta_tal_cual_sin_flags_por_cli(self):
        c = self._claude(falsos={"claude": 'printf "ARG:%s\\n" "$@"'})
        args = [x[4:] for x in c["registro"].splitlines() if x.startswith("ARG:")]
        self.assertEqual(len(args), 2)
        self.assertEqual(args[0], "-p")
        self.assertIn("ejecútalo completo sin preguntar nada", args[1])
        self.assertIn("(1 KB)", args[1], "el KB es el del encargo que se escribió aquí")
        self.assertNotIn("@@", args[1], "las marcas se sustituyen antes de lanzar")

    def test_los_marcadores_pstack_y_sesion_se_sustituyen_y_el_transcript_se_lee(self):
        rutas = (
            '[rutas.sonnet]\ncli = "claude"\n'
            'argv = ["claude", "-p", "--session-id", "{sesion}", "--plugin-dir", "{pstack}/pstack", "{prompt}"]\n'
        )
        falso = (
            'printf "ARG:%s\\n" "$@"\n'
            'sid=$3; d="$HOME/.claude/projects/$(pwd -P | sed "s/[^A-Za-z0-9]/-/g")"\n'
            'mkdir -p "$d"; echo \'{"type":"system"}\' > "$d/$sid.jsonl"'
        )
        c = self._claude(rutas=rutas, falsos={"claude": falso})
        args = [x[4:] for x in c["registro"].splitlines() if x.startswith("ARG:")]
        self.assertRegex(args[2], r"^[0-9a-f]{8}-[0-9a-f-]{27}$")
        self.assertTrue(args[4].endswith("/pstack"))
        self.assertNotIn("{", "".join(args[:5]))
        self.assertEqual(len(c["extras"]), 1)
        self.assertIn(args[2], c["extras"][0]["fuente"])
        self.assertIn('"type":"system"', c["extras"][0]["texto"])

    def test_todos_los_cli_reciben_los_secretos_de_la_maquina(self):
        s = self._corre_mide(
            rutas='[rutas.sonnet]\ncli = "claude"\nargv = ["claude", "-p", "{prompt}"]\n'
            '[rutas.qwen]\ncli = "qwen"\nargv = ["qwen", "{prompt}"]\n',
            falsos={"claude": "env", "qwen": "env"},
            secretos="UNA_LLAVE=abc123\n",
        )
        for c in s["corridas"]:
            if c["cli"] in ("claude", "qwen"):
                with self.subTest(cli=c["cli"]):
                    self.assertIn("UNA_LLAVE=abc123", c["registro"])

    def test_el_tope_mata_tambien_a_los_nietos(self):
        with tempfile.TemporaryDirectory() as t:
            pidfile = Path(t) / "nieto.pid"
            c = self._claude(
                falsos={"claude": 'sleep 300 &\necho $! > "$PIDFILE"\nwait'},
                extra_env={"PIDFILE": str(pidfile)},
                tope=2,
            )
            self.assertIn("pasó el tope", c["error"])
            pid = int(pidfile.read_text())
            time.sleep(0.5)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_la_corrida_que_pasa_el_tope_se_reintenta_una_vez_y_vale_la_segunda(self):
        # El API del modelo a veces se cuelga a media corrida (mac/opencode, intento 11):
        # una sola corrida colgada no puede hundir la medición si la segunda corre bien.
        with tempfile.TemporaryDirectory() as t:
            marca = Path(t) / "intentos"
            falso = (
                'echo x >> "$MARCA"\n'
                '[ "$(wc -l < "$MARCA" | tr -d " ")" -lt 2 ] && sleep 300\n'
                'printf \'{"type":"system"}\\n\'\n'
            )
            c = self._claude(
                falsos={"claude": falso}, extra_env={"MARCA": str(marca)}, tope=2
            )
            self.assertEqual(c["rc"], 0)
            self.assertEqual(c["error"], "")
            self.assertEqual(c["reintentos"], 1)
            self.assertIn('{"type":"system"}', c["registro"])

    def test_el_reintento_del_tope_es_uno_solo(self):
        c = self._claude(falsos={"claude": "sleep 300"}, tope=2)
        self.assertIsNone(c["rc"])
        self.assertIn("pasó el tope", c["error"])
        self.assertEqual(c["reintentos"], 1)

    def test_la_que_no_pasa_el_tope_no_se_reintenta(self):
        c = self._claude(falsos={"claude": "exit 1"})
        self.assertEqual(c["rc"], 1)
        self.assertEqual(c["reintentos"], 0)

    def test_no_deja_carpetas_temporales_y_la_conf_no_queda_junto_a_la_corrida(self):
        s = self._corre_mide(falsos={"claude": 'find "$TMPDIR" -name rutas.toml'})
        c = next(c for c in s["corridas"] if c["cli"] == "claude")
        self.assertNotIn("rutas.toml", c["registro"])
        self.assertEqual(s["sobrante"], [])

    def _grok_falso(self):
        return r"""
sid=s-$$; d="$HOME/.grok/sessions/x/$sid"; mkdir -p "$d"
printf '{"tool_name":"spawn_subagent","args":{"agent":"a"}}\n{"tool_name":"spawn_subagent","args":{"agent":"b"}}\n' >"$d/updates.jsonl"
printf '{\n  "text": "listo",\n  "sessionId": "%s",\n  "num_turns": 3\n}\n' "$sid"
"""

    def _corre_grok(self):
        s = self._corre_mide(
            rutas='[rutas.grok]\ncli = "grok"\n'
            'argv = ["grok", "--prompt-file", "{brief}", "--always-approve", "--output-format", "json"]\n',
            modelos='[carriles]\ncodigo = ["grok"]\n',
            falsos={"grok": self._grok_falso()},
        )
        self.assertEqual(s["error"], "")
        return next(c for c in s["corridas"] if c["cli"] == "grok")

    def test_grok_con_el_objeto_final_con_sangria_trae_su_updates_jsonl_y_cumple(self):
        c = self._corre_grok()
        self.assertIn('\n  "sessionId": "s-', c["registro"])
        self.assertEqual(len(c["extras"]), 1)
        ns = {"__name__": "agrega"}
        exec(compile(_heredoc(2), "mide.sh:agrega", "exec"), ns)
        estado, _, fuente, _ = ns["juicio_de"](c)
        self.assertEqual(estado, "Cumple")
        self.assertIn("updates.jsonl", fuente)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ses = tmp / "sessions" / "x" / c["extras"][0]["id"]
        ses.mkdir(parents=True)
        (ses / "updates.jsonl").write_text(c["extras"][0]["texto"], encoding="utf-8")
        log = tmp / "log.txt"
        log.write_text(c["registro"], encoding="utf-8")
        patcher = mock.patch.dict(os.environ, GROK_HOME=str(tmp))
        patcher.start()
        self.addCleanup(patcher.stop)
        regs = ef.Effects._registros_de({"cli": "grok", "registros": [str(log)]})
        self.assertEqual(regs[0], str(ses / "updates.jsonl"))

    def test_escribe_datos_conserva_la_carpeta_de_una_maquina_que_no_respondio(self):
        ns = {"__name__": "agrega"}
        exec(compile(_heredoc(2), "mide.sh:agrega", "exec"), ns)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "grok-mini" / "viejo").mkdir(parents=True)
        (tmp / "grok-mac" / "viejo").mkdir(parents=True)
        ns["escribe_datos"](
            tmp, ["mac", "mini"], [("mac", {"cli": "grok", "registro": "x\n"}, "x\n")]
        )
        self.assertTrue((tmp / "grok-mini" / "viejo").exists())
        self.assertFalse((tmp / "grok-mac" / "viejo").exists())
        self.assertTrue((tmp / "grok-mac" / "meta.json").exists())

    def test_una_conf_que_no_se_puede_leer_es_un_error_no_una_medicion_vacia(self):
        casos = {
            "carriles vacíos": dict(modelos="[carriles]\n"),
            "modelo sin ruta": dict(modelos='[carriles]\ncodigo = ["otro"]\n'),
            "rutas vacías": dict(rutas=""),
        }
        for nombre, kw in casos.items():
            with self.subTest(caso=nombre):
                s = self._corre_mide(**kw)
                self.assertNotEqual(s["error"], "")
                self.assertEqual(s["corridas"], [])

    def test_sin_las_rutas_en_el_argv_no_mide_con_las_de_la_conf(self):
        # Sin las rutas en el argv no hay medición: caer al rutas.toml de la conf fue la causa
        # raíz del --rutas ilegible que medía en silencio con las rutas de producción.
        p = subprocess.run(
            [sys.executable, "-I", "-", "mac", "60"],
            input=_heredoc(1),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("MIDE-JSON:", p.stdout)


class TestLogMezclado(unittest.TestCase):
    """runwrap deja stdout y stderr juntos en log.txt: los avisos del CLI no rompen su registro."""

    def test_qwen_json_completo_con_avisos_antes_y_despues(self):
        f, sub, cmd = CASOS["qwen"]
        arreglo = json.dumps(
            [json.loads(x) for x in f(sub, cmd).splitlines()], indent=2
        )
        for registro in (
            arreglo + "\n\nWarning: running headless with --yolo\n",
            "aviso previo\n" + arreglo + "\nWarning: yolo\n",
        ):
            with self.subTest(registro=registro[:20]):
                h = ps.huella(registro, "qwen")
                self.assertTrue(h.legible)
                self.assertEqual(len(h.subagentes), 1)

    def test_muse_exec_json_deja_solo_resultados_de_herramienta(self):
        def resultado(nombre, texto):
            return {
                "payload_type": "tool.result",
                "payload": {
                    "correlation_facts": {"tool_name": nombre},
                    "text": texto,
                },
            }

        registro = jl(
            resultado("read_file", "Read text file `/x/README.md`.\n1|---"),
            resultado("subagent_spawn", '{"status":"accepted"}'),
        )
        h = ps.huella(registro, "muse")
        self.assertEqual(len(h.subagentes), 1)
        self.assertEqual(ps.juzga(h, ps.Exigencia(min_subagentes=1)), ps.Cumple())


FALSO_CLAUDE = r"""#!/bin/sh
[ "$1" = --version ] && { echo 0; exit 0; }
[ -n "$FALSO_ARGV" ] && echo "$*" >>"$FALSO_ARGV"
if [ -n "$FALSO_BRIEFS" ]; then
  for ultimo; do :; done
  ruta=$(printf '%s' "$ultimo" | sed -n 's/^Lee el encargo completo en \(.*\) ([0-9]* KB) con .*/\1/p')
  [ -f "$ruta" ] && cp "$ruta" "$FALSO_BRIEFS/$$.md"
fi
[ "$FALSO_MODO" = nada ] && { echo '{"type":"result","result":"listo"}'; exit 0; }
tool() { printf '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"%s","input":%s}]}}\n' "$1" "$2"; }
tool Agent '{"subagent_type":"general-purpose","description":"uno"}'
tool Agent '{"subagent_type":"general-purpose","description":"dos"}'
"""

# Cada CLI que mide.sh mide y no es claude sale de este falso: anota cómo lo lanzaron y muere,
# para que la prueba no dependa de qué haya instalado en el PATH de esta máquina.
FALSO_INERTE = '#!/bin/sh\necho "$0 $*" >>"$FALSO_LANZADOS"\nexit 1\n'


def _clis():
    """La lista CLIS de mide_py: los CLI que mide.sh mide siempre, haya carril o no."""
    import re

    return json.loads(re.search(r"(?m)^CLIS = (\[.*\])", _heredoc(1)).group(1))


class TestMideDePuntaAPunta(unittest.TestCase):
    """C.3: `mide.sh --juzga` entero, con ssh y uv falsos que corren todo en esta máquina."""

    def _corre(
        self,
        modo,
        args=(),
        argv_log=None,
        lanzados=None,
        briefs=None,
        base=None,
    ):
        import subprocess

        with (
            contextlib.nullcontext(base) if base else tempfile.TemporaryDirectory() as t
        ):
            t = Path(t)
            home, bin_ = t / "home", t / "bin"
            conf = _conf_falsa(t)
            (conf / "modelos.toml").write_text(
                '[carriles]\ncodigo = ["sonnet"]\nrevisar = ["sonnet"]\n'
            )
            for d in (home / ".motor", bin_):
                d.mkdir(parents=True)
            (home / ".motor" / "maquina.toml").write_text(f'path_extra = ["{bin_}"]\n')
            (bin_ / "claude").write_text(FALSO_CLAUDE)
            for cli in _clis():
                if cli != "claude":
                    (bin_ / cli).write_text(FALSO_INERTE)
            (bin_ / "ssh").write_text('#!/bin/sh\nshift 5\nexec bash -c "$*"\n')
            (bin_ / "uv").write_text('#!/bin/sh\nshift 4\nexec python3 "$@"\n')
            for f in bin_.iterdir():
                f.chmod(0o755)
            env = {
                k: v
                for k, v in os.environ.items()
                if k != "CLAUDE_CONFIG_DIR" and not k.startswith("MOTOR_")
            }
            env.update(
                HOME=str(home),
                PATH=f"{bin_}:{env['PATH']}",
                MOTOR_CONF_REAL=str(conf),
                MOTOR_PC_UV=str(bin_ / "uv"),
                MIDE_TOPE_S="60",
                FALSO_MODO=modo,
                FALSO_ARGV=str(t / "argv.log"),
                FALSO_LANZADOS=str(t / "lanzados.log"),
                FALSO_BRIEFS=str(t / "briefs"),
                MIDE_HOME=str(t / "casa"),
            )
            (t / "briefs").mkdir()
            antes = sorted((REPO / "tests" / "datos" / "pstack").rglob("*"))
            r = subprocess.run(
                [
                    "bash",
                    str(REPO / "ops" / "pstack" / "mide.sh"),
                    "--juzga",
                    *args,
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
                check=False,
            )
            despues = sorted((REPO / "tests" / "datos" / "pstack").rglob("*"))
            if argv_log is not None:
                registro = t / "argv.log"
                argv_log.extend(
                    registro.read_text().splitlines() if registro.exists() else []
                )
            if briefs is not None:
                briefs.extend(
                    p.read_text(encoding="utf-8")
                    for p in sorted((t / "briefs").glob("*.md"))
                )
            if lanzados is not None:
                registro = t / "lanzados.log"
                lanzados.extend(
                    registro.read_text().splitlines() if registro.exists() else []
                )
        self.assertEqual(antes, despues)
        return r

    def test_juzga_sale_cero_cuando_el_cli_del_carril_cumple_en_las_tres(self):
        r = self._corre("cumple")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("Cumple"), 3 + 1)
        for mq in MAQUINAS:
            self.assertRegex(r.stdout, rf"(?m)^{mq}\s+claude\s+Cumple$")

    def test_el_remoto_escribe_el_encargo_del_motor_con_la_raiz_cambiada_y_el_prompt_sin_marca(
        self,
    ):
        import re

        from .test_mide import encargo_del_motor

        with tempfile.TemporaryDirectory() as base:
            base = Path(base)
            briefs, lanzadas = [], []
            r = self._corre("cumple", argv_log=lanzadas, briefs=briefs, base=base)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            esperado, raiz = encargo_del_motor(base / "conf", base / "casa", "claude")
        self.assertEqual(len(briefs), 3)
        for brief in briefs:
            copia = re.search(
                r"`([^`\n]+)/pstack/skills/poteto-mode/SKILL\.md`", brief
            ).group(1)
            self.assertIn("mide-pstack-copia-", copia)
            self.assertNotIn(raiz, brief)
            self.assertEqual(brief, esperado.replace(raiz, copia))
        self.assertEqual(len(lanzadas), 3)
        for linea in lanzadas:
            self.assertNotIn("@@BRIEF@@", linea)
            ruta = re.search(r"Lee el encargo completo en (\S+) \(", linea).group(1)
            self.assertIn(ef.prompt_de_encargo(ruta, esperado), linea)

    def test_mide_toma_por_omision_las_rutas_de_conf_ejemplo_y_no_las_de_la_conf(self):
        lanzadas = []
        r = self._corre("cumple", argv_log=lanzadas)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(lanzadas)
        for linea in lanzadas:
            self.assertIn("--session-id", linea)
            self.assertIn("--output-format json", linea)

    def test_rutas_cambia_el_archivo_con_el_que_se_mide(self):
        with tempfile.TemporaryDirectory() as t:
            otras = Path(t) / "otras.toml"
            otras.write_text(
                '[rutas.sonnet]\ncli = "claude"\nargv = ["claude", "--solo-estas", "{prompt}"]\n'
            )
            lanzadas = []
            self._corre("cumple", args=("--rutas", str(otras)), argv_log=lanzadas)
        self.assertTrue(lanzadas)
        for linea in lanzadas:
            self.assertIn("--solo-estas", linea)
            self.assertNotIn("--session-id", linea)

    def test_rutas_con_un_archivo_que_no_existe_sale_2_sin_medir(self):
        lanzadas, lanzados = [], []
        r = self._corre(
            "cumple",
            args=("--rutas", "/no/existe.toml"),
            argv_log=lanzadas,
            lanzados=lanzados,
        )
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no puedo leer /no/existe.toml", r.stderr)
        self.assertEqual(lanzadas, [])
        self.assertEqual(lanzados, [])

    def test_los_cli_que_no_son_claude_se_miden_con_los_falsos_de_bin(self):
        # Sin un falso por CLI en bin_, mide.sh lanzaría los CLI reales del PATH.
        lanzados = []
        r = self._corre("cumple", lanzados=lanzados)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(lanzados)
        ceros = [x.split(" ", 1)[0] for x in lanzados]
        padres = {str(Path(c).parent) for c in ceros}
        self.assertEqual(len(padres), 1, lanzados)
        nombres = {Path(c).name for c in ceros}
        for cli in _clis():
            if cli != "claude":
                self.assertIn(cli, nombres)

    def test_rutas_vacio_sale_2_sin_medir(self):
        # Un --rutas legible pero vacío no puede medir en silencio con las rutas de la conf.
        with tempfile.TemporaryDirectory() as t:
            vacio = Path(t) / "vacio.toml"
            vacio.write_text("")
            lanzadas = []
            r = self._corre("cumple", args=("--rutas", str(vacio)), argv_log=lanzadas)
            mensaje = f"no puedo leer {vacio}"
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(mensaje, r.stderr)
        self.assertEqual(lanzadas, [])

    def test_rutas_una_carpeta_sale_2_sin_medir(self):
        with tempfile.TemporaryDirectory() as t:
            lanzadas, lanzados = [], []
            r = self._corre(
                "cumple", args=("--rutas", t), argv_log=lanzadas, lanzados=lanzados
            )
            mensaje = f"no puedo leer {t}"
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(mensaje, r.stderr)
        self.assertEqual(lanzadas, [])
        self.assertEqual(lanzados, [])

    def test_rutas_sin_permiso_de_lectura_sale_2_sin_medir(self):
        with tempfile.TemporaryDirectory() as t:
            ilegible = Path(t) / "rutas.toml"
            ilegible.write_text("[rutas]\n")
            ilegible.chmod(0)
            lanzadas, lanzados = [], []
            r = self._corre(
                "cumple",
                args=("--rutas", str(ilegible)),
                argv_log=lanzadas,
                lanzados=lanzados,
            )
            mensaje = f"no puedo leer {ilegible}"
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(mensaje, r.stderr)
        self.assertEqual(lanzadas, [])
        self.assertEqual(lanzados, [])

    def test_juzga_sale_distinto_de_cero_si_el_cli_no_lanza_subagentes(self):
        r = self._corre("nada")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("NO cumple", r.stderr)
        self.assertNotIn("Cumple en las tres máquinas", r.stdout)


def _corrida(cli, registro, error=""):
    return {
        "cli": cli,
        "registro": registro,
        "extras": [],
        "error": error,
    }


def _salida(maquina, corridas, carriles=("claude", "opencode"), error=""):
    r = {
        "maquina": maquina,
        "conf_sha": "abc",
        "carriles": list(carriles),
        "corridas": corridas,
        "error": error,
    }
    return {"out": "MIDE-JSON:" + json.dumps(r) + "\n", "err": "", "rc": "0"}


def _cumple(cli):
    f, sub, _cmd = CASOS[cli]
    return f(sub, sub)


class TestJuzgaDeMide(unittest.TestCase):
    """C.3: lo que decide --juzga es una función pura de las salidas de las tres máquinas."""

    @classmethod
    def setUpClass(cls):
        ns = {"__name__": "agrega"}
        exec(compile(_heredoc(2), "mide.sh:agrega", "exec"), ns)
        cls.agrega = staticmethod(ns["agrega"])
        cls.juicio = staticmethod(ns["juicio_de"])

    def _todas(self, cambia=None, carriles=("claude",)):
        salidas = {}
        for mq in MAQUINAS:
            corridas = [_corrida("claude", _cumple("claude"))]
            if cambia:
                corridas = cambia(mq, corridas)
            salidas[mq] = _salida(mq, corridas, carriles=carriles)
        return salidas

    def test_grok_solo_usa_el_updates_jsonl_de_un_sessionid_que_el_motor_leeria(self):
        extra = {"id": "s9", "fuente": "updates.jsonl", "texto": _cumple("grok")}
        anidado = json.dumps({"usage": {"sessionId": "s9"}})
        c = {**_corrida("grok", anidado), "extras": [extra]}
        self.assertEqual(self.juicio(c)[0], "NO VERIFICABLE")
        propio = json.dumps({"text": "listo", "sessionId": "s9"})
        c = {**_corrida("grok", propio), "extras": [extra]}
        self.assertEqual(self.juicio(c)[0], "Cumple")

    def test_todos_los_cli_de_los_carriles_cumplen_no_deja_problemas(self):
        filas, problemas, _ = self.agrega(self._todas(), list(MAQUINAS))
        self.assertEqual(problemas, [])
        self.assertEqual(len(filas), 3)
        self.assertTrue(all("Cumple" in f for f in filas))

    def test_un_cli_de_carril_con_falta_es_problema(self):
        def cambia(mq, corridas):
            if mq == "mini":
                corridas[0] = _corrida("claude", claude(("Bash", {"command": "ls"})))
            return corridas

        _, problemas, _ = self.agrega(self._todas(cambia), list(MAQUINAS))
        self.assertEqual(len(problemas), 1)
        self.assertIn("mini/claude: Falta", problemas[0])

    def test_un_cli_de_carril_no_verificable_es_problema(self):
        def cambia(mq, corridas):
            corridas[0] = _corrida("claude", '{"type":"result","result":"listo"}')
            return corridas

        _, problemas, _ = self.agrega(self._todas(cambia), list(MAQUINAS))
        self.assertEqual(len(problemas), 3)
        self.assertIn("NO VERIFICABLE", problemas[0])

    def test_una_maquina_sin_mide_json_es_problema(self):
        salidas = self._todas()
        salidas["pc"] = {"out": "", "err": "ssh: timeout", "rc": "255"}
        _, problemas, _ = self.agrega(salidas, list(MAQUINAS))
        self.assertEqual(len(problemas), 1)
        self.assertIn("pc: no respondió", problemas[0])

    def test_un_cli_de_carril_sin_corrida_es_problema(self):
        def cambia(mq, corridas):
            return [] if mq == "pc" else corridas

        _, problemas, _ = self.agrega(self._todas(cambia), list(MAQUINAS))
        self.assertEqual(problemas, ["pc/claude: sin medición"])

    def test_una_corrida_con_error_es_problema_aunque_su_registro_cumpla(self):
        def cambia(mq, corridas):
            corridas[0] = _corrida(
                "claude", _cumple("claude"), error="pasó el tope de 900s"
            )
            return corridas

        _, problemas, _ = self.agrega(self._todas(cambia), list(MAQUINAS))
        self.assertEqual(len(problemas), 3)
        self.assertIn("pasó el tope", problemas[0])

    def test_carriles_vacios_no_son_cumple(self):
        salidas = {mq: _salida(mq, [], carriles=()) for mq in MAQUINAS}
        filas, problemas, _ = self.agrega(salidas, list(MAQUINAS))
        self.assertEqual(filas, [])
        self.assertTrue(problemas)

    def test_si_una_maquina_trae_carriles_vacios_es_problema(self):
        salidas = self._todas()
        salidas["mac"] = _salida(
            "mac", [_corrida("claude", _cumple("claude"))], carriles=()
        )
        _, problemas, _ = self.agrega(salidas, list(MAQUINAS))
        self.assertTrue(any("mac" in p and "carriles" in p for p in problemas))

    def test_un_error_de_la_medicion_es_problema(self):
        salidas = self._todas()
        salidas["mini"] = _salida(
            "mini", [], error="RuntimeError: no pude leer modelos.toml"
        )
        _, problemas, _ = self.agrega(salidas, list(MAQUINAS))
        self.assertIn("mini: RuntimeError: no pude leer modelos.toml", problemas)

    def test_un_cli_fuera_de_los_carriles_con_falta_solo_se_reporta(self):
        def cambia(mq, corridas):
            return corridas + [_corrida("qwen", claude(("Bash", {"command": "ls"})))]

        filas, problemas, _ = self.agrega(self._todas(cambia), list(MAQUINAS))
        self.assertEqual(problemas, [])
        self.assertEqual(len(filas), 6)

    def test_el_registro_guardado_conserva_las_llamadas_aunque_recorte_los_textos(self):
        ns = {"__name__": "agrega"}
        exec(compile(_heredoc(2), "mide.sh:agrega", "exec"), ns)
        f, sub, cmd = CASOS["qwen"]
        arreglo = [json.loads(x) for x in f(sub, cmd).splitlines()]
        arreglo.append({"type": "user", "texto": "x" * 5000})
        crudo = json.dumps(arreglo) + "\n\nWarning: yolo\n"
        corto = ns["registro_corto"](crudo)
        self.assertLess(len(corto), len(crudo))
        h = ps.huella(corto, "qwen")
        self.assertEqual(len(h.subagentes), 1)
        self.assertEqual(h.comandos, ("git status",))

    def test_el_primer_registro_legible_es_el_que_lee_el_motor(self):
        c = _corrida("claude", '{"type":"result","result":"listo"}')
        c["extras"] = [{"fuente": "transcript", "texto": _cumple("claude")}]
        estado, h, fuente, _ = self.juicio(c)
        self.assertEqual((estado, fuente), ("Cumple", "transcript"))
        c["extras"] = []
        self.assertEqual(self.juicio(c)[0], "NO VERIFICABLE")

    def test_el_script_sale_distinto_de_cero_sin_medir_nada(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for mq in MAQUINAS:
                (d / f"{mq}.out").write_text(_salida(mq, [], carriles=())["out"])
                (d / f"{mq}.err").write_text("")
                (d / f"{mq}.rc").write_text("0")
            p = subprocess.run(
                [sys.executable, "-I", "-", str(d), str(REPO), "mac mini pc", "1"],
                input=_heredoc(2),
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Cumple en las tres máquinas", p.stdout)

    def test_el_script_sale_cero_solo_si_todo_cumple(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for mq, s in self._todas().items():
                (d / f"{mq}.out").write_text(s["out"])
                (d / f"{mq}.err").write_text("")
                (d / f"{mq}.rc").write_text("0")
            p = subprocess.run(
                [sys.executable, "-I", "-", str(d), str(REPO), "mac mini pc", "1"],
                input=_heredoc(2),
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Cumple en las tres máquinas", p.stdout)

    def test_la_opcion_juzga_existe_y_cualquier_otra_se_rechaza(self):
        import subprocess

        sh = REPO / "ops" / "pstack" / "mide.sh"
        r = subprocess.run(
            ["bash", str(sh), "--nada"], capture_output=True, text=True, check=False
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("uso:", r.stderr)

    def test_mide_juzga_no_escribe_en_tests_datos(self):
        cola = _heredoc(2).split('if __name__ == "__main__":')[-1]
        juzga, no_juzga = cola.split("    else:\n")
        self.assertNotIn("escribe_datos", juzga)
        self.assertIn("escribe_datos", no_juzga)


if __name__ == "__main__":
    unittest.main()
