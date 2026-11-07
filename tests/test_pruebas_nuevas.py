"""El job de pruebas nuevas en rojo distingue una prueba viva de una muerta."""

from __future__ import annotations

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


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tools").mkdir()
    (repo / "conftest.py").write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n")
    (repo / "producto.py").write_text("def valor():\n    return 1\n")
    (repo / "tests/test_existente.py").write_text(
        "def test_viejo():\n    assert True\n"
    )
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "cambio")
    (repo / "producto.py").write_text("def valor():\n    return 2\n")
    shutil.copy2(HERRAMIENTA, repo / "tools" / "pruebas_nuevas.py")
    return repo


def _corre(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "pruebas_nuevas.py"), "--base", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=180,
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
            self.assertIn("excepción test_que_pasa_en_la_base", salida.stdout)
            self.assertIn("comprueba el contrato viejo", salida.stdout)

    def test_399_f2_reconstruido_queda_marcado(self):
        # Caso real: la prueba pasa aunque se revierta el arreglo.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "tests/test_399_f2.py").write_text(
                "from producto import valor\n\n"
                "def test_399_f2_pasa_aunque_se_revierta():\n"
                "    assert valor() in (1, 2)\n"
            )
            _commit(repo, "pruebas")
            salida = _corre(repo)
            self.assertEqual(salida.returncode, 1, salida.stdout + salida.stderr)
            self.assertIn("test_399_f2_pasa_aunque_se_revierta", salida.stderr)


if __name__ == "__main__":
    unittest.main()
