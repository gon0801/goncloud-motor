"""Fase 8: la puerta unica del cuerpo del PR y los candados de secretos."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos.ciclo import _LIMITE_CUERPO_PR, _cuerpo_pr

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = (
    ".gitleaks.toml",
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
                    "correr = por_rutas - agregados",
                    1,
                ),
                encoding="utf-8",
            )

            salida = self._revisar(raiz)

            self.assertEqual(
                salida.returncode,
                1,
                "el mensaje del commit no puede quitar la bateria",
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

    def test_el_arbol_real_no_debilita_los_candados(self):
        salida = self._revisar(RAIZ)

        self.assertEqual(salida.returncode, 0, salida.stderr)


if __name__ == "__main__":
    unittest.main()
