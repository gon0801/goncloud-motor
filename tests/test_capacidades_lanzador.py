"""La versión de capacidades pertenece al lanzador que se reporta."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor import capacidades_maquina as cm
from motor.encargos import agentes


class VersionDelLanzador(unittest.TestCase):
    def test_windows_conserva_el_lanzador_y_sus_argumentos(self):
        casos = (
            (
                [r"C:\flota\node.exe", r"C:\flota\cli.js"],
                [r"C:\flota\node.exe", r"C:\flota\cli.js", "--version"],
            ),
            ([r"C:\flota\codex.exe"], [r"C:\flota\codex.exe", "--version"]),
            (
                [r"C:\flota con espacios\codex.cmd", "--opcion"],
                [
                    "cmd-prueba.exe",
                    "/d",
                    "/s",
                    "/c",
                    r"C:\flota con espacios\codex.cmd",
                    "--opcion",
                    "--version",
                ],
            ),
            (
                [r"C:\flota\codex.BAT"],
                [
                    "cmd-prueba.exe",
                    "/d",
                    "/s",
                    "/c",
                    r"C:\flota\codex.BAT",
                    "--version",
                ],
            ),
        )
        for lanzador, esperado in casos:
            with self.subTest(lanzador=lanzador):

                def correr(argv, timeout):
                    version = b"elegido 2.0\n" if argv == esperado else b"otro 1.0\n"
                    return subprocess.CompletedProcess(argv, 0, version, b"")

                with (
                    mock.patch.object(cm, "ADAPTADORES", {"codex": None}),
                    mock.patch.object(cm, "resolver_lanzador", return_value=lanzador),
                    mock.patch.object(
                        cm,
                        "os",
                        SimpleNamespace(
                            name="nt",
                            environ={"PATH": r"C:\otro", "ComSpec": "cmd-prueba.exe"},
                        ),
                    ),
                    mock.patch.object(cm, "_correr", side_effect=correr) as ejecutar,
                ):
                    filas = cm._agentes(lambda nombre: r"C:\otro\codex.cmd")
                self.assertEqual(
                    filas,
                    [
                        {
                            "agente": "codex",
                            "estado": "ok",
                            "detalle": "codex elegido 2.0 en " + " ".join(lanzador),
                        }
                    ],
                )
                ejecutar.assert_called_once_with(esperado, 20)


class HomeDeAntigravity(unittest.TestCase):
    def test_resolve_con_runtimeerror_no_impide_detectar_el_llavero_del_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            resolver = type(home).resolve
            for falla in ("base", "entorno"):
                with self.subTest(falla=falla):
                    llamadas = 0

                    def resolve(ruta, *args, **kwargs):
                        nonlocal llamadas
                        llamadas += 1
                        if llamadas == (1 if falla == "base" else 2):
                            raise RuntimeError("enlace circular")
                        return resolver(ruta, *args, **kwargs)

                    with (
                        mock.patch.dict(
                            os.environ, {"HOME": str(home), "USERPROFILE": str(home)}
                        ),
                        mock.patch.object(type(home), "resolve", resolve),
                        mock.patch.object(
                            agentes, "_llavero_antigravity", return_value=True
                        ),
                    ):
                        self.assertTrue(agentes.sesion_antigravity_presente(home))

    def test_home_no_resoluble_ajeno_no_consulta_el_llavero(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "prueba"
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(Path(tmp) / "usuario"),
                        "USERPROFILE": str(Path(tmp) / "usuario"),
                    },
                ),
                mock.patch.object(
                    type(home), "resolve", side_effect=RuntimeError("enlace circular")
                ),
                mock.patch.object(agentes, "_llavero_antigravity") as llavero,
            ):
                self.assertFalse(agentes.sesion_antigravity_presente(home))
            llavero.assert_not_called()

    def test_capacidades_detecta_la_sesion_del_usuario_con_home_de_motor_vacio(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "usuario"
            motor_home = Path(tmp) / "motor"
            motor_home.mkdir()
            token = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
            token.parent.mkdir(parents=True)
            token.write_bytes(b"marca-de-prueba")
            with (
                mock.patch.object(agentes.Path, "home", return_value=home),
                mock.patch.object(agentes, "_llavero_antigravity", return_value=False),
                mock.patch.object(
                    cm,
                    "_correr",
                    return_value=subprocess.CompletedProcess([], 0, b"1.4.12\n", b""),
                ),
            ):
                filas = cm._agentes(
                    lambda nombre: "/falso/agy" if nombre == "agy" else None,
                    home=motor_home,
                )
            fila = next(f for f in filas if f["agente"] == "antigravity")
            self.assertEqual(
                fila,
                {
                    "agente": "antigravity",
                    "estado": "ok",
                    "detalle": "agy 1.4.12 en /falso/agy (con sesión)",
                },
            )
