"""Fase 8: la puerta unica del cuerpo del PR y los candados de secretos."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos import ciclo
from motor.encargos.ciclo import (
    _LIMITE_CUERPO_PR,
    _anotar_nota_en_pr_tardia,
    _cuerpo_pr,
    _entregar_con_pendientes,
    ahora_iso,
)
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import _bloqueante, _entrega_pr

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = (
    ".gitleaks.toml",
    ".gitleaksignore",
    ".pre-commit-config.yaml",
    "motor/encargos/trabajos_ci.py",
    "motor/encargos/comprobacion.py",
)


def _arbol_temporal(tmp: str) -> Path:
    raiz = Path(tmp)
    for relativa in CONFIG:
        destino = raiz / relativa
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RAIZ / relativa, destino)
    return raiz


def _correr(raiz: Path, modo: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RAIZ / "tools" / "candados.py"), modo, "--raiz", str(raiz)],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )


class ElCuerpoConservaSuFinal(unittest.TestCase):
    def test_un_cuerpo_de_70_kb_conserva_la_linea_final_y_las_notas(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp)
            interno = espacio / ".interno"
            interno.mkdir()
            (interno / "cuerpo-pr.md").write_text(
                "# Cambios\n\n" + ("detalle " * 10_000), encoding="utf-8"
            )
            fase = SimpleNamespace(
                id="dos",
                pendientes=("queda el despliegue pendiente",),
                notas=("la nota del motor no se puede perder",),
            )
            enc = SimpleNamespace(
                id="enc_70",
                fase_activa="dos",
                fase_actual=fase,
                fases=(fase,),
                pendientes_entrega=(),
                fuera_de_alcance=(),
                notas_pr=(),
            )

            cuerpo = _cuerpo_pr(enc, espacio, "dos")

            self.assertLessEqual(len(cuerpo), _LIMITE_CUERPO_PR)
            self.assertTrue(
                cuerpo.endswith("Encargo enc_70. Fase: dos."),
                "la linea del encargo tiene que quedar al final",
            )
            self.assertIn(
                "## Notas del motor",
                cuerpo,
                "el recorte no debe perder las notas del motor",
            )
            self.assertIn(
                "la nota del motor no se puede perder",
                cuerpo,
                "el recorte no debe perder el texto de las notas del motor",
            )
            self.assertIn(
                "## Pendientes abiertos al entregar",
                cuerpo,
                "el recorte no debe perder los pendientes del motor",
            )


class UnaSolaPuertaPublicaElCuerpo(unittest.TestCase):
    def test_el_arbol_real_no_tiene_otra_llamada_que_edite_el_cuerpo(self):
        salida = _correr(RAIZ, "cuerpo")

        self.assertEqual(
            salida.returncode,
            0,
            "toda escritura del cuerpo debe pasar por _escribir_cuerpo_pr: "
            + salida.stderr,
        )

    def test_una_llamada_que_edita_el_cuerpo_fuera_de_la_puerta_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = Path(tmp)
            destino = raiz / "motor" / "encargos" / "ciclo.py"
            destino.parent.mkdir(parents=True)
            destino.write_text(
                (RAIZ / "motor" / "encargos" / "ciclo.py").read_text(encoding="utf-8")
                + "\n\ndef _atajo(gh):\n    gh.actualizar_cuerpo('o/r', 1, 'x')\n",
                encoding="utf-8",
            )

            salida = _correr(raiz, "cuerpo")

            self.assertEqual(
                salida.returncode,
                1,
                "otra llamada que edite el cuerpo del PR debe hacer fallar el check",
            )
            self.assertIn("fuera de `_escribir_cuerpo_pr`", salida.stderr)

    def test_una_puerta_nueva_que_escribe_un_cuerpo_sin_limite_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = Path(tmp)
            destino = raiz / "motor" / "encargos" / "ciclo.py"
            destino.parent.mkdir(parents=True)
            destino.write_text(
                (RAIZ / "motor" / "encargos" / "ciclo.py").read_text(encoding="utf-8")
                + "\n\ndef _nota_nueva(gh, repo, numero, cuerpo, nota):\n"
                "    _escribir_cuerpo_pr(gh, repo, numero, cuerpo + '\\n\\n' + nota)\n",
                encoding="utf-8",
            )

            salida = _correr(raiz, "cuerpo")

            self.assertEqual(
                salida.returncode,
                1,
                "una llamada nueva a la puerta sin el cierre debe fallar el check",
            )
            self.assertIn("sin el cierre del motor", salida.stderr)

    def test_abrir_un_pr_con_un_cuerpo_sin_limite_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = Path(tmp)
            destino = raiz / "motor" / "encargos" / "ciclo.py"
            destino.parent.mkdir(parents=True)
            destino.write_text(
                (RAIZ / "motor" / "encargos" / "ciclo.py").read_text(encoding="utf-8")
                + "\n\ndef _abrir_otro(gh, repo, rama, base, texto):\n"
                "    return gh.abrir_pr(repo, rama, base, 't', texto * 1000)\n",
                encoding="utf-8",
            )

            salida = _correr(raiz, "cuerpo")

            self.assertEqual(
                salida.returncode,
                1,
                "abrir un PR con un cuerpo que no pasa por el limite debe fallar",
            )
            self.assertIn("abre un PR sin el limite", salida.stderr)


class CandadosDeSecretos(unittest.TestCase):
    def _revisar(self, raiz: Path) -> subprocess.CompletedProcess:
        return _correr(raiz, "secretos")

    def test_agregar_una_entrada_a_la_allowlist_de_gitleaks_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace("paths = [", "paths = [\n    '''motor/.*''',", 1),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "agregar una ruta a la allowlist de gitleaks debe fallar el check",
            )
            self.assertIn("entrada nueva en la allowlist", salida.stderr)

    def test_una_entrada_nueva_con_razon_y_fecha_es_la_excepcion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "paths = [",
                    "paths = [\n"
                    "    '''motor/.*''',  # excepcion: alcance fijado 2026-11-09",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 0, salida.stderr)

    def test_quitar_un_hook_del_precommit_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".pre-commit-config.yaml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace("      - id: gitleaks\n", "", 1), encoding="utf-8"
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "quitar el hook de secretos debe fallar el check",
            )
            self.assertIn("falta el hook gitleaks", salida.stderr)

    def test_quitar_el_hook_de_tamano_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".pre-commit-config.yaml"
            texto = ruta.read_text(encoding="utf-8")
            self.assertIn("      - id: calidad-tamano\n", texto)
            ruta.write_text(
                texto.replace("      - id: calidad-tamano\n", "", 1), encoding="utf-8"
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "quitar el hook que frena el tamaño de motor/ debe fallar el check",
            )
            self.assertIn("falta el hook calidad-tamano", salida.stderr)

    def test_un_hook_retirado_con_excepcion_anotada_pasa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".pre-commit-config.yaml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace("      - id: gitleaks\n", "", 1)
                + "\n# excepcion: gitleaks retirado por decision 2026-11-09\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 0, salida.stderr)

    def test_saltar_la_bateria_por_el_mensaje_del_commit_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "trabajos_ci.py"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "correr = por_rutas | agregados",
                    "correr = set(extra) or (por_rutas | agregados)",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "el mensaje del commit no puede decidir que jobs corren",
            )
            self.assertIn("puede quitar jobs por el mensaje del commit", salida.stderr)

    def test_quitar_la_regla_de_secretos_del_prompt_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "comprobacion.py"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "Nunca subas credenciales ni llaves a un repo",
                    "Nunca subas datos a un repo",
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "quitar la regla de secretos del prompt debe fallar el check",
            )
            self.assertIn("falta la regla de secretos", salida.stderr)

    def test_una_clave_nueva_en_la_allowlist_de_gitleaks_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto + "\n                commits = ['abc123']\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "una clave nueva dentro de [[allowlists]] debilita el candado",
            )
            self.assertIn("clave nueva en la allowlist", salida.stderr)

    def test_una_stopword_nueva_en_la_allowlist_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto + "\n                stopwords = ['AKIA']\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("clave nueva en la allowlist", salida.stderr)

    def test_quitar_target_rules_de_la_allowlist_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    'targetRules = ["generic-api-key", "sentry-access-token", '
                    '"discord-api-token"]\n',
                    "",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "sin targetRules las rutas quedan permitidas para todas las reglas",
            )
            self.assertIn("targetRules", salida.stderr)

    def test_una_regla_deshabilitada_en_extend_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "useDefault = true",
                    'useDefault = true\ndisabledRules = ["generic-api-key"]',
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "disabledRules apaga reglas de gitleaks",
            )
            self.assertIn("disabledRules", salida.stderr)

    def test_una_regla_deshabilitada_con_excepcion_pasa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "useDefault = true",
                    'useDefault = true\ndisabledRules = ["generic-api-key"]  '
                    "# excepcion: revisado por David 2026-11-09",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 0, salida.stderr)

    def test_apagar_las_reglas_por_defecto_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace("useDefault = true", "useDefault = false", 1),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode, 1, "apagar useDefault apaga todas las reglas"
            )
            self.assertIn("useDefault", salida.stderr)

    def test_la_bateria_no_se_apaga_por_el_mensaje_con_una_interseccion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "trabajos_ci.py"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "correr = por_rutas | agregados",
                    "correr = (por_rutas & set(extra)) if extra else "
                    "(por_rutas | agregados)",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "la interseccion con el mensaje deja jobs sin correr",
            )
            self.assertIn("puede quitar jobs por el mensaje del commit", salida.stderr)

    def test_la_bateria_no_se_apaga_por_el_mensaje_fuera_de_jobs_extra(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "trabajos_ci.py"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "        extra = _jobs_extra(mensaje_de_git(head)) if head else ()",
                    '        if "[sin-bateria]" in mensaje_de_git(head):\n'
                    "            return _calcular((), ())\n"
                    "        extra = _jobs_extra(mensaje_de_git(head)) if head else ()",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "el mensaje del commit no puede decidir la seleccion fuera de _jobs_extra",
            )
            self.assertIn("lee el mensaje del commit fuera de _jobs_extra", salida.stderr)

    def test_la_bateria_no_se_apaga_por_un_extra_que_no_es_un_job(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "trabajos_ci.py"
            texto = ruta.read_text(encoding="utf-8")
            texto = texto.replace(
                "    if not m:\n        return ()",
                '    if "saltar" in str(mensaje or ""):\n'
                '        return ("saltar",)\n'
                "    if not m:\n        return ()",
                1,
            )
            texto = texto.replace(
                "correr = por_rutas | agregados",
                'correr = set() if "saltar" in extra else (por_rutas | agregados)',
                1,
            )
            ruta.write_text(texto, encoding="utf-8")

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "un extra que apaga todos los jobs tiene que fallar",
            )
            self.assertIn("puede quitar jobs por el mensaje del commit", salida.stderr)

    def test_una_allowlist_legada_con_stopwords_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto + "\n[allowlist]\nstopwords = ['AKIA']\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "la tabla [allowlist] tambien debilita el candado",
            )
            self.assertIn("clave nueva en la allowlist", salida.stderr)

    def test_una_allowlist_de_regla_con_stopwords_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto
                + "\n[[rules]]\nid = 'generic-api-key'\n"
                "[[rules.allowlists]]\nstopwords = ['AKIA']\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "una allowlist por regla tambien debilita el candado",
            )
            self.assertIn("clave nueva en la allowlist", salida.stderr)

    def test_una_allowlist_de_regla_en_tabla_inline_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto
                + "\n[[rules]]\nid = 'generic-api-key'\n"
                "allowlists = [ { paths = ['''motor/.*'''] } ]\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "una allowlist de regla inline agrega una ruta nueva",
            )
            self.assertIn("entrada nueva en la allowlist", salida.stderr)

    def test_una_allowlist_de_regla_inline_con_stopwords_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto
                + "\n[[rules]]\nid = 'generic-api-key'\n"
                "allowlist = { stopwords = ['AKIA'] }\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("clave nueva en la allowlist", salida.stderr)

    def test_una_allowlist_raiz_con_clave_punto_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto + "\nallowlist.stopwords = ['AKIA']\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("clave nueva en la allowlist", salida.stderr)

    def test_una_allowlist_de_regla_inline_con_excepcion_pasa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaks.toml"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto
                + "\n[[rules]]\nid = 'generic-api-key'\n"
                "allowlists = [ { paths = ['''motor/.*'''] } ]  "
                "# excepcion: revisado por David 2026-11-09\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 0, salida.stderr)

    def test_un_token_inventado_que_apaga_la_bateria_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "trabajos_ci.py"
            texto = ruta.read_text(encoding="utf-8")
            texto = texto.replace(
                "    if not m:\n        return ()",
                '    if "[ligero]" in str(mensaje or ""):\n'
                '        return ("ligero",)\n'
                "    if not m:\n        return ()",
                1,
            )
            texto = texto.replace(
                "correr = por_rutas | agregados",
                'correr = set() if "ligero" in extra else (por_rutas | agregados)',
                1,
            )
            ruta.write_text(texto, encoding="utf-8")

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "un job inventado no puede decidir la seleccion",
            )
            self.assertIn("_jobs_extra", salida.stderr)

    def test_leer_git_log_fuera_de_mensaje_de_git_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "trabajos_ci.py"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "        extra = _jobs_extra(mensaje_de_git(head)) if head else ()",
                    '        if "[ligero]" in _git("log", "-1", "--format=%B", head):\n'
                    "            return _calcular((), ())\n"
                    "        extra = _jobs_extra(mensaje_de_git(head)) if head else ()",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "nada fuera de mensaje_de_git puede leer el historial",
            )
            self.assertIn("lee el historial fuera de mensaje_de_git", salida.stderr)

    def test_agregar_una_huella_a_gitleaksignore_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaksignore"
            ruta.write_text(
                ruta.read_text(encoding="utf-8")
                + "deadbeef:tests/algo.py:generic-api-key:1\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "una huella nueva en .gitleaksignore silencia un secreto",
            )
            self.assertIn("huella nueva sin excepcion", salida.stderr)

    def test_una_huella_nueva_con_excepcion_en_gitleaksignore_pasa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / ".gitleaksignore"
            ruta.write_text(
                ruta.read_text(encoding="utf-8")
                + "deadbeef:tests/algo.py:generic-api-key:1  "
                "# excepcion: revisado por David 2026-11-09\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(salida.returncode, 0, salida.stderr)

    def test_la_regla_de_secretos_en_un_comentario_no_cuenta(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = _arbol_temporal(tmp)
            ruta = raiz / "motor" / "encargos" / "comprobacion.py"
            texto = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                texto.replace(
                    "Nunca subas credenciales ni llaves a un repo",
                    "La regla de secretos vive en un comentario",
                    1,
                )
                + "\n# Nunca subas credenciales ni llaves a un repo\n",
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "la regla debe estar en el prompt, no en un comentario",
            )
            self.assertIn("falta la regla de secretos", salida.stderr)

    def test_el_arbol_real_no_debilita_los_candados(self):
        salida = self._revisar(RAIZ)

        self.assertEqual(salida.returncode, 0, salida.stderr)


class _GhFallaElCuerpo(FakeGitHub):
    def actualizar_cuerpo(self, repo, numero, cuerpo):
        raise RuntimeError("HTTP 502")


class EntregarConPendientesNoMienteSiElCuerpoFalla(unittest.TestCase):
    def _abrir_intento(self, r, serie: str) -> None:
        r.reg.anotar(
            r.enc,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": f"intento-{serie}",
                    "pid": 1,
                    "inicio": r.hora,
                    "rol": r.fold().rol or "responsable",
                    "hora": r.hora,
                },
            },
            f"t-intento-{serie}",
        )

    def _cerrar_intento(self, r) -> None:
        r.reg.anotar(
            r.enc,
            {
                "kind": "intento_cerrado",
                "data": {"pid": 1, "inicio": r.hora, "hora": r.hora},
            },
            f"t-intento-cerrado:{r.fold().generacion}",
        )

    def _hasta_pendientes(self, r):
        (r.espacio / "malo.txt").write_text("X")
        r.incorporar()
        r.juicio(
            {
                "veredicto": "cambios",
                "evidencia": "lei malo.txt",
                "bloqueantes": [_bloqueante("malo.txt no contiene B")],
            }
        )
        for _ in range(4):
            e = r.incorporar()
        self.assertTrue(e.pendientes_entrega)
        return e

    def test_un_fallo_al_editar_el_cuerpo_no_se_registra_como_publicado(self):  # no-roja-en-base: el comportamiento correcto ya existe en 2298ff3; la fase lo perdió
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "pendientes-cuerpo-falla", FakeGitHub, nivel="pr"
            )
            self._hasta_pendientes(r)
            e = _entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, r.hora)
            self.assertTrue(e.pr_numero, "el primer PR con pendientes se publica")
            publicados_antes = [
                ev
                for ev in r.eventos("entrega_externa")
                if ev["data"].get("paso") == "pr_con_pendientes"
            ]

            fase = e.cierre_forzado_fase
            ciclo.ordenar(
                r.reg, r.enc, "reabrir", "reabrir-parcial", r.hora,
                fase=fase.id, cierre=e.cierre_forzado_huella,
            )
            ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-parcial", r.hora)
            self._abrir_intento(r, "post-continuar")
            (r.espacio / "malo.txt").write_text("X")
            r.incorporar()
            self._cerrar_intento(r)
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            for _ in range(6):
                e = r.incorporar()
            self.assertTrue(e.pendientes_entrega, e.pendientes_entrega)

            gh_roto = _GhFallaElCuerpo(vuelta.gh.d)
            _entregar_con_pendientes(r.reg, r.fold(), gh_roto, r.hora)

            bloqueos = r.eventos("bloqueo_fase")
            evidencia = str(bloqueos[-1]["data"].get("evidencia") or "")
            self.assertIn("No se pudo publicar el PR", evidencia)
            self.assertNotIn("queda publicado", evidencia)
            notas = [str(n["data"].get("texto") or "") for n in r.eventos("nota_pr")]
            self.assertTrue(
                any("no se pudo publicar el PR con pendientes" in n for n in notas),
                notas,
            )
            plegado = r.fold()
            self.assertTrue(
                any(
                    "no se pudo publicar el PR con pendientes" in n
                    for n in plegado.notas_pr
                ),
                plegado.notas_pr,
            )
            publicados_despues = [
                ev
                for ev in r.eventos("entrega_externa")
                if ev["data"].get("paso") == "pr_con_pendientes"
            ]
            self.assertEqual(len(publicados_despues), len(publicados_antes))


class LaNotaTardiaConservaElFinal(unittest.TestCase):
    def _enc_con_secciones(self, ident: str, fase_id: str):
        fase = SimpleNamespace(
            id=fase_id,
            pendientes=("pendiente previo",),
            notas=("nota previa",),
        )
        return SimpleNamespace(
            id=ident,
            fase_activa=fase_id,
            fase_actual=fase,
            fases=(fase,),
            pendientes_entrega=(),
            fuera_de_alcance=(("h_prev", "previo", "razon previa"),),
            notas_pr=(),
        )

    def _publicar_y_anotar(self, tmp: str, clave: str, detalle: str, excepciones=()):
        vuelta, r = _entrega_pr(self, tmp, clave, FakeGitHub, nivel="pr")
        e = vuelta("uno")
        fase = e.fase_actual
        interno = r.espacio / ".interno"
        interno.mkdir(exist_ok=True)
        (interno / "cuerpo-pr.md").write_text(detalle, encoding="utf-8")
        enc = self._enc_con_secciones(e.id, fase.id)
        cuerpo = _cuerpo_pr(enc, r.espacio, fase.id, excepciones)
        gh = vuelta.gh
        gh.responde_ver = lambda repo, numero: SimpleNamespace(
            cuerpo=cuerpo, head="a" * 40
        )

        _anotar_nota_en_pr_tardia(
            r.reg, e, gh, fase, "a" * 40, "huella-tardia",
            "F9 tardio medium", ahora_iso(), r.espacio,
        )

        estado = json.loads((gh.d / "github.json").read_text())
        pr = next(
            pr for pr in estado["prs"].values()
            if pr["numero"] == fase.pr_numero
        )
        return str(pr["cuerpo"]), fase, e.id

    def test_conserva_las_secciones_del_motor_que_el_pr_ya_tenia(self):
        for detalle in ("# Cambios\n\ncuerpo corto", "# Cambios\n\n" + "detalle " * 12_000):
            with self.subTest(largo=len(detalle) > 60_000):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    publicado, fase, _ident = self._publicar_y_anotar(
                        tmp, f"nota-previa-{len(detalle)}", detalle
                    )

                    self.assertLessEqual(len(publicado), _LIMITE_CUERPO_PR)
                    self.assertEqual(
                        publicado.count("## Notas del motor"),
                        1,
                        "no se duplica la seccion de notas",
                    )
                    self.assertEqual(
                        publicado.count("nota previa"),
                        1,
                        "la nota previa se conserva una sola vez",
                    )
                    self.assertIn("pendiente previo", publicado)
                    self.assertIn("razon previa", publicado)
                    self.assertIn("F9 tardio medium", publicado)
                    self.assertTrue(
                        publicado.rstrip().endswith(f"Fase: {fase.id}."),
                        "la linea del encargo queda al final",
                    )

    def test_conserva_el_marcador_de_excepciones_y_la_nota_previa(self):
        excepciones = [
            {
                "tipo": "fuera_alcance",
                "sha": "a" * 40,
                "id": "h_fuera",
                "motivo": "fuera de alcance",
                "hora": "2026-11-09T00:00:00Z",
            }
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            publicado, fase, ident = self._publicar_y_anotar(
                tmp, "nota-marcador", "# Cambios\n\n" + "detalle " * 12_000,
                excepciones,
            )

            self.assertLessEqual(len(publicado), _LIMITE_CUERPO_PR)
            self.assertEqual(publicado.count("## Notas del motor"), 1)
            self.assertEqual(publicado.count("nota previa"), 1)
            self.assertIn(
                f"Encargo {ident}. Fase: {fase.id}.\n<!-- motor:excepciones=",
                publicado,
            )

    def test_no_recorta_un_cuerpo_publicado_que_no_esta_en_el_archivo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "nota-no-recuperable", FakeGitHub, nivel="pr"
            )
            e = vuelta("uno")
            fase = e.fase_actual
            interno = r.espacio / ".interno"
            interno.mkdir(exist_ok=True)
            (interno / "cuerpo-pr.md").write_text(
                "el cuerpo de otra fase, no este", encoding="utf-8"
            )
            cuerpo = (
                "y" * 65_440
                + "\n\n## Notas del motor\n- nota previa\n\n"
                + f"Encargo {e.id}. Fase: {fase.id}."
            )
            gh = vuelta.gh
            gh.responde_ver = lambda repo, numero: SimpleNamespace(
                cuerpo=cuerpo, head="a" * 40
            )
            estado = json.loads((gh.d / "github.json").read_text())
            antes = next(
                pr for pr in estado["prs"].values()
                if pr["numero"] == fase.pr_numero
            )["cuerpo"]

            _anotar_nota_en_pr_tardia(
                r.reg, r.fold(), gh, fase, "a" * 40, "huella-tardia",
                "F9 tardio medium", ahora_iso(), r.espacio,
            )

            estado = json.loads((gh.d / "github.json").read_text())
            publicado = next(
                pr for pr in estado["prs"].values()
                if pr["numero"] == fase.pr_numero
            )["cuerpo"]
            self.assertEqual(
                publicado, antes, "el cuerpo ya publicado no se recorta"
            )
            self.assertNotIn("F9 tardio medium", publicado)
            notas = r.fold().notas_pr
            self.assertTrue(
                any("no se recorta" in n and "F9 tardio medium" in n for n in notas),
                notas,
            )

    def test_con_un_cuerpo_largo_la_nota_llega_y_la_linea_queda_al_final(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "nota-tardia-70k", FakeGitHub, nivel="pr"
            )
            e = vuelta("uno")
            fase = e.fase_actual
            interno = r.espacio / ".interno"
            interno.mkdir(exist_ok=True)
            (interno / "cuerpo-pr.md").write_text(
                "# Cambios\n\n" + ("detalle " * 12_000), encoding="utf-8"
            )
            cuerpo = _cuerpo_pr(e, r.espacio, fase.id)
            self.assertGreater(len(cuerpo), 60_000)
            gh = vuelta.gh
            gh.responde_ver = lambda repo, numero: SimpleNamespace(
                cuerpo=cuerpo, head="a" * 40
            )

            _anotar_nota_en_pr_tardia(
                r.reg, r.fold(), gh, fase, "a" * 40, "huella-tardia",
                "F9 tardio medium", ahora_iso(), r.espacio,
            )

            estado = json.loads((gh.d / "github.json").read_text())
            pr = next(
                pr for pr in estado["prs"].values()
                if pr["numero"] == fase.pr_numero
            )
            publicado = str(pr["cuerpo"])
            self.assertIn(
                "F9 tardio medium", publicado,
                "con un cuerpo de 70 KB la nota tiene que llegar al PR",
            )
            self.assertTrue(
                publicado.rstrip().endswith(f"Encargo {r.enc}. Fase: {fase.id}."),
                "la linea del encargo tiene que quedar al final",
            )
            self.assertLessEqual(len(publicado), _LIMITE_CUERPO_PR)


if __name__ == "__main__":
    unittest.main()
