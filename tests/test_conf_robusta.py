"""Una conf mal escrita no tumba al motor: se rechaza con su mensaje y el motor sigue con la buena."""

from __future__ import annotations

import contextlib
import gc
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor import __main__ as cli
from motor.conf import ConfError, load_conf
from motor.daemon import CONF_BUENA, Daemon
from motor.pstack import parse_conf
from motor.store import Store

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

    def test_max_parallel_cero(self):
        extra = '[rutas.b]\ncuenta = "y"\ncli = "b"\nmax_parallel = 0\nargv = ["b", "{prompt}"]\n'
        self.assertRechaza("[rutas.b] max_parallel", rutas_extra="\n" + extra)

    def test_argv_con_algo_que_no_es_texto(self):
        extra = '[rutas.b]\ncuenta = "y"\ncli = "b"\nargv = ["b", "{prompt}", 5]\n'
        self.assertRechaza("[rutas.b] argv", rutas_extra="\n" + extra)

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


class Banco(unittest.TestCase):
    """Un origen, la copia de la conf de la máquina y un home con su maquina.toml."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="motor-conf-robusta-"))
        self.origen = self.t / "origen.git"
        git("init", "-q", "--bare", "-b", "main", str(self.origen), cwd=self.t)
        self.conf = self.t / "conf"
        git("clone", "-q", str(self.origen), str(self.conf), cwd=self.t)
        escribe_conf(self.conf, general="[tiempos]\nconf_cada_s = 1\n")
        self.sube("buena")
        self.home = self.t / "home"
        self.home.mkdir()
        self.maquina()

    def maquina(self, texto: str | None = None):
        (self.home / "maquina.toml").write_text(
            texto or f'nombre = "Mac (prueba)"\nconf = "{self.conf}"\n'
        )

    def sube(self, msg: str) -> str:
        git("add", "-A", cwd=self.conf)
        git("commit", "-q", "-m", msg, cwd=self.conf)
        git("push", "-q", "origin", "HEAD:main", cwd=self.conf)
        return git("rev-parse", "HEAD", cwd=self.conf)

    def deja_conf_buena(self):
        """Arranca un Daemon (que guarda conf-buena) y suelta su candado, como un motor que terminó."""
        d = Daemon(self.home)
        del d
        gc.collect()

    def eventos(self, kind: str) -> list:
        return [e for e in Store(self.home / "motor.db").events() if e.kind == kind]

    def avisos(self) -> list[Path]:
        return [
            p
            for p in (self.home / "avisos" / "pendientes").glob("*.json")
            if "confmala" in json.loads(p.read_text(encoding="utf-8")).get("key", "")
        ]


class TraeConfTest(Banco):
    def test_commit_con_conf_mala_no_llega_al_disco_ni_da_effectfailed(self):
        d = Daemon(self.home)
        buena = git("rev-parse", "HEAD", cwd=self.conf)
        validadas: list[tuple[Path, str]] = []
        real = cli.conf_valida

        def espia(carpeta):
            validadas.append((Path(carpeta), git("rev-parse", "HEAD", cwd=self.conf)))
            return real(carpeta)

        otro = self.t / "otro"
        git("clone", "-q", str(self.origen), str(otro), cwd=self.t)
        (otro / "general.toml").write_text('[tiempos]\ntimeout_s = "abc"\n')
        git("commit", "-qam", "mala", cwd=otro)
        git("push", "-q", "origin", "HEAD:main", cwd=otro)
        ahora = time.time()
        with mock.patch.object(cli, "conf_valida", espia):
            for i in range(4):
                d.tick(ahora + i * 5)
        self.assertTrue(validadas, "la conf nueva se validó")
        for carpeta, head in validadas:
            self.assertNotEqual(carpeta.resolve(), self.conf.resolve())
            self.assertEqual(head, buena, "se validó antes de mover la conf")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.conf), buena)
        self.assertEqual(git("status", "--porcelain", cwd=self.conf), "")
        self.assertEqual(len(self.eventos("ConfRechazada")), 1)
        self.assertEqual(len(self.avisos()), 1, "se avisa una vez")
        self.assertEqual(self.eventos("EffectFailed"), [])

    def test_el_daemon_guarda_el_sha_de_la_conf_buena(self):
        self.deja_conf_buena()
        self.assertEqual(
            (self.home / CONF_BUENA).read_text().strip(),
            git("rev-parse", "HEAD", cwd=self.conf),
        )


class ArranqueConMalaConfTest(Banco):
    def arranca(self) -> tuple[int, str]:
        err = io.StringIO()
        with (
            mock.patch.object(cli, "_espera") as dormir,
            contextlib.redirect_stderr(err),
        ):
            codigo = cli.main(
                [
                    "daemon",
                    "--home",
                    str(self.home),
                    "--hasta-terminar",
                    "--intervalo",
                    "0.1",
                    "--tope",
                    "5",
                ]
            )
        self.dormir = dormir
        return codigo, err.getvalue()

    def rompe_conf(self) -> str:
        (self.conf / "general.toml").write_text('[tiempos]\ntimeout_s = "abc"\n')
        return self.sube("mala")

    def test_vuelve_a_la_buena_avisa_una_vez_y_arranca(self):
        self.deja_conf_buena()
        buena = (self.home / CONF_BUENA).read_text().strip()
        mala = self.rompe_conf()
        git("remote", "set-url", "origin", str(self.t / "no-existe.git"), cwd=self.conf)
        codigo, err = self.arranca()
        self.assertEqual(codigo, SALIDA_POR_TOPE, err)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.conf), buena)
        self.assertEqual([e.data["sha"] for e in self.eventos("ConfRechazada")], [mala])
        self.assertEqual(len(self.avisos()), 1)
        self.assertNotIn("Traceback", err)
        self.dormir.assert_not_called()
        codigo, _ = self.arranca()
        self.assertEqual(codigo, SALIDA_POR_TOPE)
        self.assertEqual(len(self.eventos("ConfRechazada")), 1, "una sola vez")

    def test_maquina_con_env_que_no_es_texto_espera_y_sale(self):
        (self.home / "maquina.toml").write_text(
            f'nombre = "x"\nconf = "{self.conf}"\n\n[env]\nA = 1\n'
        )
        codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.dormir.assert_called_once_with(cli.ESPERA_CONF_S)
        self.assertIn("[env]", err)
        self.assertNotIn("Traceback", err)

    def test_fallo_al_armar_el_daemon_espera_y_sale(self):
        with mock.patch("motor.daemon.Daemon", side_effect=TypeError("boom")):
            codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.dormir.assert_called_once_with(cli.ESPERA_CONF_S)
        self.assertIn("boom", err)
        self.assertNotIn("Traceback", err)

    def test_sin_conf_buena_espera_y_sale_sin_traceback(self):
        self.rompe_conf()
        codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.dormir.assert_called_once_with(cli.ESPERA_CONF_S)
        self.assertIn("conf-buena", err)
        self.assertNotIn("Traceback", err)

    def test_con_cambios_sin_commitear_no_la_toca(self):
        self.deja_conf_buena()
        (self.conf / "general.toml").write_text('[tiempos]\ntimeout_s = "abc"\n')
        codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.assertIn("timeout_s", (self.conf / "general.toml").read_text())
        self.assertNotIn("Traceback", err)

    def test_archivos_sin_trackear_no_impiden_volver(self):
        self.deja_conf_buena()
        buena = (self.home / CONF_BUENA).read_text().strip()
        self.rompe_conf()
        (self.conf / "notas.txt").write_text("mías")
        codigo, err = self.arranca()
        self.assertEqual(codigo, SALIDA_POR_TOPE, err)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.conf), buena)
        self.assertTrue((self.conf / "notas.txt").exists())

    def test_carpeta_de_conf_que_no_existe_espera_y_sale_sin_traceback(self):
        self.deja_conf_buena()
        self.maquina(f'nombre = "x"\nconf = "{self.t / "no-existe"}"\n')
        codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.dormir.assert_called_once_with(cli.ESPERA_CONF_S)
        self.assertNotIn("Traceback", err)

    def test_conf_buena_que_tampoco_carga_espera_y_sale(self):
        self.deja_conf_buena()
        (self.home / CONF_BUENA).write_text(self.rompe_conf())
        codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.dormir.assert_called_once()
        self.assertIn("tampoco carga", err)

    def test_maquina_toml_roto_espera_y_sale_sin_traceback(self):
        self.deja_conf_buena()
        self.maquina('nombre = "x"\nrutas = 1\nconf = "' + str(self.conf) + '"\n')
        codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.dormir.assert_called_once()
        self.assertIn("maquina.toml", err)
        self.assertNotIn("Traceback", err)

    def test_secretos_con_permisos_abiertos_espera_y_sale(self):
        self.deja_conf_buena()
        s = self.home / "secretos.env"
        s.write_text("A=1\n")
        os.chmod(s, 0o644)
        codigo, err = self.arranca()
        self.assertEqual(codigo, 1)
        self.assertIn("secretos.env", err)
        self.dormir.assert_called_once()


if __name__ == "__main__":
    unittest.main()
