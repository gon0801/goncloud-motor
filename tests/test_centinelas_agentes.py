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
    @unittest.skipIf(os.name == "nt", SIN_POSIX)
    def test_el_path_de_la_suite_no_alcanza_los_clientes_reales(self):  # no-roja-en-base: prueba del arnés de la batería; el archivo de guarda viaja al worktree base
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
        if shutil.which("agy") is None:
            self.skipTest("agy no está instalado en esta máquina")
        log = Path(guarda_home.llamadas_centinela())
        antes = log.read_text(encoding="utf-8").count("agy") if log.is_file() else 0
        entorno = dict(os.environ)
        entorno.pop("MOTOR_PROBAR_AGY_VIVO", None)
        corrida = subprocess.run(
            ["agy", "--version"],
            env=entorno,
            capture_output=True,
            text=True,
        )
        self.assertEqual(corrida.returncode, 86, corrida.stderr)
        self.assertIn("centinela", corrida.stderr)
        self.assertIn("MOTOR_PROBAR_AGY_VIVO=1", corrida.stderr)
        despues = log.read_text(encoding="utf-8").count("agy") if log.is_file() else 0
        self.assertEqual(despues, antes + 1, "el centinela no anotó la llamada")
