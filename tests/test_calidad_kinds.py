"""`calidad.py kinds` frena un escritor de un evento que el reductor de la base no pliega.

Desplegar un escritor así deja en el registro un evento que la versión anterior
no sabe plegar, y la compuerta de plegado del despliegue bloquea volver a ella.
Cada prueba arma un repo de git con su propio reductor, guarda la base en un
commit y corre la herramienta como la corre el CI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "calidad.py"

_DOMINIO = """
class TransicionIlegal(Exception):
    pass


class Encargo:
    pass


def reducir(estado, evento, nuevo=True):
    kind = evento["kind"]
    if kind == "entrega":
        return Encargo()
    if kind in {plegables!r}:
        return estado
    raise TransicionIlegal({desconocido})
"""
_SIN_NOMBRE = '"evento desconocido"'
_CON_NOMBRE = 'f"evento desconocido: {kind}"'


class Repo:
    def __init__(self, prueba: unittest.TestCase) -> None:
        directorio = tempfile.mkdtemp()
        prueba.addCleanup(shutil.rmtree, directorio, True)
        self.raiz = Path(directorio)
        (self.raiz / "tools").mkdir()
        shutil.copy(HERRAMIENTA, self.raiz / "tools" / "calidad.py")
        for paquete in ("motor", "motor/encargos", "motor/integraciones"):
            self.escribir(f"{paquete}/__init__.py", "")
        self.git("init", "-q", "-b", "main")

    def escribir(self, ruta: str, texto: str) -> None:
        destino = self.raiz / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")

    def reductor(self, *plegables: str, desconocido: str = _CON_NOMBRE) -> None:
        self.escribir(
            "motor/encargos/dominio.py",
            _DOMINIO.format(plegables=set(plegables), desconocido=desconocido),
        )

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=prueba", "-c", "user.email=prueba@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            cwd=self.raiz, capture_output=True, text=True, check=True,
        ).stdout.strip()

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        return self.git("rev-parse", "HEAD")

    def kinds(self, base: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.raiz / "tools" / "calidad.py"), "kinds", "--base", base],
            cwd=self.raiz, capture_output=True, text=True, check=False,
        )


_ESCRITOR = 'def detener(registro, ident):\n    registro.anotar(ident, {"kind": "fallo_interno", "data": {}}, "k")\n'


class EscritorNuevo(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo(self)
        self.repo.escribir(
            "motor/encargos/ciclo.py",
            'def anotar(registro, ident):\n    registro.anotar(ident, {"kind": "nota", "data": {}}, "k")\n',
        )

    def test_un_kind_que_la_base_no_pliega_falla_y_dice_donde_se_escribe(self):
        self.repo.reductor("nota")
        base = self.repo.commit()
        self.repo.escribir("motor/encargos/contencion.py", _ESCRITOR)

        corrida = self.repo.kinds(base)

        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("fallo_interno: motor/encargos/contencion.py:2", corrida.stderr)
        self.assertNotIn("nota:", corrida.stderr)
        self.assertIn("PR propio", corrida.stderr)

    def test_el_mismo_escritor_pasa_cuando_la_base_ya_lo_pliega(self):
        self.repo.reductor("nota", "fallo_interno")
        base = self.repo.commit()
        self.repo.escribir("motor/encargos/contencion.py", _ESCRITOR)

        corrida = self.repo.kinds(base)

        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(base[:12], corrida.stdout)

    def test_ensenarlo_al_reductor_en_el_mismo_cambio_no_alcanza(self):
        self.repo.reductor("nota")
        base = self.repo.commit()
        self.repo.reductor("nota", "fallo_interno")
        self.repo.escribir("motor/encargos/contencion.py", _ESCRITOR)

        corrida = self.repo.kinds(base)

        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("fallo_interno:", corrida.stderr)

    def test_ve_las_tres_formas_de_escribir_un_evento(self):
        self.repo.reductor("nota")
        base = self.repo.commit()
        formas = {
            "motor/encargos/lote.py": 'def e(Evento):\n    return Evento("con_evento", {}, "k")\n',
            "motor/encargos/orden.py": 'def a(registro):\n    registro.append("i", "con_append", {}, "k", "t")\n',
            "motor/integraciones/via.py": 'def d(registro):\n    registro.anotar("i", {"kind": "con_dict", "data": {}}, "k")\n',
        }
        for ruta, texto in formas.items():
            self.repo.escribir(ruta, texto)

        corrida = self.repo.kinds(base)

        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        for kind, ruta in zip(("con_evento", "con_append", "con_dict"), formas, strict=True):
            self.assertIn(f"{kind}: {ruta}:2", corrida.stderr)

    def test_un_reductor_que_no_nombra_el_kind_desconocido_tambien_se_juzga(self):
        self.repo.reductor("nota", desconocido=_SIN_NOMBRE)
        base = self.repo.commit()
        self.repo.escribir("motor/encargos/contencion.py", _ESCRITOR)

        corrida = self.repo.kinds(base)

        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("fallo_interno:", corrida.stderr)


class SinRespuestaDelReductor(unittest.TestCase):
    def test_un_reductor_que_pliega_cualquier_kind_no_deja_pasar(self):
        repo = Repo(self)
        repo.escribir("motor/encargos/dominio.py", "def reducir(estado, evento, nuevo=True):\n    return object()\n")
        base = repo.commit()
        repo.escribir("motor/encargos/contencion.py", _ESCRITOR)

        corrida = repo.kinds(base)

        self.assertNotEqual(corrida.returncode, 0, corrida.stdout)
        self.assertIn("kind inventado", corrida.stderr)


if __name__ == "__main__":
    unittest.main()
