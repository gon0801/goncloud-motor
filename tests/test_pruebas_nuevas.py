"""El job de pruebas nuevas en rojo distingue una prueba viva de una muerta."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "pruebas_nuevas.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, mensaje: str) -> None:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@l",
        "commit",
        "-q",
        "-m",
        mensaje,
    )


def _repo(
    tmp: Path,
    base_extra: dict[str, str] | None = None,
    conftest_extra: str = "",
) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests/__init__.py").write_text("")
    (repo / "tools").mkdir()
    (repo / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
        + conftest_extra
    )
    (repo / "producto.py").write_text("def valor():\n    return 1\n")
    (repo / "tests/test_existente.py").write_text(
        "def test_viejo():\n    assert True\n"
    )
    for ruta, texto in (base_extra or {}).items():
        destino = repo / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto)
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "cambio")
    (repo / "producto.py").write_text("def valor():\n    return 2\n")
    shutil.copy2(HERRAMIENTA, repo / "tools" / "pruebas_nuevas.py")
    return repo


def _corre(repo: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "pruebas_nuevas.py"), "--base", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


class PruebasNuevasContraLaBase(unittest.TestCase):
    def test_marca_la_prueba_que_pasa_sin_el_arreglo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_nueva.py").write_text(
                "from producto import valor\n\n"
                "def test_usa_el_arreglo():\n"
                "    assert valor() == 2\n\n"
                "def test_que_pasa_en_la_base():\n"
                "    assert 1 + 1 == 2\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("test_que_pasa_en_la_base", salida.stderr)
            self.assertNotIn("test_usa_el_arreglo", salida.stderr)

    def test_acepta_una_prueba_que_falla_en_la_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_nueva.py").write_text(
                "from producto import valor\n\n"
                "def test_usa_el_arreglo():\n"
                "    assert valor() == 2\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 0, salida.stdout + salida.stderr)
            self.assertIn("ninguna pasa en la base", salida.stdout)

    def test_lista_las_excepciones_declaradas(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_nueva.py").write_text(
                "def test_que_pasa_en_la_base():  # no-roja-en-base: comprueba el contrato viejo\n"
                "    assert True\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 0, salida.stdout + salida.stderr)
            self.assertIn("excepción", salida.stdout)
            self.assertIn("test_que_pasa_en_la_base", salida.stdout)
            self.assertIn("comprueba el contrato viejo", salida.stdout)

    def test_la_excepcion_de_una_clase_no_tapa_la_otra(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_dos_clases.py").write_text(
                "class TestA:\n"
                "    def test_igual(self):  # no-roja-en-base: contrato previo\n"
                "        assert True\n\n"
                "class TestB:\n"
                "    def test_igual(self):\n"
                "        assert True\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("TestB::test_igual", salida.stderr)
            self.assertNotIn("prueba(s) nueva(s) revisada(s); ninguna pasa", salida.stdout)

    def test_no_culpa_a_una_prueba_vieja_con_el_mismo_prefijo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(
                Path(tmp),
                {"tests/test_arreglo.py": "def test_arreglo_extra():\n    assert True\n"},
            )
            (repo / "tests/test_arreglo.py").write_text(
                "def test_arreglo_extra():\n    assert True\n\n"
                "def test_arreglo():\n    assert False\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 0, salida.stdout + salida.stderr)
            self.assertIn("ninguna pasa en la base", salida.stdout)

    def test_el_caso_nuevo_de_parametrize_queda_en_el_candado(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(
                Path(tmp),
                {
                    "tests/test_param.py": (
                        "import pytest\n\n"
                        "@pytest.mark.parametrize('x', [1])\n"
                        "def test_param(x):\n"
                        "    assert x > 0\n"
                    )
                },
            )
            (repo / "tests/test_param.py").write_text(
                "import pytest\n\n"
                "@pytest.mark.parametrize('x', [1, 2])\n"
                "def test_param(x):\n"
                "    assert x > 0\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("test_param", salida.stderr)

    def test_el_parametrize_de_la_clase_tambien_cuenta(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(
                Path(tmp),
                {
                    "tests/test_param_clase.py": (
                        "import pytest\n\n"
                        "@pytest.mark.parametrize('x', [1])\n"
                        "class TestCasos:\n"
                        "    def test_param(self, x):\n"
                        "        assert x > 0\n"
                    )
                },
            )
            (repo / "tests/test_param_clase.py").write_text(
                "import pytest\n\n"
                "@pytest.mark.parametrize('x', [1, 2])\n"
                "class TestCasos:\n"
                "    def test_param(self, x):\n"
                "        assert x > 0\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("TestCasos::test_param", salida.stderr)

    def test_un_error_de_coleccion_no_tapa_otra_prueba_que_pasa(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_nueva_api.py").write_text(
                "from producto import funcion_nueva\n\n"
                "def test_nueva_api():\n"
                "    assert funcion_nueva() == 2\n"
            )
            (repo / "tests/test_pasa.py").write_text(
                "def test_pasa():\n    assert True\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("tests/test_pasa.py::test_pasa", salida.stderr)
            self.assertIn("collection failure", salida.stderr)

    def test_si_pytest_no_arranca_el_job_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_nueva.py").write_text(
                "def test_pasa_sin_arreglo():\n    assert True\n"
            )
            _commit(repo, "pruebas")
            venv = Path(tmp) / "sin-pytest"
            subprocess.run(
                [sys.executable, "-m", "venv", "--without-pip", str(venv)],
                check=True,
                capture_output=True,
            )
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            salida = subprocess.run(
                [
                    str(python),
                    str(repo / "tools/pruebas_nuevas.py"),
                    "--base",
                    "main",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
            self.assertEqual(
                salida.returncode, 2, salida.stdout + salida.stderr
            )
            self.assertIn("no se pudo correr la base", salida.stderr)

    def test_un_filtro_que_deselecciona_la_prueba_no_aprueba(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_nueva.py").write_text(
                "def test_pasa_sin_arreglo():\n    assert True\n"
            )
            _commit(repo, "pruebas")
            env = {
                **os.environ,
                "PYTEST_ADDOPTS": "-k no_existe_ninguna_prueba_con_este_nombre",
            }
            salida = _corre(repo, env=env)
            self.assertEqual(
                salida.returncode, 2, salida.stdout + salida.stderr
            )
            self.assertIn("no se pudo correr la base", salida.stderr)

    def test_un_pytest_que_sale_3_con_reporte_valido_no_aprueba(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_nueva.py").write_text(
                "def test_pasa_sin_arreglo():\n    assert True\n"
            )
            _commit(repo, "pruebas")
            falso = Path(tmp) / "falsopytest"
            falso.mkdir()
            (falso / "pytest.py").write_text(
                "import pathlib, sys\n"
                "destino = next(\n"
                "    a.split('=', 1)[1] for a in sys.argv[1:]\n"
                "    if a.startswith('--junitxml=')\n"
                ")\n"
                "pathlib.Path(destino).write_text(\n"
                "    '<?xml version=\"1.0\" encoding=\"utf-8\"?>'\n"
                "    '<testsuites><testsuite name=\"pytest\" tests=\"1\" errors=\"0\"'\n"
                "    ' failures=\"0\" skipped=\"0\">'\n"
                "    '<testcase classname=\"tests.test_nueva\"'\n"
                "    ' name=\"test_pasa_sin_arreglo\" time=\"0\"/>'\n"
                "    '</testsuite></testsuites>',\n"
                "    encoding='utf-8',\n"
                ")\n"
                "raise SystemExit(3)\n"
            )
            env = {**os.environ, "PYTHONPATH": str(falso)}
            salida = _corre(repo, env=env)
            self.assertEqual(
                salida.returncode, 2, salida.stdout + salida.stderr
            )
            self.assertIn("pytest terminó con 3", salida.stderr)
            self.assertIn("no hay un resultado completo", salida.stderr)

    def test_un_archivo_que_no_colecciona_y_ninguna_pasa_no_aborta(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_rota.py").write_text(
                "from producto import no_existe\n\n"
                "def test_rota():\n"
                "    assert False\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(
                salida.returncode, 0, salida.stdout + salida.stderr
            )
            self.assertIn("tests/test_rota.py: collection failure", salida.stderr)
            self.assertIn("ninguna pasa en la base", salida.stdout)

    def test_un_error_interno_de_pytest_no_aprueba(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(
                Path(tmp),
                conftest_extra=(
                    "def pytest_collection_modifyitems(session, config, items):\n"
                    "    raise RuntimeError('fallo de plugin al preparar las pruebas')\n"
                ),
            )
            (repo / "tests/test_nueva.py").write_text(
                "def test_pasa_sin_arreglo():\n    assert True\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(
                salida.returncode, 2, salida.stdout + salida.stderr
            )
            self.assertIn("no se pudo correr la base", salida.stderr)

    def test_los_apoyos_nuevos_viajan_al_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/auxiliar.py").write_text("VALOR = 1\n")
            (repo / "tests/test_nueva.py").write_text(
                "from tests.auxiliar import VALOR\n\n"
                "def test_que_pasa():\n"
                "    assert VALOR == 1\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("tests/test_nueva.py::test_que_pasa", salida.stderr)


if __name__ == "__main__":
    unittest.main()
