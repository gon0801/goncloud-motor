"""Parte pura: plan, decisiones, configuración, avisos y piezas de Windows que no necesitan Windows."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from motor import machine as m
from motor.conf import ConfError, RepoConf, load_maquina
from motor.__main__ import conf_valida, doctor
from motor.github import veredicto
from motor.notify import PLANTILLAS, AvisoInvalido, lint_plain, render
from motor.plan import PlanError, _celdas, parse_plan
from motor.plataforma.windows import (
    flags_creacion,
    resolve_shim,
    tarea_xml,
    CREATE_BREAKAWAY_FROM_JOB,
)
from motor.servicio import plist_launchd
from motor.store import Event

PLAN = """# Demo
autopiloto: motor
plan: demo
repo: git@github.com:x/app.git

## Bloque A — uno
lane: gate
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | algo | true |

## Bloque C — otro repo
lane: gate
repo: git@github.com:x/docs.git
prueba-real: true
| Fila | Contenido | Acepta |
| C.1 | algo | true |
"""


def ev(kind: str, block: str, data: dict, i=[0]) -> Event:
    i[0] += 1
    return Event(i[0], time.time(), "demo", block, kind, data)


def pol(impl=("sonnet", "glm"), rev=("opus", "k3")) -> m.Policy:
    cuenta = {"sonnet": "anthropic", "opus": "anthropic", "glm": "zai", "k3": "kimi"}
    return m.Policy(
        implementadores=impl,
        revisores=rev,
        cuenta=cuenta,
        max_parallel={c: 2 for c in set(cuenta.values())},
    )


def entregado(block="A", modelo="glm", sha="a" * 40, n=1):
    return [
        ev(
            "AttemptStarted",
            block,
            {"n": n, "role": "implementar", "modelo": modelo, "proc": {}, "dir": "d"},
        ),
        ev(
            "AttemptEnded",
            block,
            {
                "n": n,
                "role": "implementar",
                "outcome": {"kind": "entregado", "sha": sha},
            },
        ),
    ]


class PlanTest(unittest.TestCase):
    def test_multirepo(self):
        p = parse_plan(PLAN)
        self.assertEqual(p.block("A").repo, "git@github.com:x/app.git")
        self.assertEqual(p.block("C").repo, "git@github.com:x/docs.git")

    def test_rechaza_lo_que_paralizaria_despues(self):
        malo = "# x\nplan: x\n## Bloque A — a\nlane: gate\n| Fila | Contenido | Acepta |\n| A.1 | a | - |\n"
        with self.assertRaises(PlanError) as cm:
            parse_plan(malo, min_implementadores=2, implementadores=1)
        t = "\n".join(cm.exception.errores)
        for x in (
            "autopiloto: motor",
            "sin repo",
            "ninguna fila trae 'Acepta'",
            "prueba-real",
            "se exigen 2",
        ):
            self.assertIn(x, t)

    def test_formato_de_la_skill_valida(self):
        """El plan que enseña skill/motor/SKILL.md, con su bloque humano, pasa la validación."""
        skill = (
            Path(__file__).parent.parent / "skill" / "motor" / "SKILL.md"
        ).read_text()
        self.assertIn("`lane: fast`", skill)
        p = parse_plan(
            PLAN
            + "\n## Bloque H — login\nlane: fast\nhumano: login entra a Seller Central\n"
            "sonda-humano: test -f x\n"
        )
        self.assertEqual(p.block("H").lane, "fast")


class MachineTest(unittest.TestCase):
    def snap(self, eventos):
        return m.fold(parse_plan(PLAN), eventos)

    def test_merge_exige_revision_listo_y_checks_sobre_el_mismo_sha(self):
        base = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 7}),
            ev(
                "Reviewed",
                "A",
                {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []},
            ),
        ]
        self.assertEqual(
            m.merge_gate(self.snap(base).blocks["A"]),
            "el PR no está marcado listo sobre ese SHA",
        )
        base.append(ev("MarkedReady", "A", {"sha": "a" * 40}))
        s = self.snap(
            base + [ev("ChecksResult", "A", {"sha": "b" * 40, "green": True})]
        )
        self.assertIsInstance(m.merge_gate(s.blocks["A"]), str)
        s = self.snap(
            base + [ev("ChecksResult", "A", {"sha": "a" * 40, "green": True})]
        )
        self.assertEqual(m.merge_gate(s.blocks["A"]), m.MergeTicket("A", "a" * 40, 7))

    def test_la_cuota_es_por_cuenta(self):
        now = time.time()
        s = self.snap(
            [ev("Cooldown", "", {"cuenta": "anthropic", "hasta": now + 3600})]
        )
        inicio = [a for a in m.decide(s, pol(), now) if isinstance(a, m.StartAttempt)]
        self.assertEqual(
            {a.modelo for a in inicio},
            {"glm"},
            "sonnet comparte cuenta con opus: ninguno entra",
        )

    def test_revisor_relevo_sin_autores_ni_cuentas_en_cuota(self):
        now = time.time()
        eventos = entregado(modelo="glm") + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
            ev("Cooldown", "", {"cuenta": "anthropic", "hasta": now + 3600}),
        ]
        rev = [
            a
            for a in m.decide(self.snap(eventos), pol(), now)
            if isinstance(a, m.StartAttempt) and a.role == "revisar"
        ]
        self.assertEqual(
            rev[0].modelo, "k3"
        )  # opus en cuota (cuenta anthropic), glm es autor
        self.assertNotIn("glm", m.revisores_para(self.snap(eventos).blocks["A"], pol()))

    def test_un_corte_de_luz_no_cuenta_como_falla(self):
        now = time.time()
        eventos = []
        for n in (1, 2, 3):
            eventos += [
                ev(
                    "AttemptStarted",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "modelo": "sonnet",
                        "proc": {},
                        "dir": "d",
                    },
                ),
                ev(
                    "AttemptEnded",
                    "A",
                    {"n": n, "role": "implementar", "outcome": {"kind": "corte"}},
                ),
            ]
        a = [
            x
            for x in m.decide(self.snap(eventos), pol(), now)
            if isinstance(x, m.StartAttempt) and x.block == "A"
        ]
        self.assertEqual(
            a[0].modelo, "sonnet", "tres cortes no hacen que se releve al agente"
        )
        self.assertIn("corte de luz", " ".join(a[0].feedback))

    def test_cambios_sin_repro_cuenta_como_aprobado(self):
        s = self.snap(
            entregado()
            + [
                ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
                ev("PrOpened", "A", {"numero": 1}),
                ev(
                    "Reviewed",
                    "A",
                    {
                        "sha": "a" * 40,
                        "veredicto": "CAMBIOS",
                        "bloqueantes": [{"texto": "estilo", "repro": ""}],
                    },
                ),
            ]
        )
        self.assertIs(m.estado(s, "A"), m.Estado.LISTO)

    def test_no_existe_fallado_ni_pausado(self):
        self.assertFalse(
            {e.name for e in m.Estado} & {"FALLADO", "FAILED", "PAUSADO", "PAUSED"}
        )

    def test_clasifica(self):
        c = m.classify(
            1, False, False, "429 weekly limit reached, resets at 2026-10-23T09:00:00Z"
        )
        self.assertEqual((c["kind"], c["reset_at"]), ("cuota", "2026-10-23T09:00:00Z"))
        self.assertEqual(
            m.classify(1, False, False, "Your macOS login keychain is locked")["kind"],
            "auth",
        )


class PedidoDeModelosTest(unittest.TestCase):
    """David: "haz este plan con DeepSeek como revisor y Qwen como implementer" (2026-10-20)."""

    PEDIDO = PLAN.replace(
        "repo: git@github.com:x/app.git\n",
        "repo: git@github.com:x/app.git\nimplementa: Qwen\nrevisa: DeepSeek\n",
        1,
    )

    def pol(self):
        cuenta = {
            "sonnet": "anthropic",
            "opus": "anthropic",
            "glm": "zai",
            "k3": "kimi",
            "qwen": "qwen",
            "deepseek": "deepseek",
        }
        return m.Policy(
            implementadores=("sonnet", "glm"),
            revisores=("opus", "k3"),
            cuenta=cuenta,
            max_parallel={c: 2 for c in set(cuenta.values())},
        )

    def test_lee_el_pedido_sin_importar_mayusculas(self):
        p = parse_plan(self.PEDIDO, modelos={"qwen", "deepseek", "sonnet"})
        self.assertEqual((p.implementa, p.revisa), (("qwen",), ("deepseek",)))
        self.assertEqual(
            type(p).from_dict(p.to_dict()), p, "el pedido sobrevive al registro"
        )

    def test_modelo_desconocido_se_rechaza_con_la_lista(self):
        with self.assertRaises(PlanError) as cm:
            parse_plan(self.PEDIDO, modelos={"sonnet", "opus"})
        self.assertIn(
            "no conozco el modelo qwen, deepseek; los que hay: opus, sonnet",
            str(cm.exception),
        )

    def test_nadie_revisa_su_propio_trabajo(self):
        mismo = self.PEDIDO.replace("revisa: DeepSeek", "revisa: qwen")
        with self.assertRaises(PlanError) as cm:
            parse_plan(mismo)
        self.assertIn("nadie revisa su propio trabajo", str(cm.exception))

    def test_un_solo_implementador_pedido_no_choca_con_el_minimo(self):
        parse_plan(self.PEDIDO, min_implementadores=2, implementadores=1)

    def test_el_motor_usa_lo_pedido_y_no_la_tabla(self):
        now = time.time()
        p = parse_plan(self.PEDIDO)
        inicio = [
            a
            for a in m.decide(m.fold(p, []), self.pol(), now)
            if isinstance(a, m.StartAttempt)
        ]
        self.assertEqual({a.modelo for a in inicio}, {"qwen"})
        eventos = entregado(modelo="qwen") + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
        ]
        rev = [
            a
            for a in m.decide(m.fold(p, eventos), self.pol(), now)
            if isinstance(a, m.StartAttempt) and a.role == "revisar"
        ]
        self.assertEqual(rev[0].modelo, "deepseek")
        sin_cuota = eventos + [
            ev("Cooldown", "", {"cuenta": "deepseek", "hasta": now + 3600})
        ]
        rev = [
            a
            for a in m.decide(m.fold(p, sin_cuota), self.pol(), now)
            if isinstance(a, m.StartAttempt) and a.role == "revisar"
        ]
        self.assertEqual(
            rev,
            [],
            "si DeepSeek no tiene cuota se espera; no entra otro que David no pidió",
        )


class RevisionGrokTest(unittest.TestCase):
    """Hallazgos de la revisión de Grok a MOTOR 01 (2026-10-20)."""

    PASOS = (
        PLAN
        + """
## Bloque F — instalar
lane: gate
prueba-real: true
| Fila | Contenido | Acepta |
| F.1 | instalar | true |

## Bloque E — llaves
lane: fast
depende: F
humano: login pega las llaves
"""
    )

    def test_no_pide_el_paso_humano_antes_de_sus_dependencias(self):
        s = m.fold(parse_plan(self.PASOS), [])
        self.assertIs(m.estado(s, "E"), m.Estado.ESPERANDO)
        avisos = [
            a
            for a in m.decide(s, pol(), time.time())
            if isinstance(a, m.Notify) and a.template == "humano"
        ]
        self.assertEqual(
            avisos, [], "a David no se le pide nada que todavía no puede hacer"
        )

    def test_pipe_escapado_queda_dentro_de_la_celda(self):
        self.assertEqual(
            _celdas(
                r"| G.4 | cerrar | gh pr view 37 --json state -q .state \| grep -q CLOSED |"
            ),
            ["G.4", "cerrar", "gh pr view 37 --json state -q .state | grep -q CLOSED"],
        )

    def test_checks_por_bloque(self):
        p = parse_plan(
            PLAN.replace(
                "lane: gate\nprueba-real: true\n| Fila",
                "lane: gate\nchecks: espejo-de-g, gate\nprueba-real: true\n| Fila",
                1,
            )
        )
        self.assertEqual(p.block("A").checks, ("espejo-de-g", "gate"))
        self.assertEqual(
            type(p).from_dict(p.to_dict()).block("A").checks, ("espejo-de-g", "gate")
        )

    def test_check_en_matriz(self):
        corre = [
            {
                "name": f"completa ({i}, 3)",
                "status": "completed",
                "conclusion": "success",
            }
            for i in (1, 2)
        ]
        corre.append(
            {"name": "completa (3, 3)", "status": "in_progress", "conclusion": None}
        )
        v = veredicto("s", ("completa",), corre)
        self.assertEqual((v.terminal, v.faltan), (False, ("completa",)))
        corre[-1] = {
            "name": "completa (3, 3)",
            "status": "completed",
            "conclusion": "failure",
        }
        self.assertEqual(veredicto("s", ("completa",), corre).fallas, ("completa",))
        corre[-1]["conclusion"] = "success"
        self.assertTrue(veredicto("s", ("completa",), corre).green)
        self.assertEqual(
            veredicto("s", ("comp",), corre).faltan,
            ("comp",),
            "un prefijo suelto no cuenta",
        )
        saltado = [{"name": "completa", "status": "completed", "conclusion": "skipped"}]
        self.assertEqual(
            veredicto("s", ("completa",), saltado).faltan,
            ("completa",),
            "saltado en borrador no es aprobado (MOTOR 01, PR #484)",
        )

    def test_checks_que_nunca_terminan_avisan_una_vez(self):
        p, now = parse_plan(PLAN), time.time()
        base = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
            ev(
                "Reviewed",
                "A",
                {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []},
            ),
            ev("MarkedReady", "A", {"sha": "a" * 40}),
        ]
        base[-1] = Event(
            base[-1].id, now - 7 * 3600, "demo", "A", "MarkedReady", {"sha": "a" * 40}
        )
        acc = [
            a for a in m.decide(m.fold(p, base), pol(), now) if isinstance(a, m.Notify)
        ]
        self.assertIn("atorado:A", [a.key for a in acc])

    def test_efecto_que_falla_una_hora_avisa(self):
        p, now = parse_plan(PLAN), time.time()
        fallas = [
            Event(
                900 + i,
                now - h,
                "demo",
                "A",
                "EffectFailed",
                {"accion": "OpenPr", "error": "red"},
            )
            for i, h in enumerate((4000, 10))
        ]
        acc = [
            a
            for a in m.decide(m.fold(p, entregado() + fallas), pol(), now)
            if isinstance(a, m.Notify)
        ]
        self.assertIn("atorado:A", [a.key for a in acc])
        acc = [
            a
            for a in m.decide(m.fold(p, entregado() + fallas[1:]), pol(), now)
            if isinstance(a, m.Notify)
        ]
        self.assertNotIn(
            "atorado:A", [a.key for a in acc], "una falla suelta no molesta a David"
        )

    def test_doctor_busca_en_path_extra_de_la_maquina(self):
        with tempfile.TemporaryDirectory() as t:
            h, binario = Path(t), Path(t) / "bin"
            binario.mkdir()
            ejecutable = binario / ("claude.cmd" if os.name == "nt" else "claude")
            ejecutable.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n")
            ejecutable.chmod(0o755)
            conf = Path(__file__).resolve().parent.parent / "conf-ejemplo"
            # Rutas entre comillas simples (literales): en Windows el `\` no es escape de TOML.
            (h / "maquina.toml").write_text(
                f"nombre = 'PC'\nconf = '{conf}'\npath_extra = ['{binario}']\n"
            )
            viejo = os.environ.get("PATH", "")
            os.environ["PATH"] = os.pathsep.join(  # git sí; los CLIs de agentes no
                [str(Path(shutil.which("git")).parent)]
                if os.name == "nt"
                else ["/usr/bin", "/bin"]
            )
            try:
                r = doctor(h)
            finally:
                os.environ["PATH"] = viejo
            self.assertTrue(r["rutas"]["sonnet"]["ejecutable"])
            self.assertFalse(r["rutas"]["k3"]["ejecutable"])


class AvanceCadaHoraTest(unittest.TestCase):
    """David: "prefiero cada hora" (2026-10-20). Si el resumen deja de llegar, algo se cayó."""

    def eventos(self, horas: float, now: float):
        t0 = now - horas * 3600
        return [
            Event(1, t0, "demo", "", "PlanLoaded", {}),
            Event(
                2,
                now - 40 * 60,
                "demo",
                "A",
                "AttemptStarted",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "sonnet",
                    "proc": {},
                    "dir": "d",
                },
            ),
        ]

    def avisos(self, evs, now):
        return [
            a
            for a in m.decide(m.fold(parse_plan(PLAN), evs), pol(), now)
            if isinstance(a, m.Notify) and a.template == "avance"
        ]

    def test_un_resumen_por_hora_con_lo_que_se_hace(self):
        now = time.time()
        self.assertEqual(
            self.avisos(self.eventos(0.5, now), now),
            [],
            "antes de la primera hora, nada",
        )
        a = self.avisos(self.eventos(2.2, now), now)
        self.assertEqual([x.key for x in a], ["avance:2"])
        texto = render("avance", dict(a[0].campos), "Mac", "Motor de planes")
        self.assertIn("0 de 2 partes listas.", texto)
        self.assertIn("Ahora trabajo en «uno» (Sonnet, 40 min).", texto)
        self.assertIn("Nada atorado.", texto)
        ya = self.eventos(2.2, now) + [
            Event(3, now, "demo", "", "NoticeQueued", {"key": "avance:2", "texto": "x"})
        ]
        self.assertEqual(self.avisos(ya, now), [], "una vez por hora")

    def test_plan_terminado_no_manda_resumen(self):
        now = time.time()
        p = parse_plan(PLAN)
        s = m.fold(p, self.eventos(3, now))
        for bv in s.blocks.values():
            bv.merged.append({"sha": "a" * 40, "commit": "c"})
            bv.proved["a" * 40] = {"ok": True}
        self.assertFalse(
            [
                a
                for a in m.decide(s, pol(), now)
                if isinstance(a, m.Notify) and a.template == "avance"
            ]
        )

    def test_el_resumen_no_se_pierde_por_un_titulo_con_jerga(self):
        texto = render(
            "avance",
            {
                "listas": "3",
                "total": "9",
                "ahora": "Ahora hago rebase del commit",
                "atoron": "",
            },
            "Mini",
            "Plan",
        )
        self.assertIn("3 de 9 partes listas. Sigo trabajando.", texto)


class ConfValidaTest(unittest.TestCase):
    def test_conf_compartida_y_maquinas(self):
        import shutil

        raiz = Path(__file__).resolve().parent.parent
        self.assertEqual(conf_valida(raiz / "conf-ejemplo"), [])
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "conf"
            shutil.copytree(raiz / "conf-ejemplo", d)
            (d / "maquinas").mkdir()
            (d / "maquinas" / "mac.toml").write_text(
                'nombre = "Mac"\nconf = "~/c"\n[avisos]\nchat = "1"\n'
            )
            (d / "maquinas" / "pc.toml").write_text('nombre = "PC"\n')
            (d / "maquinas" / "mini.toml").write_text(
                'nombre = "Mini"\nconf = "~/c"\n[no_dormir]\nencender = "pmset 1"\napagar = []\n'
            )
            with open(d / "rutas.toml", "a") as f:
                f.write(
                    '\n[rutas.x]\ncuenta = "a"\ncli = "x"\nargv = ["x"]\nenv = { K = "sk-abcdefghijklmnopqrstuvwxyz123456" }\n'
                )
            texto = "\n".join(conf_valida(d))
        for esperado in (
            "mac.toml: maquina.toml no puede traer ['avisos']",
            "pc.toml: maquina.toml necesita 'nombre' y 'conf'",
            "mini.toml: [no_dormir] lleva dos listas",
            "ruta x: el comando no recibe el encargo",
            "forma de llave",
        ):
            self.assertIn(esperado, texto)


class ConfTest(unittest.TestCase):
    def test_maquina_no_puede_traer_papeles_ni_modelos(self):
        with tempfile.TemporaryDirectory() as t:
            h = Path(t)
            (h / "maquina.toml").write_text(
                'nombre = "Mini"\nconf = "/x"\n[carriles]\ncodigo = ["glm"]\n'
            )
            with self.assertRaises(ConfError) as cm:
                load_maquina(h)
            self.assertIn("conf compartida", str(cm.exception))

    @unittest.skipUnless(
        os.name == "posix", "los permisos 0600 son de POSIX; en Windows no aplican"
    )
    def test_secretos_deben_ser_privados(self):
        with tempfile.TemporaryDirectory() as t:
            h = Path(t)
            (h / "maquina.toml").write_text('nombre = "Mini"\nconf = "/x"\n')
            (h / "secretos.env").write_text("X=1\n")
            os.chmod(h / "secretos.env", 0o644)
            with self.assertRaises(ConfError):
                load_maquina(h)
            os.chmod(h / "secretos.env", 0o600)
            self.assertEqual(dict(load_maquina(h).secretos), {"X": "1"})

    def test_repo_conf(self):
        rc = RepoConf.parse(
            '[repo]\nprincipal="master"\n[github]\nchecks_extra=["completa"]\n[deploy]\ndesplegar="d.sh"\n'
        )
        self.assertEqual(
            (rc.principal, rc.checks_extra, rc.desplegar),
            ("master", ("completa",), "d.sh"),
        )


class AvisosTest(unittest.TestCase):
    def test_plantillas_con_maquina_y_filtro(self):
        campos = {
            "n": "1",
            "total": "3",
            "aviso": "ya puedes bloquear búsquedas",
            "prueba": "pasó",
            "que_hacer": "entra a Seller Central",
            "hasta": str(time.time() + 3600),
            "bloque": "el despliegue",
            "listas": "2",
            "ahora": "Ahora trabajo en «el reporte» (Sonnet, 12 min).",
            "atoron": "Nada atorado.",
        }
        for t in PLANTILLAS:
            texto = render(t, campos, "Mini", "Reporte de ventas")
            self.assertTrue(texto.startswith("En la Mini · Reporte de ventas:"))

    def test_jerga_del_plan_cae_a_texto_generico(self):
        texto = render(
            "entregado",
            {
                "n": "1",
                "total": "1",
                "aviso": "commit 3eda9854 listo",
                "prueba": "pasó",
            },
            "PC",
            "x",
        )
        self.assertNotIn("3eda9854", texto)

    def test_rechaza(self):
        for malo in (
            "Listo 3eda9854",
            "lee /Users/dn/x.txt",
            "el commit quedó",
            "\n".join("x" * 9),
        ):
            with self.assertRaises(AvisoInvalido):
                lint_plain(malo)


class ServicioTest(unittest.TestCase):
    def test_windows_resuelve_shims_cmd(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "claude.cmd").write_text("@echo off")
            argv = resolve_shim("claude", t)
            self.assertEqual(argv[1:4], ["/d", "/s", "/c"])
            self.assertTrue(argv[-1].endswith("claude.cmd"))

    def test_windows_grupo_propio_fuera_del_job(self):
        self.assertTrue(flags_creacion() & CREATE_BREAKAWAY_FROM_JOB)

    def test_tarea_arranca_sin_sesion(self):
        x = tarea_xml("ehven", r"C:\uv\python.exe", r"C:\Users\ehven\.motor")
        for esperado in (
            "<BootTrigger>",
            "<LogonType>Password</LogonType>",
            "<ExecutionTimeLimit>PT0S",
            "motor servicio",
        ):
            self.assertIn(esperado, x)

    def test_launchd_arranca_sin_sesion(self):
        p = plist_launchd(
            "gon", "/usr/bin/python3", "/Users/gon/.motor", "/Users/gon/motor"
        )
        for esperado in (
            "<key>UserName</key><string>gon</string>",
            "<key>KeepAlive</key><true/>",
            "LaunchDaemons",
        ):
            self.assertIn(esperado, p)


if __name__ == "__main__":
    unittest.main()
