"""`calidad.py tamano` frena que un archivo o una función de `motor/` crezca.

Cada prueba corre la herramienta como la corre el CI, sobre una copia temporal
con su propio `motor/` y su propia línea base.
"""

from __future__ import annotations

import json
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
        (raiz / "tools").mkdir()
        shutil.copy(HERRAMIENTA, raiz / "tools" / "calidad.py")
        (raiz / "motor").mkdir()
        self.base = raiz / ".github" / "calidad" / "baseline.json"
        self.base.parent.mkdir(parents=True)
        self.base.write_text("{}\n", encoding="utf-8")

    def escribir(self, ruta: str, texto: str) -> None:
        destino = self.raiz / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")

    def guardar(self, entradas: dict[tuple[str, ...], int]) -> None:
        tamano = sorted([list(clave), str(n)] for clave, n in entradas.items())
        self.base.write_text(
            json.dumps({"tamano": tamano}, ensure_ascii=False), encoding="utf-8"
        )

    def guardado(self) -> dict[tuple[str, ...], int]:
        datos = json.loads(self.base.read_text(encoding="utf-8"))
        return {tuple(clave): int(n) for clave, n in datos.get("tamano", [])}

    def tamano(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.raiz / "tools" / "calidad.py"), "tamano", *extra],
            cwd=self.raiz,
            capture_output=True,
            text=True,
            check=False,
        )


class LimiteDeTamano(unittest.TestCase):
    def setUp(self):
        directorio = tempfile.TemporaryDirectory()
        self.addCleanup(directorio.cleanup)
        self.repo = Repo(Path(directorio.name))
        self.repo.escribir("motor/grande.py", _relleno(1200))
        self.repo.guardar({("archivo", "motor/grande.py"): 1200})

    def test_la_linea_base_vigente_pasa_y_dice_cuantas_entradas_tiene(self):
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn("1 archivos", corrida.stdout)
        self.assertIn("0 funciones", corrida.stdout)

    def test_un_archivo_guardado_que_crece_una_linea_falla(self):
        self.repo.escribir("motor/grande.py", _relleno(1201))
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/grande.py", corrida.stderr)
        self.assertIn("1201", corrida.stderr)
        self.assertIn("1200", corrida.stderr)

    def test_un_archivo_nuevo_de_1001_lineas_falla(self):
        self.repo.escribir("motor/encargos/nuevo.py", _relleno(1001))
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/encargos/nuevo.py", corrida.stderr)
        self.assertIn("1001", corrida.stderr)

    def test_un_archivo_nuevo_de_1000_lineas_cabe(self):
        self.repo.escribir("motor/nuevo.py", _relleno(1000))
        self.assertEqual(self.repo.tamano().returncode, 0)

    def test_renombrar_el_archivo_no_hereda_su_tamano_guardado(self):
        (self.repo.raiz / "motor" / "grande.py").rename(
            self.repo.raiz / "motor" / "otro.py"
        )
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/otro.py", corrida.stderr)

    def test_actualizar_baja_la_linea_base_y_volver_a_crecer_falla(self):
        self.repo.escribir("motor/grande.py", _relleno(1100))
        self.assertEqual(self.repo.tamano("--actualizar").returncode, 0)
        self.assertEqual(self.repo.guardado(), {("archivo", "motor/grande.py"): 1100})

        self.repo.escribir("motor/grande.py", _relleno(1200))
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("1100", corrida.stderr)

    def test_actualizar_quita_la_entrada_que_ya_cabe(self):
        self.repo.escribir("motor/grande.py", _relleno(900))
        self.assertEqual(self.repo.tamano("--actualizar").returncode, 0)
        self.assertEqual(self.repo.guardado(), {})
        self.repo.escribir("motor/grande.py", _relleno(1001))
        self.assertEqual(self.repo.tamano().returncode, 1)

    def test_actualizar_se_niega_a_subir_una_entrada(self):
        self.repo.escribir("motor/grande.py", _relleno(1201))
        corrida = self.repo.tamano("--actualizar")
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/grande.py", corrida.stderr)
        self.assertEqual(self.repo.guardado(), {("archivo", "motor/grande.py"): 1200})

    def test_actualizar_se_niega_a_agregar_una_entrada(self):
        self.repo.escribir("motor/nuevo.py", _relleno(1001))
        self.assertEqual(self.repo.tamano("--actualizar").returncode, 1)
        self.assertEqual(self.repo.guardado(), {("archivo", "motor/grande.py"): 1200})


class LimiteDeFuncion(unittest.TestCase):
    def setUp(self):
        directorio = tempfile.TemporaryDirectory()
        self.addCleanup(directorio.cleanup)
        self.repo = Repo(Path(directorio.name))
        self.repo.escribir(
            "motor/m.py", _funcion("chica", 10) + _funcion("larga", 150)
        )
        self.repo.guardar({("funcion", "motor/m.py", "larga"): 150})

    def test_la_funcion_guardada_que_no_crece_pasa(self):
        self.assertEqual(self.repo.tamano().returncode, 0)

    def test_la_funcion_guardada_que_crece_falla(self):
        self.repo.escribir("motor/m.py", _funcion("chica", 10) + _funcion("larga", 151))
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::larga", corrida.stderr)
        self.assertIn("151", corrida.stderr)
        self.assertIn("150", corrida.stderr)

    def test_un_metodo_nuevo_de_101_lineas_falla(self):
        self.repo.escribir(
            "motor/m.py",
            _funcion("chica", 10)
            + _funcion("larga", 150)
            + "class Caja:\n"
            + _funcion("metodo", 101, sangria="    "),
        )
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::Caja.metodo", corrida.stderr)

    def test_mover_la_funcion_dentro_del_archivo_conserva_su_entrada(self):
        self.repo.escribir("motor/m.py", _funcion("larga", 150) + _funcion("chica", 10))
        self.assertEqual(self.repo.tamano().returncode, 0)

    def test_renombrar_la_funcion_no_hereda_su_tamano_guardado(self):
        self.repo.escribir("motor/m.py", _funcion("chica", 10) + _funcion("otra", 150))
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::otra", corrida.stderr)

    def test_una_segunda_definicion_con_el_mismo_nombre_no_se_cuela(self):
        self.repo.escribir(
            "motor/m.py",
            _funcion("chica", 10) + _funcion("larga", 150) + _funcion("larga", 20),
        )
        self.assertEqual(self.repo.tamano().returncode, 1)

    def test_actualizar_se_niega_a_subir_una_funcion(self):
        self.repo.escribir("motor/m.py", _funcion("chica", 10) + _funcion("larga", 151))
        self.assertEqual(self.repo.tamano("--actualizar").returncode, 1)
        self.assertEqual(self.repo.guardado(), {("funcion", "motor/m.py", "larga"): 150})


if __name__ == "__main__":
    unittest.main()
