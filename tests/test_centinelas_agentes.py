"""La batería normal no alcanza los CLIs de agente reales.

`tests.guarda_home.instalar()` deja un centinela por cada cliente instalado.
Si alguien quita esa guarda, estos casos fallan: el PATH vuelve a resolver el
binario real y la suite podría abrir el navegador para un OAuth.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests import guarda_home

SIN_POSIX = "los centinelas son ejecutables POSIX"


class CentinelasDeAgentes(unittest.TestCase):
    def setUp(self):
        """Guarda los permisos vivos presentes para exigir que no se filtren."""
        self.permisos = {
            clave: valor
            for clave, valor in os.environ.items()
            if clave.startswith("MOTOR_PROBAR_")
        }
        self.addCleanup(self._sin_permisos_nuevos)

    def _sin_permisos_nuevos(self):
        """Un ensayo activa su permiso sin dejarlo puesto para el resto."""
        self.assertEqual(
            {
                clave: valor
                for clave, valor in os.environ.items()
                if clave.startswith("MOTOR_PROBAR_")
            },
            self.permisos,
            "una prueba dejó un permiso vivo activo para el resto de la corrida",
        )

    def _centinela_para(self, cliente: str) -> Path:
        """La ruta del centinela; salta si el cliente no tiene centinela aquí."""
        encontrado = shutil.which(cliente)
        carpeta = guarda_home.bin_centinelas()
        if encontrado is None or not carpeta:
            self.skipTest(f"{cliente} no está instalado en esta máquina")
        ruta = Path(encontrado)
        self.assertEqual(
            ruta.parent,
            Path(carpeta),
            f"{cliente} no resuelve al centinela y se ejecutaría el CLI real",
        )
        return ruta

    @unittest.skipIf(os.name == "nt", SIN_POSIX)
    def test_el_path_de_la_suite_no_alcanza_los_clientes_reales(self):  # no-roja-en-base: prueba del arnés de la batería; el archivo de guarda viaja al worktree base
        """Todo CLI del catálogo que exista resuelve al centinela, no al real."""
        carpeta = guarda_home.bin_centinelas()
        self.assertTrue(carpeta, "la batería no instaló los centinelas")
        for cliente in guarda_home.CLIENTES_CENTINELA:
            with self.subTest(cliente=cliente):
                encontrado = shutil.which(cliente)
                if encontrado is None:
                    continue
                self.assertEqual(Path(encontrado).parent, Path(carpeta), cliente)

    @unittest.skipIf(os.name == "nt", SIN_POSIX)
    def test_el_centinela_de_agy_frena_el_cli_real(self):  # no-roja-en-base: prueba del arnés de la batería; el archivo de guarda viaja al worktree base
        """El centinela anota la llamada y sale 86 sin ejecutar el agy real."""
        ruta = self._centinela_para("agy")
        log = Path(guarda_home.llamadas_centinela())
        antes = log.read_text(encoding="utf-8").count("agy") if log.is_file() else 0
        entorno = dict(os.environ)
        entorno.pop("MOTOR_PROBAR_AGY_VIVO", None)
        corrida = subprocess.run(
            [str(ruta), "--version"],
            env=entorno,
            capture_output=True,
            text=True,
        )
        self.assertEqual(corrida.returncode, 86, corrida.stderr)
        self.assertIn("centinela", corrida.stderr)
        self.assertIn("MOTOR_PROBAR_AGY_VIVO=1", corrida.stderr)
        despues = log.read_text(encoding="utf-8").count("agy") if log.is_file() else 0
        self.assertEqual(despues, antes + 1, "el centinela no anotó la llamada")
