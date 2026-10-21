"""B.1: huella, juicio, puerta e instrucciones, con un registro de ejemplo por CLI.

Los registros imitan la forma que `docs/arena/pstack/hechos.md` §4 midió en cada CLI. El bloque C los
reemplaza por registros reales de cada máquina.
"""

from __future__ import annotations

import json
import unittest

from motor import pstack as ps

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


if __name__ == "__main__":
    unittest.main()
