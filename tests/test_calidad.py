"""Los candados de calidad detectan lo que dicen detectar."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tools.calidad import _comparar, _llamadas_io, _lista


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
        linea_base = {"io": _lista({("motor/viejo.py", "read_text"): 1})}
        actual = {("motor/viejo.py", "read_text"): 1, ("motor/nuevo.py", "write_text"): 1}
        self.assertEqual(_comparar(actual, linea_base, "io"), 1)

    def test_repetir_lo_de_la_base_no_falla(self):
        linea_base = {"io": _lista({("motor/viejo.py", "read_text"): 2})}
        self.assertEqual(_comparar({("motor/viejo.py", "read_text"): 2}, linea_base, "io"), 0)

    def test_arreglar_lo_viejo_no_falla(self):
        linea_base = {"io": _lista({("motor/viejo.py", "read_text"): 2})}
        self.assertEqual(_comparar({("motor/viejo.py", "read_text"): 1}, linea_base, "io"), 0)


if __name__ == "__main__":
    unittest.main()
