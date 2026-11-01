"""Traba del 1 de noviembre (enc_58c92a2e04e1a8a7): la sesión de agente se lanzaba
con `aislar(...)` sin las lecturas del intérprete. En la Mini el Python 3.12 de
uv vive bajo el home, que la jaula niega ("No module named encodings"), y el
`python3` que quedaba a mano era el 3.9 del sistema, sin `tomllib`. El
comprobador ya lo resolvía (#142); la sesión del responsable y la del revisor no."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import sesion
from motor.encargos.aislamiento import (
    ComandoAislado,
    lecturas_del_interprete,
    path_con_interprete,
)
from tests.entorno import HAY_AISLAMIENTO, SIN_AISLAMIENTO
from tests.test_comprobador_interprete import EXIGIDA, FLUJO, JAULA_REAL, SANDBOX

ROLES = ("responsable", "revisor")
PRUEBA = "import sys, tomllib, encodings; print(sys.version)"


def _encargo(rol: str) -> SimpleNamespace:
    return SimpleNamespace(id="enc_x", rol=rol, conductor="token", adjuntos=())


def _preparar(tmp: Path, rol: str, cmd: list[str]) -> dict:
    espacio = tmp / "base" / "espacio" / "enc_x"
    espacio.mkdir(parents=True)
    directorio = tmp / "base" / "ejecucion" / "enc_x" / "intento-1"
    directorio.mkdir(parents=True)
    sesion.preparar_administrado(
        cmd, espacio, directorio, _encargo(rol), "s1", "2026-11-01T00:00:00Z", tmp / "runtime"
    )
    return json.loads((directorio / "arranque.json").read_text(encoding="utf-8"))


class ElPythonQueVeLaSesion(unittest.TestCase):
    def test_python3_ejecuta_el_interprete_del_supervisor(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            carpeta = Path(tmp) / "runtime"
            path = path_con_interprete("/usr/bin:/bin", carpeta)
            wrapper = carpeta / "bin" / "python3"
            self.assertEqual(path.split(os.pathsep)[0], str(carpeta / "bin"))
            self.assertTrue(os.access(wrapper, os.X_OK))
            resultado = subprocess.run(
                [str(wrapper), "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.strip(), sys.executable)

    def test_los_shims_del_motor_siguen_al_frente(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            shims = Path(tmp) / "shims"
            shims.mkdir()
            (shims / "llamada_guard.py").write_text("")
            runtime = Path(tmp) / "runtime"
            path = path_con_interprete(os.pathsep.join([str(shims), "/usr/bin"]), runtime)
        self.assertEqual(
            path.split(os.pathsep), [str(shims), str(runtime / "bin"), "/usr/bin"]
        )

    def test_repetir_deja_el_mismo_python3(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runtime = Path(tmp) / "runtime"
            primero = path_con_interprete("/usr/bin", runtime)
            wrapper = runtime / "bin" / "python3"
            contenido = wrapper.read_text(encoding="utf-8")
            segundo = path_con_interprete("/usr/bin", runtime)
            self.assertEqual(primero, segundo)
            self.assertEqual(wrapper.read_text(encoding="utf-8"), contenido)
            self.assertEqual(os.listdir(runtime / "bin"), ["python3"])


class LaSesionPideElInterprete(unittest.TestCase):
    def test_responsable_y_revisor_lanzan_con_el_interprete(self):
        for rol in ROLES:
            with self.subTest(rol=rol), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                pedidos = []

                def falso(cmd, espacio, runtime, **kw):
                    pedidos.append(kw)
                    return ComandoAislado(list(cmd), {}, "prueba")

                with mock.patch("motor.encargos.aislamiento.aislar", falso):
                    _preparar(Path(tmp).resolve(), rol, ["python3", "-c", "pass"])
                self.assertEqual(len(pedidos), 1)
                self.assertIs(pedidos[0]["interprete"], True)
                self.assertIs(pedidos[0]["lectura"], rol == "revisor")


@unittest.skipUnless(HAY_AISLAMIENTO and SANDBOX.is_file(), SIN_AISLAMIENTO)
class PerfilDeLaSesion(unittest.TestCase):
    def test_solo_se_concede_la_lectura_de_la_instalacion(self):
        home = Path.home().resolve()
        amplios = {str(home), *(str(p) for p in home.parents)}
        for rol in ROLES:
            with self.subTest(rol=rol), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                base = Path(tmp).resolve()
                spec = _preparar(base, rol, ["python3", "-c", "pass"])
                perfil = (base / "runtime" / "jaula.sb").read_text(encoding="utf-8").splitlines()
                espacio = str(base / "base" / "espacio" / "enc_x")
                runtime = str(base / "runtime")
                self.assertEqual(spec["aislamiento"], "seatbelt")

            def rutas(prefijo: str) -> list[str]:
                return [
                    json.loads(l.split("subpath ", 1)[1].rstrip(")"))
                    for l in perfil
                    if l.startswith(prefijo) and "subpath" in l
                ]

            negar = next(i for i, l in enumerate(perfil) if "deny file-read*" in l)
            for ruta in lecturas_del_interprete():
                regla = f"(allow file-read* (subpath {json.dumps(str(ruta.resolve()))}))"
                self.assertIn(regla, perfil)
                self.assertGreater(perfil.index(regla), negar)
            self.assertFalse(amplios & set(rutas("(allow file-read*")))
            self.assertEqual(
                sorted(rutas("(allow file-write*")),
                sorted([runtime] if rol == "revisor" else [espacio, runtime]),
            )


class ConLaJaulaRealDeMacOS(unittest.TestCase):
    def setUp(self):
        if JAULA_REAL:
            return
        causa = "hace falta sandbox-exec aplicable (macOS fuera de otra jaula)"
        if EXIGIDA:
            self.fail("MOTOR_EXIGE_SEATBELT=1 y la jaula real no se puede aplicar: " + causa)
        self.skipTest(causa)

    def _correr(self, rol: str, cmd: list[str]) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            spec = _preparar(Path(tmp).resolve(), rol, cmd)
            return subprocess.run(
                spec["cmd"],
                cwd=spec["cwd"],
                env=spec["env"],
                capture_output=True,
                text=True,
                timeout=60,
            )

    def test_python3_de_la_sesion_es_el_del_supervisor_y_arranca_en_la_jaula(self):
        for rol in ROLES:
            with self.subTest(rol=rol):
                resultado = self._correr(rol, ["python3", "-c", PRUEBA])
                self.assertEqual(resultado.returncode, 0, resultado.stderr)
                self.assertEqual(resultado.stdout, sys.version + "\n")

    def test_la_jaula_sigue_negando_el_home(self):
        try:
            descriptor, nombre = tempfile.mkstemp(prefix=".motor-sesion-", dir=Path.home().resolve())
        except OSError as exc:
            self.skipTest(f"no se puede escribir bajo el home: {exc}")
        os.close(descriptor)
        self.addCleanup(os.unlink, nombre)
        for rol in ROLES:
            with self.subTest(rol=rol):
                resultado = self._correr(rol, ["python3", "-c", f"open({nombre!r}).read(1)"])
                self.assertNotEqual(resultado.returncode, 0)
                self.assertIn("PermissionError", resultado.stderr)


class ElCiCorreLaSesionEnLaJaulaDeMacOS(unittest.TestCase):
    def test_el_job_de_macos_corre_esta_prueba(self):
        texto = FLUJO.read_text(encoding="utf-8")
        lineas = [l for l in texto.splitlines() if "tests.test_sesion_interprete" in l]
        self.assertEqual(len(lineas), 1, lineas)
        self.assertIn("unittest", lineas[0])
        macos = texto.split("jaula-macos:", 1)[1].split("\n  bateria:", 1)[0]
        self.assertIn(lineas[0], macos)


if __name__ == "__main__":
    unittest.main()
