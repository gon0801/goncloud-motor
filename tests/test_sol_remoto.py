import contextlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class SolRemotoTest(unittest.TestCase):
    def test_ssh_y_codex_conservan_prompt_sha_y_resultado_sin_ejecutar_el_prompt(self):
        ruta = Path(__file__).resolve().parents[1] / "ops" / "sol_remoto.py"
        spec = importlib.util.spec_from_file_location("sol_remoto", ruta)
        sol = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sol)
        with tempfile.TemporaryDirectory(prefix="sol-frontera-") as temporal:
            raiz = Path(temporal)
            repo = raiz / "repo"
            repo.mkdir()
            for args in (
                ("init", "-q"),
                ("config", "user.email", "test@example.invalid"),
                ("config", "user.name", "Prueba"),
            ):
                subprocess.run(["git", *args], cwd=repo, check=True)
            (repo / "dato.txt").write_text("contenido versionado\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            captura, control = raiz / "captura.json", raiz / "control.json"
            falso = raiz / "codex"
            falso.write_text(
                "#!/usr/bin/env python3\nimport json, pathlib, sys\n"
                f"captura = pathlib.Path({str(captura)!r})\n"
                f"control = json.loads(pathlib.Path({str(control)!r}).read_text())\n"
                "args = sys.argv[1:]\nprompt = sys.stdin.read()\n"
                "if '_error' in control:\n"
                " print(control['_error'], file=sys.stderr)\n sys.exit(9)\n"
                "captura.write_text(json.dumps({'args': args, 'prompt': prompt, "
                "'cwd': str(pathlib.Path.cwd()), 'dato': pathlib.Path('dato.txt').read_text(), "
                "'git': pathlib.Path('.git').exists(), "
                "'schema': json.loads(pathlib.Path(args[args.index('--output-schema') + 1]).read_text())}))\n"
                "pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(control))\n"
            )
            falso.chmod(0o700)
            resultado = raiz / "result.json"
            brief = raiz / "brief.txt"
            intruso = raiz / "no-debe-existir"
            prompt = f"Revisa 'comillas' y `touch {intruso}` y $(touch {intruso}).\nGuia real."
            brief.write_text(prompt)
            popen = subprocess.Popen
            ssh_windows = str(raiz / "Git" / "usr" / "bin" / "ssh.exe")
            ssh_esperado = ssh_windows

            def ssh_falso(args, **kwargs):
                if args[0] not in ("ssh", ssh_windows):
                    return popen(args, **kwargs)
                self.assertEqual(
                    args[:5],
                    [
                        ssh_esperado,
                        "-T",
                        "-oBatchMode=yes",
                        "-oConnectTimeout=10",
                        "mac",
                    ],
                )
                self.assertNotIn(prompt, args[-1])
                remoto = shlex.split(args[-1])
                self.assertEqual(remoto[:2], ["/opt/homebrew/bin/python3", "-c"])
                prefijo = (
                    "import subprocess\n_real = subprocess.Popen\n"
                    "def _codex(args, **kwargs):\n"
                    " assert args[0] == '/opt/homebrew/bin/codex'\n"
                    f" args[0] = {str(falso)!r}\n"
                    " return _real(args, **kwargs)\nsubprocess.Popen = _codex\n"
                )
                return popen(["python3", "-c", prefijo + remoto[2]], **kwargs)

            esperado = {
                "sha": sha,
                "veredicto": "CAMBIOS",
                "bloqueantes": [
                    {
                        "texto": "Falta real",
                        "repro": "python3 caso.py",
                        "arreglo": "Corregir",
                    }
                ],
                "notas": ["Nota del modelo"],
            }
            anterior = Path.cwd()
            try:
                os.chdir(repo)
                with patch.object(sol.subprocess, "Popen", side_effect=ssh_falso):
                    control.write_text(json.dumps(esperado))
                    with patch.object(
                        sol,
                        "os",
                        SimpleNamespace(
                            name="nt",
                            environ={"ProgramFiles": str(raiz)},
                            replace=os.replace,
                        ),
                    ):
                        sol.main(["--brief", str(brief), "--result", str(resultado)])
                    ssh_esperado = "ssh"
                    self.assertEqual(json.loads(resultado.read_text()), esperado)
                    control.write_text(json.dumps({**esperado, "sha": "0" * 40}))
                    with self.assertRaises((ValueError, RuntimeError)):
                        sol.main(["--brief", str(brief), "--result", str(resultado)])
                    self.assertEqual(json.loads(resultado.read_text()), esperado)
                    for categoria, original, seguro in (
                        ("auth", "Authentication required", "Authentication required"),
                        ("cuota", "quota exceeded", "quota exceeded"),
                        (
                            "modelo_no_disponible",
                            "model is disabled for this account",
                            "model is disabled for this account",
                        ),
                        ("transitorio", "connection error", "connection error"),
                        (
                            "desconocido",
                            "Falla no clasificada",
                            "Codex termino con codigo 9",
                        ),
                    ):
                        with self.subTest(error=categoria):
                            control.write_text(
                                json.dumps(
                                    {"_error": original + " secreto-nunca-publicar"}
                                )
                            )
                            resultado.unlink(missing_ok=True)
                            stderr = io.StringIO()
                            with (
                                contextlib.redirect_stderr(stderr),
                                self.assertRaises(RuntimeError),
                            ):
                                sol.main(
                                    ["--brief", str(brief), "--result", str(resultado)]
                                )
                            self.assertIn(categoria, stderr.getvalue())
                            self.assertIn(seguro, stderr.getvalue())
                            self.assertNotIn(
                                "secreto-nunca-publicar", stderr.getvalue()
                            )
                            self.assertFalse(resultado.exists())
            finally:
                os.chdir(anterior)
            leido = json.loads(captura.read_text())
            self.assertIn(prompt, leido["prompt"])
            self.assertIn(sha, leido["prompt"])
            self.assertEqual(leido["dato"], "contenido versionado\n")
            self.assertFalse(leido["git"])
            self.assertFalse(Path(leido["cwd"]).exists())
            self.assertFalse(intruso.exists())
            self.assertIn("gpt-5.6-sol", leido["args"])
            self.assertIn("read-only", leido["args"])
            self.assertIn('model_reasoning_effort="medium"', leido["args"])
            self.assertEqual(leido["schema"]["properties"]["sha"]["const"], sha)


if __name__ == "__main__":
    unittest.main()
