"""Los candados de calidad detectan lo que dicen detectar."""

from __future__ import annotations

import ast
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tools.calidad import (
    _comparar,
    _comparar_io,
    _llamadas_io,
    _lista,
    diagnosticos_lint,
    diagnosticos_tipos,
)


class DeteccionDeLecturas(unittest.TestCase):
    def test_detecta_read_text_y_json_loads(self):
        arbol = ast.parse(
            "import json\n"
            "from pathlib import Path\n"
            "Path('a').read_text(encoding='utf-8')\n"
            "json.loads(Path('b').read_text(encoding='utf-8'))\n"
        )
        patrones = [p for p, _ in _llamadas_io(arbol)]
        self.assertEqual(patrones.count("read_text"), 2)
        self.assertEqual(patrones.count("json.loads(read_text)"), 1)

    def test_detecta_write_text(self):
        arbol = ast.parse("from pathlib import Path\nPath('a').write_text('x')\n")
        self.assertEqual([p for p, _ in _llamadas_io(arbol)], ["write_text"])


class ReglasYGenerados(unittest.TestCase):
    def test_claude_es_copia_generada_de_agents(self):
        raiz = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (raiz / "CLAUDE.md").read_bytes(),
            (raiz / "AGENTS.md").read_bytes(),
            "CLAUDE.md se genera con `cp AGENTS.md CLAUDE.md`; no se edita a mano",
        )


class ComparacionConLaBase(unittest.TestCase):
    def test_un_error_nuevo_falla(self):
        linea_base = {"lint": _lista({("motor/viejo.py", "F401", "x"): 1})}
        actual = {("motor/viejo.py", "F401", "x"): 1, ("motor/nuevo.py", "F401", "y"): 1}
        self.assertEqual(_comparar(actual, linea_base, "lint"), 1)


class LecturasFueraDeMotorIo(unittest.TestCase):
    def test_una_lectura_nueva_falla(self):
        actual = {("motor/nuevo.py", "read_text"): 1}
        self.assertEqual(_comparar_io(actual), 1)

    def test_reintroducir_la_lectura_en_un_archivo_ya_migrado_falla(self):
        actual = {("motor/encargos/fases.py", "json.loads(read_text)"): 1}
        self.assertEqual(
            _comparar_io(actual),
            1,
            "los créditos históricos de un archivo no pueden tapar una lectura nueva",
        )

    def test_daemon_queda_exento_por_el_modo_planes(self):
        actual = {("motor/daemon.py", "read_text"): 3}
        self.assertEqual(_comparar_io(actual), 0)


class HerramientaQueNoAnaliza(unittest.TestCase):
    def _corrida(self, codigo, stdout, stderr):
        return subprocess.CompletedProcess([], codigo, stdout, stderr)

    def test_tipos_falla_si_no_hay_resultado(self):
        with mock.patch(
            "tools.calidad.subprocess.run",
            return_value=self._corrida(2, "", "error de herramienta\n"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                diagnosticos_tipos()
        self.assertIn("no pudo analizar", str(ctx.exception))

    def test_lint_falla_si_no_hay_resultado(self):
        with mock.patch(
            "tools.calidad.subprocess.run",
            return_value=self._corrida(2, "", "error de herramienta\n"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                diagnosticos_lint()
        self.assertIn("no pudo analizar", str(ctx.exception))

    def test_tipos_acepta_salida_valida_con_diagnosticos(self):
        datos = {
            "generalDiagnostics": [
                {"severity": "error", "file": "/x/motor/a.py", "rule": "r", "message": "m"}
            ]
        }
        with mock.patch(
            "tools.calidad.subprocess.run",
            return_value=self._corrida(1, json.dumps(datos), ""),
        ):
            self.assertEqual(sum(diagnosticos_tipos().values()), 1)

    def test_lint_acepta_una_lista_vacia_valida(self):
        with mock.patch(
            "tools.calidad.subprocess.run",
            return_value=self._corrida(0, "[]", ""),
        ):
            self.assertEqual(sum(diagnosticos_lint().values()), 0)


if __name__ == "__main__":
    unittest.main()
