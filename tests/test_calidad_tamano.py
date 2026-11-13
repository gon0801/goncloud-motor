"""`calidad.py tamano` frena que un archivo o una función de `motor/` crezca.

Cada prueba arma un repo de git temporal con su propio `motor/`, guarda una base
en un commit y corre la herramienta como la corre el CI, contra esa base.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERRAMIENTA = Path(__file__).resolve().parents[1] / "tools" / "calidad.py"


def _funcion(nombre: str, lineas: int, sangria: str = "") -> str:
    cuerpo = f"{sangria}    x = 1\n" * (lineas - 1)
    return f"{sangria}def {nombre}():\n{cuerpo}"


def _relleno(lineas: int) -> str:
    return "x = 1\n" * lineas


class Repo:
    def __init__(self, raiz: Path):
        self.raiz = raiz
        self.git("init", "-q", "-b", "main")
        (raiz / "tools").mkdir()
        shutil.copy(HERRAMIENTA, raiz / "tools" / "calidad.py")

    def git(self, *args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c", "user.name=prueba",
                "-c", "user.email=prueba@example.invalid",
                "-c", "commit.gpgsign=false",
                *args,
            ],
            cwd=self.raiz,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def escribir(self, ruta: str, texto: str) -> None:
        destino = self.raiz / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        return self.git("rev-parse", "HEAD")

    def tamano(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.raiz / "tools" / "calidad.py"), "tamano", *extra],
            cwd=self.raiz,
            capture_output=True,
            text=True,
            check=False,
        )


def _repo(prueba: unittest.TestCase) -> Repo:
    directorio = tempfile.TemporaryDirectory()
    prueba.addCleanup(directorio.cleanup)
    return Repo(Path(directorio.name))


class LimiteDeArchivo(unittest.TestCase):
    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/grande.py", _relleno(1200))
        self.base = self.repo.commit()

    def test_sin_cambios_pasa_y_dice_contra_que_comparo_y_cuantos_pasan(self):
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(self.base, corrida.stdout)
        self.assertIn("1 archivo(s)", corrida.stdout)
        self.assertIn("0 función(es)", corrida.stdout)

    def test_un_archivo_que_ya_pasaba_y_crece_una_linea_falla(self):
        self.repo.escribir("motor/grande.py", _relleno(1201))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/grande.py: archivo de 1201 líneas", corrida.stderr)
        self.assertIn("mide 1200", corrida.stderr)
        self.assertIn("Saca código", corrida.stderr)

    def test_un_archivo_nuevo_de_1001_lineas_falla(self):
        self.repo.escribir("motor/encargos/nuevo.py", _relleno(1001))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/encargos/nuevo.py: archivo de 1001 líneas", corrida.stderr)
        self.assertIn("no existe", corrida.stderr)

    def test_un_archivo_nuevo_de_1000_lineas_cabe(self):
        self.repo.escribir("motor/nuevo.py", _relleno(1000))
        self.assertEqual(self.repo.tamano("--base", self.base).returncode, 0)

    def test_un_archivo_que_cabia_en_la_base_no_puede_pasar_del_limite(self):
        self.repo.escribir("motor/chico.py", _relleno(900))
        base = self.repo.commit()
        self.repo.escribir("motor/chico.py", _relleno(1001))
        corrida = self.repo.tamano("--base", base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/chico.py: archivo de 1001 líneas", corrida.stderr)
        self.assertIn("mide 900", corrida.stderr)

    def test_renombrar_el_archivo_no_hereda_su_tamano_en_la_base(self):
        (self.repo.raiz / "motor" / "grande.py").rename(
            self.repo.raiz / "motor" / "otro.py"
        )
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/otro.py: archivo de 1200 líneas", corrida.stderr)

    def test_lo_que_se_encoge_no_deja_holgura_para_volver_a_crecer(self):
        self.repo.escribir("motor/grande.py", _relleno(1100))
        self.assertEqual(self.repo.tamano("--base", self.base).returncode, 0)
        nueva_base = self.repo.commit()

        self.repo.escribir("motor/grande.py", _relleno(1200))
        corrida = self.repo.tamano("--base", nueva_base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/grande.py: archivo de 1200 líneas", corrida.stderr)
        self.assertIn("mide 1100", corrida.stderr)

    def test_por_omision_compara_contra_origin_main(self):
        self.repo.git("update-ref", "refs/remotes/origin/main", self.base)
        self.repo.escribir("motor/grande.py", _relleno(1201))
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("en origin/main mide 1200", corrida.stderr)

    def test_sin_la_referencia_de_la_base_dice_como_traerla(self):
        corrida = self.repo.tamano("--base", "origin/main")
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("git fetch origin main", corrida.stderr)

    def test_no_hay_linea_base_que_actualizar(self):
        corrida = self.repo.tamano("--actualizar")
        self.assertEqual(corrida.returncode, 2)
        self.assertIn("--base", corrida.stderr)
        self.assertFalse((self.repo.raiz / ".github").exists())


class LimiteDeFuncion(unittest.TestCase):
    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir(
            "motor/m.py", _funcion("chica", 10) + _funcion("larga", 150)
        )
        self.base = self.repo.commit()

    def test_la_funcion_que_ya_pasaba_y_no_crece_pasa(self):
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn("1 función(es)", corrida.stdout)

    def test_la_funcion_que_ya_pasaba_y_crece_falla(self):
        self.repo.escribir("motor/m.py", _funcion("chica", 10) + _funcion("larga", 151))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::larga: función de 151 líneas", corrida.stderr)
        self.assertIn("mide 150", corrida.stderr)

    def test_un_metodo_nuevo_de_101_lineas_falla(self):
        self.repo.escribir(
            "motor/m.py",
            _funcion("chica", 10)
            + _funcion("larga", 150)
            + "class Caja:\n"
            + _funcion("metodo", 101, sangria="    "),
        )
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::Caja.metodo: función de 101 líneas", corrida.stderr)

    def test_mover_la_funcion_dentro_del_archivo_conserva_su_tamano_en_la_base(self):
        self.repo.escribir("motor/m.py", _funcion("larga", 150) + _funcion("chica", 10))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn("nada crece", corrida.stdout)

    def test_renombrar_la_funcion_no_hereda_su_tamano_en_la_base(self):
        self.repo.escribir("motor/m.py", _funcion("chica", 10) + _funcion("otra", 150))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::otra: función de 150 líneas", corrida.stderr)

    def test_una_segunda_definicion_con_el_mismo_nombre_no_se_cuela(self):
        self.repo.escribir(
            "motor/m.py",
            _funcion("chica", 10) + _funcion("larga", 150) + _funcion("larga", 20),
        )
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::larga: función de 170 líneas", corrida.stderr)


if __name__ == "__main__":
    unittest.main()
