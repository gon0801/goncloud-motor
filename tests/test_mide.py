"""C.6: `ops/pstack/mide.sh` arma el encargo con el código del motor y lanza con su mismo prompt."""

from __future__ import annotations

import ast
import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from motor import effects as ef
from motor import machine as m
from motor import pstack as ps
from motor.conf import RepoConf, load_conf
from motor.plan import parse_plan

from .test_guias import _conf_falsa

REPO = Path(__file__).resolve().parent.parent
MIDE_SH = REPO / "ops" / "pstack" / "mide.sh"
ARMA_PY = REPO / "ops" / "pstack" / "arma_encargo.py"
CONF_REAL = os.environ.get("MOTOR_CONF_REAL", "")


def _modulo_arma():
    spec = importlib.util.spec_from_file_location("arma_encargo", ARMA_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def encargo_del_motor(conf_dir: Path, casa: Path, cli: str) -> tuple[str, str]:
    """(encargo, raiz de la copia fija) tal como `start_attempt` armaría el de la medición: del
    plan de `arma_encargo` en adelante lo arma el motor, sin pasar por el script."""
    conf = load_conf(conf_dir)
    celda = conf.pstack.exigencia("gate", "implementar")
    conf = replace(
        conf,
        pstack=replace(
            conf.pstack,
            exigencias={
                **conf.pstack.exigencias,
                ("gate", "implementar"): replace(celda, min_subagentes=2),
            },
        ),
    )
    fx = ef.Effects(
        SimpleNamespace(
            home=casa,
            conf=conf,
            ws=SimpleNamespace(rama=lambda p, b: f"motor/{p}/{b}"),
        )
    )
    trabajo = casa / "trabajo"
    trabajo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=trabajo, check=True)
    plan = parse_plan(_modulo_arma().PLAN)
    d = casa / f"intento-{cli}"
    d.mkdir(parents=True, exist_ok=True)
    a = m.StartAttempt("A", "implementar", "sonnet", 1, "a" * 40, (), False)
    info = fx._prepara_pstack(d, "gate", "implementar", cli, trabajo, "mide", False)
    rc = RepoConf.parse('[repo]\nprincipal = "main"\n')
    return fx._encargo(plan, plan.block("A"), a, trabajo, rc, info), info["raiz"]


class TestMide(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-mide-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.casa = self.tmp / "casa"
        self.conf = Path(CONF_REAL) if CONF_REAL else _conf_falsa(self.tmp)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("MOTOR_")},
            "MOTOR_CONF_REAL": str(self.conf),
            "MIDE_HOME": str(self.casa),
        }

    def test_encargo_es_el_del_motor_byte_por_byte_y_el_prompt_el_compartido(self):
        datos = REPO / "tests" / "datos" / "pstack"
        antes = sorted(str(p) for p in datos.rglob("*"))
        r = subprocess.run(
            ["bash", str(MIDE_SH), "--encargo"],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(antes, sorted(str(p) for p in datos.rglob("*")))
        marca_encargo, marca_prompt = "=== encargo ===\n", "=== prompt ===\n"
        self.assertTrue(r.stdout.startswith(marca_encargo))
        encargo, prompt = r.stdout[len(marca_encargo) :].split(marca_prompt)
        esperado, _ = encargo_del_motor(self.conf, self.casa, "claude")
        self.assertEqual(encargo, esperado)
        brief = self.casa / "intento-claude" / "brief.md"
        self.assertEqual(prompt, ef.prompt_de_encargo(str(brief), esperado) + "\n")
        self.assertEqual(brief.read_text(encoding="utf-8"), esperado)

    def test_encargo_con_cli_arma_el_de_ese_cli(self):
        r = subprocess.run(
            ["bash", str(MIDE_SH), "--encargo", "--cli", "kimi"],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        esperado, _ = encargo_del_motor(self.conf, self.casa, "kimi")
        self.assertIn(esperado, r.stdout)

    def test_cli_sin_encargo_sale_2(self):
        r = subprocess.run(
            ["bash", str(MIDE_SH), "--cli", "kimi"],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
            check=False,
        )
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_los_datos_que_viajan_traen_el_encargo_del_motor_de_cada_cli(self):
        r = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ARMA_PY),
                str(REPO),
                str(self.conf),
                str(self.casa),
                "datos",
            ],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        datos = json.loads(base64.b64decode(r.stdout))
        self.assertEqual(set(datos["clis"]), set(ps.FORMATOS))
        for cli, x in datos["clis"].items():
            with self.subTest(cli=cli):
                esperado, raiz = encargo_del_motor(self.conf, self.casa, cli)
                self.assertEqual(x["encargo"], esperado)
                self.assertEqual(datos["raiz"], raiz)
                self.assertEqual(
                    x["prompt"],
                    ef.prompt_de_encargo(datos["marca_brief"], esperado),
                )
                self.assertIn("al menos 2 subagente(s)", x["encargo"])

    def test_el_script_no_escribe_ningun_titulo_del_encargo(self):
        for archivo in (MIDE_SH, ARMA_PY):
            texto = archivo.read_text(encoding="utf-8")
            for titulo in (
                "## Anexo",
                "## Cómo trabajas",
                "## Cómo se entrega",
                "Lo que manda el motor",
                "ejecútalo completo",
            ):
                self.assertNotIn(titulo, texto, archivo.name)


class TestPromptCompartido(unittest.TestCase):
    def test_el_prompt_dice_la_ruta_el_tamano_y_la_herramienta_de_lectura(self):
        p = ef.prompt_de_encargo("/x/brief.md", "a" * 2048)
        self.assertIn("/x/brief.md (2 KB)", p)
        self.assertIn("herramienta de lectura de archivos, no con `cat`", p)
        self.assertIn("ejecútalo completo sin preguntar nada", p)

    def test_start_attempt_lanza_con_el_prompt_de_la_funcion_compartida(self):
        arbol = ast.parse((REPO / "motor" / "effects.py").read_text(encoding="utf-8"))
        inicio = next(
            n
            for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name == "start_attempt"
        )
        asignado = {
            tgt.id
            for n in ast.walk(inicio)
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "id", "") == "prompt_de_encargo"
            for tgt in n.targets
            if isinstance(tgt, ast.Name)
        }
        lanzadas = [
            kw.value
            for n in ast.walk(inicio)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "argv_de_ruta"
            for kw in n.keywords
            if kw.arg == "prompt"
        ]
        self.assertEqual(len(lanzadas), 1)
        self.assertIsInstance(lanzadas[0], ast.Name)
        self.assertIn(lanzadas[0].id, asignado)


if __name__ == "__main__":
    unittest.main()
