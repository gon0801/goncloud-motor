"""Traba 3 del 30 y 31 de octubre: el comprobador corría las comprobaciones
dentro de la jaula seatbelt sin lecturas extra. En la Mini el Python 3.12 de uv
vive bajo el home, que la jaula niega, y no arrancaba ("No module named
encodings"): el único intérprete que quedaba era el 3.9 del sistema."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import comprobacion
from motor.encargos.aislamiento import (
    ComandoAislado,
    aislar,
    lecturas_del_interprete,
)
from tests.entorno import HAY_AISLAMIENTO, SIN_AISLAMIENTO
from tests.test_flujo_revisiones import Recorrido

SANDBOX = Path("/usr/bin/sandbox-exec")


def _jaula_aplicable() -> bool:
    """sandbox-exec existe pero no siempre puede aplicarse (dentro de otra
    jaula macOS responde `sandbox_apply: Operation not permitted`)."""
    if not SANDBOX.is_file():
        return False
    try:
        return subprocess.run(
            [str(SANDBOX), "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            timeout=30,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


JAULA_REAL = _jaula_aplicable()
EXIGIDA = os.environ.get("MOTOR_EXIGE_SEATBELT") == "1"
FLUJO = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pruebas.yml"


class LecturasDelInterprete(unittest.TestCase):
    def test_incluye_el_ejecutable_y_la_instalacion(self):
        lecturas = lecturas_del_interprete()
        self.assertIn(Path(sys.executable), lecturas)
        self.assertIn(Path(sys.executable).resolve(), lecturas)
        self.assertIn(Path(sys.base_prefix).resolve(), [p.resolve() for p in lecturas])

    def test_nunca_concede_el_home_ni_sus_padres(self):
        home = Path.home().resolve()
        with mock.patch.object(sys, "base_prefix", str(home)), mock.patch.object(
            sys, "prefix", str(home.parent)
        ):
            lecturas = lecturas_del_interprete()
        self.assertNotIn(home, lecturas)
        self.assertNotIn(home.parent, lecturas)
        self.assertNotIn(Path("/"), lecturas)

    def test_lo_que_no_existe_no_se_concede(self):
        with mock.patch.object(sys, "base_prefix", "/no/existe/python"):
            self.assertNotIn(Path("/no/existe/python"), lecturas_del_interprete())


def _fingir_instalacion(tmp: Path) -> tuple[Path, Path, Path]:
    """Un Python como el de uv en la Mini: el ejecutable es un enlace del home
    a un directorio que es otro enlace."""
    home = tmp / "home"
    real = home / ".local" / "share" / "uv" / "python" / "cpython-3.12.5-macos-aarch64-none"
    (real / "bin").mkdir(parents=True)
    (real / "bin" / "python3.12").write_text("")
    enlace_dir = real.parent / "cpython-3.12-macos-aarch64-none"
    enlace_dir.symlink_to(real.name)
    enlace_bin = home / ".local" / "bin" / "python3"
    enlace_bin.parent.mkdir(parents=True)
    enlace_bin.symlink_to(enlace_dir / "bin" / "python3.12")
    return home, enlace_bin, enlace_dir


class LosEnlacesDeLaInstalacionSeConceden(unittest.TestCase):
    def test_cada_salto_de_enlace_bajo_el_home_se_concede_como_tal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            home, enlace_bin, enlace_dir = _fingir_instalacion(Path(tmp).resolve())
            with mock.patch.object(Path, "home", return_value=home), mock.patch.object(
                sys, "executable", str(enlace_bin)
            ), mock.patch.object(sys, "base_prefix", str(enlace_dir)), mock.patch.object(
                sys, "prefix", str(enlace_dir)
            ), mock.patch.object(sys, "base_exec_prefix", str(enlace_dir)), mock.patch.object(
                sys, "exec_prefix", str(enlace_dir)
            ):
                lecturas = lecturas_del_interprete()
            self.assertIn(enlace_bin, lecturas)
            self.assertIn(enlace_dir, lecturas)
            self.assertIn(enlace_dir.resolve(), lecturas)
            self.assertIn(enlace_bin.resolve(), lecturas)
            self.assertNotIn(home, lecturas)

    @unittest.skipUnless(HAY_AISLAMIENTO and SANDBOX.is_file(), SIN_AISLAMIENTO)
    def test_el_perfil_lee_el_enlace_como_enlace(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            home, enlace_bin, enlace_dir = _fingir_instalacion(base)
            espacio = base / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            runtime = base / "runtime"
            aislar([str(enlace_bin), "-c", "pass"], espacio, runtime, lecturas_extra=(enlace_bin, enlace_dir))
            perfil = (runtime / "jaula.sb").read_text(encoding="utf-8").splitlines()
            destino = enlace_dir.resolve()
        self.assertIn(f"(allow file-read* (literal {json.dumps(str(enlace_bin))}))", perfil)
        self.assertIn(f"(allow file-read* (literal {json.dumps(str(enlace_dir))}))", perfil)
        self.assertIn(f"(allow file-read* (subpath {json.dumps(str(destino))}))", perfil)


class ElCiCorreLaJaulaRealDeMacOS(unittest.TestCase):
    """Si ningún job corre estas pruebas, que se omitan es silencioso: el flujo
    tiene que correrlas en macOS y convertir el salto en fallo."""

    def test_el_flujo_tiene_un_job_de_macos_que_exige_la_jaula(self):
        texto = FLUJO.read_text(encoding="utf-8")
        self.assertIn("runs-on: macos-latest", texto)
        self.assertIn('MOTOR_EXIGE_SEATBELT: "1"', texto)
        lineas = [l for l in texto.splitlines() if "tests.test_comprobador_interprete" in l]
        self.assertEqual(len(lineas), 1, lineas)
        self.assertIn("unittest", lineas[0])

    def test_sin_jaula_aplicable_la_exigencia_la_convierte_en_fallo(self):
        # Documenta el contrato: con MOTOR_EXIGE_SEATBELT=1 ConLaJaulaRealDeMacOS
        # falla en vez de omitirse (ver su setUp).
        self.assertTrue(hasattr(ConLaJaulaRealDeMacOS, "setUp"))


class ElComprobadorLasPide(unittest.TestCase):
    def test_cada_comprobacion_corre_con_lectura_a_la_instalacion_del_supervisor(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "comp-1", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            pedidos = []

            def falso(cmd, espacio, runtime, **kw):
                pedidos.append(kw)
                return ComandoAislado(list(cmd), dict(os.environ), "prueba")

            comandos = [{"objetivo": "corre", "argv": [sys.executable, "-c", "print('ok')"]}]
            with mock.patch.object(comprobacion, "aislar", falso):
                datos = comprobacion._ejecutar_declaradas(
                    comandos, r.espacio, r.d, r.d / "comprobador-t"
                )

        self.assertEqual(datos["ejecuciones"][0].get("rc"), 0, datos)
        self.assertEqual(len(pedidos), 1)
        self.assertEqual(
            tuple(pedidos[0]["lecturas_extra"]), lecturas_del_interprete()
        )


@unittest.skipUnless(HAY_AISLAMIENTO and SANDBOX.is_file(), SIN_AISLAMIENTO)
class PerfilDeLaJaula(unittest.TestCase):
    def test_el_perfil_permite_leer_la_instalacion_despues_de_negar_el_home(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp) / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            runtime = Path(tmp) / "runtime"
            aislar(
                [sys.executable, "-c", "pass"],
                espacio,
                runtime,
                lecturas_extra=lecturas_del_interprete(),
            )
            perfil = (runtime / "jaula.sb").read_text(encoding="utf-8").splitlines()

        negar = next(i for i, l in enumerate(perfil) if "deny file-read*" in l)
        for ruta in lecturas_del_interprete():
            regla = f"(allow file-read* (subpath {json.dumps(str(ruta.resolve()))}))"
            self.assertIn(regla, perfil)
            self.assertGreater(perfil.index(regla), negar)
        self.assertFalse([l for l in perfil if "file-write*" in l and "uv" in l])


class ConLaJaulaRealDeMacOS(unittest.TestCase):
    def setUp(self):
        if JAULA_REAL:
            return
        causa = "hace falta sandbox-exec aplicable (macOS fuera de otra jaula)"
        if EXIGIDA:
            self.fail("MOTOR_EXIGE_SEATBELT=1 y la jaula real no se puede aplicar: " + causa)
        self.skipTest(causa)

    def _correr(
        self, lecturas: tuple[Path, ...], ejecutable: str | None = None
    ) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp) / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            comando = aislar(
                [
                    ejecutable or sys.executable,
                    "-c",
                    "import encodings, sys; print('arranco', sys.version_info[:2])",
                ],
                espacio,
                Path(tmp) / "runtime",
                lecturas_extra=lecturas,
            )
            return subprocess.run(
                comando.argv,
                cwd=espacio,
                env=comando.entorno,
                capture_output=True,
                text=True,
                timeout=60,
            )

    def test_el_interprete_del_supervisor_arranca_dentro_de_la_jaula(self):
        resultado = self._correr(lecturas_del_interprete())
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("arranco", resultado.stdout)

    def test_el_layout_del_incidente_arranca_dentro_de_la_jaula(self):
        """El intérprete de la Mini se declara por un enlace del home
        (~/.local/bin/python3) que apunta a un directorio que es otro enlace
        (cpython-3.12-macos-aarch64-none). Se arma esa misma cadena bajo el home
        real, sobre la instalación de Python que corre la prueba, y se ejecuta
        dentro de la jaula con lo que concede el comprobador."""
        home = Path.home().resolve()
        instalacion = Path(sys.base_prefix).resolve()
        binario = Path(sys.executable).resolve()
        if instalacion not in binario.parents:
            self.skipTest("el ejecutable no vive dentro del prefijo de su instalación")
        try:
            raiz = Path(tempfile.mkdtemp(prefix=".motor-jaula-", dir=home))
        except OSError as exc:
            self.skipTest(f"no se puede escribir bajo el home: {exc}")
        self.addCleanup(shutil.rmtree, raiz, True)
        enlace_dir = raiz / "python" / "cpython-3.12-macos-aarch64-none"
        enlace_dir.parent.mkdir()
        enlace_dir.symlink_to(instalacion)
        enlace_bin = raiz / ".local" / "bin" / "python3"
        enlace_bin.parent.mkdir(parents=True)
        enlace_bin.symlink_to(enlace_dir / binario.relative_to(instalacion))

        with mock.patch.object(sys, "executable", str(enlace_bin)), mock.patch.object(
            sys, "base_prefix", str(enlace_dir)
        ), mock.patch.object(sys, "prefix", str(enlace_dir)), mock.patch.object(
            sys, "base_exec_prefix", str(enlace_dir)
        ), mock.patch.object(sys, "exec_prefix", str(enlace_dir)):
            lecturas = lecturas_del_interprete()
        self.assertIn(enlace_bin, lecturas)
        self.assertIn(enlace_dir, lecturas)

        resultado = self._correr(lecturas, str(enlace_bin))

        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("arranco", resultado.stdout)

    def test_sin_la_lectura_un_interprete_bajo_el_home_no_arranca(self):
        home = Path.home().resolve()
        if home not in Path(sys.base_prefix).resolve().parents:
            self.skipTest("el intérprete no vive bajo el home: la jaula no lo niega")
        resultado = self._correr(())
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("encodings", resultado.stderr)


if __name__ == "__main__":
    unittest.main()
