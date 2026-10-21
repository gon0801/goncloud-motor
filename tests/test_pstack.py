"""B.1: huella, juicio, puerta e instrucciones, con un registro de ejemplo por CLI.

Los registros imitan la forma que `docs/arena/pstack/hechos.md` §4 midió en cada CLI. El bloque C los
reemplaza por registros reales de cada máquina.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from motor import effects as ef
from motor import machine as m
from motor import pstack as ps
from motor import relevo
from motor.__main__ import conf_valida
from motor.conf import load_conf
from motor.plan import Plan, parse_plan

SKILL = "/Users/dn/.motor/pstack/abc123/pstack/skills/poteto-mode/SKILL.md"
PLAYBOOK = "/Users/dn/.motor/pstack/abc123/pstack/skills/poteto-mode/playbooks/docs.md"


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


# por CLI: (fábrica, cargar skill, leer archivo, subagente, lista de tareas, comando)
CASOS = {
    "claude": (
        claude,
        ("Skill", {"skill": "pstack:poteto-mode"}),
        ("Read", {"file_path": PLAYBOOK}),
        ("Agent", {"subagent_type": "poteto-agent"}),
        ("TodoWrite", {"todos": []}),
        ("Bash", {"command": "git status"}),
    ),
    "kimi": (
        kimi,
        ("Skill", {"skill": "poteto-mode"}),
        ("Read", {"path": PLAYBOOK}),
        ("Agent", {"subagent_type": "poteto-agent"}),
        ("SetTodoList", {}),
        ("Bash", {"command": "git status"}),
    ),
    "opencode": (
        opencode,
        ("skill", {"name": "poteto-mode"}),
        ("read", {"filePath": PLAYBOOK}),
        ("task", {"subagent_type": "poteto-agent"}),
        ("todowrite", {}),
        ("bash", {"command": "git status"}),
    ),
    "grok": (
        grok,
        ("read_file", {"path": SKILL}),
        ("read_file", {"path": PLAYBOOK}),
        ("spawn_subagent", {"agent": "poteto-agent"}),
        ("todo_write", {}),
        ("run_terminal_command", {"command": "git status"}),
    ),
    "muse": (
        muse,
        ("read_skill", {"name": "poteto-mode"}),
        ("read_file", {"path": PLAYBOOK}),
        ("subagent_spawn", {"subagent_type": "poteto-agent"}),
        ("write_todos", {}),
        ("bash", {"command": "git status"}),
    ),
    "qwen": (
        claude,
        ("Read", {"file_path": SKILL}),
        ("read_file", {"file_path": PLAYBOOK}),
        ("task", {"subagent_type": "poteto-agent"}),
        ("todo_write", {}),
        ("run_shell_command", {"command": "git status"}),
    ),
}


class TestFormatos(unittest.TestCase):
    def test_hay_una_fila_por_cli(self):
        self.assertEqual(
            set(ps.FORMATOS), {"claude", "kimi", "opencode", "grok", "muse", "qwen"}
        )

    def test_huella_ve_skill_playbook_lista_subagente_y_comando_en_cada_cli(self):
        for cli, (fab, cargar, playbook, sub, todos, cmd) in CASOS.items():
            with self.subTest(cli=cli):
                h = ps.huella(fab(cargar, playbook, todos, sub, cmd), cli)
                self.assertTrue(h.legible)
                self.assertIn("poteto-mode", h.skills)
                self.assertEqual(h.poteto_en, 1)
                self.assertEqual(h.playbooks, ("docs",))
                self.assertEqual(h.todos, 1)
                self.assertEqual(h.subagentes, ("poteto-agent",))
                self.assertEqual(h.comandos, ("git status",))
                self.assertEqual(h.acciones, 5)

    def test_sin_cargar_poteto_mode_da_falta_en_cada_cli(self):
        for cli, (fab, cargar, playbook, sub, todos, cmd) in CASOS.items():
            with self.subTest(cli=cli):
                h = ps.huella(fab(cmd, todos, sub), cli)
                r = ps.juzga(h, ps.Exigencia())
                self.assertIsInstance(r, ps.Falta)
                self.assertIn("no cargó la skill poteto-mode", r.faltas)

    def test_cargar_leyendo_el_skill_md_cuenta_igual_que_la_herramienta(self):
        h = ps.huella(
            claude(("Read", {"file_path": SKILL})), "claude"
        )  # Claude también puede cargar con Read
        self.assertEqual(h.skills, ("poteto-mode",))
        h = ps.huella(
            claude(("Read", {"file_path": SKILL.replace("/", "\\")})), "claude"
        )
        self.assertEqual(h.skills, ("poteto-mode",), "rutas de Windows")

    def test_las_llamadas_de_un_subagente_no_son_del_agente(self):
        h = ps.huella(
            claude(("Skill", {"skill": "poteto-mode"}), parent="toolu_1"), "claude"
        )
        self.assertNotIn("poteto-mode", h.skills)

    def test_opencode_y_grok_cuentan_una_vez_la_llamada_repetida(self):
        dos = jl(*[{"tool_name": "todo_write", "tool_call_id": "t1", "args": {}}] * 2)
        self.assertEqual(ps.huella(dos, "grok").todos, 1)

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
                                "name": "Skill",
                                "input": {"skill": "poteto-mode"},
                            }
                        ]
                    },
                },
            ],
            indent=2,
        )
        self.assertEqual(ps.huella(arreglo, "qwen").skills, ("poteto-mode",))


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

    def test_registro_legible_sin_acciones_si_falta(self):
        h = ps.huella(jl({"type": "system"}), "claude")
        self.assertTrue(h.legible)
        self.assertIsInstance(ps.juzga(h, ps.Exigencia()), ps.Falta)


class TestJuzga(unittest.TestCase):
    def h(self, *llamadas):
        return ps.huella(claude(*llamadas), "claude")

    def test_cumple(self):
        h = self.h(
            ("Skill", {"skill": "poteto-mode"}),
            ("Skill", {"skill": "interrogate"}),
            ("Read", {"file_path": PLAYBOOK}),
            ("TodoWrite", {}),
            ("Agent", {"subagent_type": "x"}),
        )
        e = ps.Exigencia(
            skills=("poteto-mode", "interrogate"),
            playbook=True,
            todos=True,
            min_subagentes=1,
        )
        self.assertEqual(ps.juzga(h, e), ps.Cumple())

    def test_falta_lista_todo_en_llano(self):
        h = self.h(("Bash", {"command": "ls"}))
        e = ps.Exigencia(
            skills=("poteto-mode", "interrogate"),
            playbook=True,
            todos=True,
            min_subagentes=2,
        )
        r = ps.juzga(h, e)
        self.assertIsInstance(r, ps.Falta)
        self.assertEqual(
            r.faltas,
            (
                "no cargó la skill poteto-mode",
                "no cargó la skill interrogate",
                "no abrió ningún playbook de poteto-mode",
                "no usó su lista de tareas",
                "lanzó 0 subagente(s) y se pedían al menos 2",
            ),
        )

    def test_cargarla_tarde_es_falta(self):
        h = self.h(
            *[("Bash", {"command": "ls"})] * 7, ("Skill", {"skill": "poteto-mode"})
        )
        r = ps.juzga(h, ps.Exigencia())
        self.assertIsInstance(r, ps.Falta)
        self.assertIn("acción 8", r.faltas[0])


class TestPuerta(unittest.TestCase):
    def acciones(self, *llamadas):
        return ps.eventos(claude(*llamadas), "claude")[0]

    def test_corta_si_a_la_accion_6_no_cargo_poteto_mode(self):
        ev = self.acciones(*[("Bash", {"command": "ls"})] * 6)
        self.assertTrue(ps.puerta(ev, 6))

    def test_no_corta_antes_de_la_accion_6(self):
        self.assertFalse(
            ps.puerta(self.acciones(*[("Bash", {"command": "ls"})] * 5), 6)
        )

    def test_no_corta_si_la_cargo_a_tiempo(self):
        ev = self.acciones(
            *[("Bash", {"command": "ls"})] * 5, ("Skill", {"skill": "poteto-mode"})
        )
        self.assertFalse(ps.puerta(ev, 6))
        self.assertFalse(
            ps.puerta(self.acciones(*[("Bash", {"command": "ls"})] * 9), 0)
        )


class TestInstrucciones(unittest.TestCase):
    def test_la_seccion_sale_de_la_misma_exigencia_que_juzga(self):
        e = ps.Exigencia(
            skills=("poteto-mode", "interrogate"),
            playbook=True,
            todos=True,
            min_subagentes=2,
        )
        t = ps.instrucciones(e, "claude", "/x/pstack/abc")
        self.assertTrue(t.startswith("## Cómo trabajas"))
        for esperado in (
            "antes de tu acción número 6",
            "`interrogate`",
            "playbook",
            "lista de tareas",
            "al menos 2 subagente",
            "/x/pstack/abc/pstack-motor/claude.md",
            "no tu resumen",
        ):
            self.assertIn(esperado, t)

    def test_cli_sin_herramienta_de_skills_pide_leer_el_skill_md(self):
        t = ps.instrucciones(ps.Exigencia(), "grok", "/x")
        self.assertIn("una lectura de su `SKILL.md`", t)
        self.assertNotIn("playbook", t)


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


if __name__ == "__main__":
    unittest.main()
