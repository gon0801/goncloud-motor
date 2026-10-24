"""Una conf mal escrita no tumba al motor: se rechaza con su mensaje y el motor sigue con la buena."""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor import __main__ as cli
from motor.conf import ConfError, load_conf
from motor.pstack import parse_conf

SALIDA_POR_TOPE = 3  # el daemon arrancó y corrió hasta el --tope
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@l"]


def git(*a: str, cwd: Path) -> str:
    r = subprocess.run([*GIT, *a], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def escribe_conf(d: Path, general: str = "", rutas_extra: str = "", pstack: str = ""):
    (d / "reglas").mkdir(parents=True, exist_ok=True)
    (d / "papeles.toml").write_text('[papeles]\nimplementar = "uno"\nrevisar = "dos"\n')
    (d / "modelos.toml").write_text('[carriles]\nuno = ["a"]\ndos = ["a"]\n')
    (d / "rutas.toml").write_text(
        '[rutas.a]\ncuenta = "x"\ncli = "a"\nargv = ["a", "{prompt}"]\n' + rutas_extra
    )
    (d / "general.toml").write_text(general)
    if pstack:
        (d / "pstack.toml").write_text(pstack)


class ConfValidaTest(unittest.TestCase):
    def valida(self, **kw) -> list[str]:
        with tempfile.TemporaryDirectory() as t:
            escribe_conf(Path(t), **kw)
            return cli.conf_valida(Path(t))

    def assertRechaza(self, frag: str, **kw):
        errores = self.valida(**kw)
        self.assertTrue(errores, "una conf mala no pasa")
        self.assertTrue(any(frag in e for e in errores), errores)
        self.assertFalse(any("Traceback" in e for e in errores), errores)

    def test_conf_buena_pasa(self):
        self.assertEqual(self.valida(general="[tiempos]\ntimeout_s = 60\n"), [])

    def test_tiempos_mal_escritos_salen_con_su_clave(self):
        for texto in ('x = "abc"', "x = inf", "x = nan", "x = true"):
            with self.subTest(texto):
                self.assertRechaza(
                    "[tiempos] x tiene que ser un número",
                    general=f"[tiempos]\n{texto}\n",
                )

    def test_tabla_dentro_de_tiempos(self):
        self.assertRechaza(
            "[tiempos] x tiene que ser un número", general="[tiempos.x]\ny = 1\n"
        )

    def test_duracion_mayor_que_cero(self):
        self.assertRechaza(
            "[tiempos] timeout_s tiene que ser un número mayor que 0",
            general="[tiempos]\ntimeout_s = 0\n",
        )

    def test_cuentas_enteras(self):
        self.assertRechaza(
            "[tiempos] repeticiones_max tiene que ser un número entero mayor que 0",
            general="[tiempos]\nrepeticiones_max = 0\n",
        )
        self.assertRechaza(
            "[tiempos] agentes_por_maquina tiene que ser un número entero",
            general="[tiempos]\nagentes_por_maquina = 1.5\n",
        )

    def test_cero_agentes_por_maquina_es_sin_tope(self):
        self.assertEqual(
            self.valida(general="[tiempos]\nagentes_por_maquina = 0\n"), []
        )

    def test_max_parallel_no_entero(self):
        extra = '[rutas.b]\ncuenta = "y"\ncli = "b"\nmax_parallel = "x"\nargv = ["b", "{prompt}"]\n'
        self.assertRechaza("[rutas.b] max_parallel", rutas_extra="\n" + extra)

    def test_celda_con_clave_desconocida_nombra_clave_y_celda(self):
        self.assertRechaza(
            "[exigencia.gate.implementar]",
            pstack='[pstack]\ncommit = "a"\n[exigencia.gate.implementar]\ncargar = ["x"]\n',
        )
        self.assertRechaza(
            "'cargar'",
            pstack='[pstack]\ncommit = "a"\n[exigencia.gate.implementar]\ncargar = ["x"]\n',
        )

    def test_pstack_clave_desconocida(self):
        with self.assertRaises(ConfError) as c:
            parse_conf({"pstack": {"exigier": True}})
        self.assertIn("exigier", str(c.exception))

    def test_celda_con_las_claves_validas_pasa(self):
        parse_conf(
            {
                "pstack": {"commit": "a", "exigir": False},
                "exigencia": {
                    "gate": {
                        "implementar": {
                            "skill": "poteto-mode",
                            "playbook": False,
                            "todos": True,
                            "min_subagentes": 0,
                            "puerta": "x",
                        }
                    }
                },
            }
        )

    def test_cualquier_excepcion_es_un_error_con_mensaje(self):
        with tempfile.TemporaryDirectory() as t:
            escribe_conf(Path(t))
            with mock.patch.object(
                cli.m.Policy, "from_conf", side_effect=RuntimeError("boom")
            ):
                errores = cli.conf_valida(Path(t))
        self.assertTrue(any("RuntimeError: boom" in e for e in errores), errores)

    def test_programadas_roto_no_truena(self):
        with tempfile.TemporaryDirectory() as t:
            escribe_conf(Path(t))
            (Path(t) / "programadas.toml").write_text("[mac]\narchivos = 3\n")
            with mock.patch.object(
                cli.efimeras, "valida_programadas", side_effect=TypeError("raro")
            ):
                errores = cli.conf_valida(Path(t))
        self.assertTrue(any("TypeError: raro" in e for e in errores), errores)

    def test_ruta_con_dato_que_el_motor_no_llena(self):
        extra = (
            '\n[rutas.b]\ncuenta = "y"\ncli = "b"\nargv = ["b", "{prompt}", "{otra}"]\n'
        )
        errores = self.valida(rutas_extra=extra)
        self.assertTrue(any("ruta b" in e and "{otra}" in e for e in errores), errores)

    def test_los_cinco_datos_pasan(self):
        extra = (
            '\n[rutas.b]\ncuenta = "y"\ncli = "b"\n'
            'argv = ["b", "{prompt}", "{brief}", "{result}", "{pstack}", "{sesion}"]\n'
        )
        self.assertEqual(self.valida(rutas_extra=extra), [])

    def test_comando_conf_valida_imprime_el_motivo_y_sale_2(self):
        with tempfile.TemporaryDirectory() as t:
            escribe_conf(Path(t), general='[tiempos]\nx = "abc"\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                codigo = cli.main(["conf", "valida", t])
        self.assertEqual(codigo, 2)
        self.assertIn("[tiempos] x", out.getvalue())
        self.assertNotIn("Traceback", out.getvalue())

    def test_load_conf_levanta_conferror(self):
        with tempfile.TemporaryDirectory() as t:
            escribe_conf(Path(t), general="[tiempos]\nx = inf\n")
            with self.assertRaises(ConfError):
                load_conf(Path(t))


if __name__ == "__main__":
    unittest.main()
