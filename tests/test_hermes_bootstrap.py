"""El bootstrap de Hermes corre en otro intérprete y define su propio IO."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from motor.flota import actualizaciones


class BootstrapDeHermes(unittest.TestCase):
    def test_usa_solo_stdlib_y_prepara_el_soul(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            config, home, instalacion = raiz / "config", raiz / "home", raiz / "py"
            for ruta, texto in (
                ("director-plugins.json", '{"director": "dir", "plugins": []}'),
                ("shared/CONTRACT.md", "contrato"),
                ("shared/BUSINESS.md", "negocio"),
                ("roles/dir.md", "rol"),
                ("plugins/goncloud-team/poteto.py", 'def bloque():\n    return "bloque"\n'),
            ):
                destino = config / ruta
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(texto, encoding="utf-8")
            (instalacion / "hermes_cli").mkdir(parents=True)
            (instalacion / "hermes_cli/__init__.py").write_text("", encoding="utf-8")
            (instalacion / "hermes_cli/profiles.py").write_text(
                "import os\n"
                "from pathlib import Path\n\n"
                "def get_profile_dir(nombre):\n"
                "    return Path(os.environ['HERMES_TEST_HOME']) / 'profiles' / nombre\n\n"
                "def create_profile(nombre, no_alias=True, description='', soul=''):\n"
                "    destino = get_profile_dir(nombre)\n"
                "    destino.mkdir(parents=True, exist_ok=True)\n"
                "    (destino / 'SOUL.md').write_text(soul, encoding='utf-8')\n",
                encoding="utf-8",
            )
            (instalacion / "hermes_cli/default_soul.py").write_text(
                "DEFAULT_SOUL_MD = 'por omision'\n", encoding="utf-8"
            )
            env = dict(os.environ, HERMES_TEST_HOME=str(home))
            actualizaciones._plugins_hermes(
                config, home, [sys.executable], instalacion, env
            )
            soul = (home / "profiles/dir/SOUL.md").read_text(encoding="utf-8")
            for texto in ("contrato", "negocio", "rol", "bloque"):
                self.assertIn(texto, soul)


if __name__ == "__main__":
    unittest.main()
