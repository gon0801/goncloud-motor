"""El commit del producto no falla cuando el repo ya ignora `.interno`.

El pathspec de exclusion nombraba una ruta ignorada y `git add` devolvia 1. El
indice se prepara agregando todo y sacando despues lo interno. Las pruebas usan
un repo temporal real para ver el commit, el arbol y el remoto.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos.ciclo import _commit_push
from motor.encargos.integracion import _commit_pendiente

RAMA = "encargo/enc_x"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@l", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _encargo():
    return SimpleNamespace(
        id="enc_x",
        clave="clave-x",
        texto="pedido de prueba",
        fase_actual=None,
        fases=(),
        entrega=SimpleNamespace(rama=RAMA),
    )


class _Repo:
    def __init__(self, raiz: Path):
        self.bare = raiz / "remoto.git"
        subprocess.run(
            ["git", "init", "--bare", "--quiet", "-b", "main", str(self.bare)],
            check=True,
        )
        self.espacio = raiz / "espacio"
        self.espacio.mkdir()
        _git(self.espacio, "init", "--quiet", "-b", "main")
        (self.espacio / "producto.txt").write_text("base\n", encoding="utf-8")
        _git(self.espacio, "add", "-A")
        _git(self.espacio, "commit", "--quiet", "-m", "base")
        _git(self.espacio, "remote", "add", "origin", str(self.bare))
        _git(self.espacio, "push", "--quiet", "origin", "main")
        _git(self.espacio, "checkout", "--quiet", "-b", RAMA)
        _git(self.espacio, "push", "--quiet", "origin", f"{RAMA}:refs/heads/{RAMA}")

    def arbol(self, ref: str = "HEAD") -> list[str]:
        return _git(self.espacio, "ls-tree", "-r", "--name-only", ref).splitlines()

    def indice(self) -> list[str]:
        return _git(self.espacio, "ls-files").splitlines()

    def remoto(self) -> list[str]:
        return _git(
            self.bare, "ls-tree", "-r", "--name-only", f"refs/heads/{RAMA}"
        ).splitlines()


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.repo = _Repo(Path(tmp.name))
        self.espacio = self.repo.espacio

    def _cambio_de_producto(self):
        (self.espacio / "producto.txt").write_text("nuevo\n", encoding="utf-8")

    def _ruido_interno(self):
        for ruta in (
            ".interno/nota.txt",
            "sub/.interno/nota.txt",
            "sub/deep/.interno/nota.txt",
            "__pycache__/x.pyc",
            "sub/__pycache__/y.pyc",
            ".runtime/estado",
            "sub/deep/.runtime/estado",
        ):
            archivo = self.espacio / ruta
            archivo.parent.mkdir(parents=True, exist_ok=True)
            archivo.write_text(f"{ruta}\n", encoding="utf-8")

    def _escenario_ignorado(self):
        (self.espacio / ".gitignore").write_text(
            ".interno\n__pycache__/\n.runtime/\n", encoding="utf-8"
        )
        self._ruido_interno()
        self._cambio_de_producto()

    def _escenario_sin_gitignore(self):
        self._ruido_interno()
        self._cambio_de_producto()

    def _escenario_rastreado(self):
        (self.espacio / ".interno").mkdir()
        (self.espacio / ".interno" / "nota.txt").write_text(
            "antes\n", encoding="utf-8"
        )
        _git(self.espacio, "add", ".interno")
        _git(self.espacio, "commit", "--quiet", "-m", "rastrea interno")
        (self.espacio / ".interno" / "nota.txt").write_text(
            "despues\n", encoding="utf-8"
        )
        self._ruido_interno()
        self._cambio_de_producto()

    def _rastrear_cache(self):
        (self.espacio / ".runtime").mkdir()
        (self.espacio / ".runtime" / "estado").write_text("antes\n", encoding="utf-8")
        (self.espacio / ".runtime" / "viejo").write_text("viejo\n", encoding="utf-8")
        _git(self.espacio, "add", ".runtime")
        _git(self.espacio, "commit", "--quiet", "-m", "rastrea cache")
        (self.espacio / ".runtime" / "estado").write_text(
            "despues\n", encoding="utf-8"
        )
        (self.espacio / ".runtime" / "viejo").unlink()
        (self.espacio / ".runtime" / "nuevo").write_text("nuevo\n", encoding="utf-8")
        self._cambio_de_producto()

    def _comprobar_cache_rastreada(self):
        arbol = self.repo.arbol()
        self.assertIn(".runtime/estado", arbol)
        self.assertIn(".runtime/viejo", arbol)
        self.assertNotIn(".runtime/nuevo", arbol)
        self.assertEqual(
            _git(self.espacio, "show", "HEAD:.runtime/estado"), "antes\n"
        )
        self.assertEqual(
            _git(self.espacio, "show", "HEAD:.runtime/viejo"), "viejo\n"
        )
        self.assertEqual(
            set(_git(self.espacio, "status", "--porcelain", "--", ".runtime").splitlines()),
            {" M .runtime/estado", " D .runtime/viejo", "?? .runtime/nuevo"},
        )

    def _internas(self, rutas: list[str]) -> list[str]:
        return [
            ruta
            for ruta in rutas
            if any(p in {".interno", "__pycache__", ".runtime"} for p in Path(ruta).parts)
        ]

    def _comprobar_commit(self, remoto: bool = True):
        arbol = self.repo.arbol()
        self.assertIn("producto.txt", arbol)
        self.assertEqual(self._internas(arbol), [])
        self.assertEqual(self._internas(self.repo.indice()), [])
        if remoto:
            self.assertEqual(self._internas(self.repo.remoto()), [])


class CommitDelProducto(_Base):
    def test_con_interno_en_gitignore_commitea_el_resto(self):
        self._escenario_ignorado()

        sha, causa = _commit_push(_encargo(), self.espacio)

        self.assertEqual(causa, "")
        self.assertEqual(_git(self.espacio, "rev-parse", "HEAD").strip(), sha)
        self._comprobar_commit()
        self.assertEqual(
            _git(self.espacio, "show", "HEAD:producto.txt"), "nuevo\n"
        )

    def test_sin_gitignore_interno_tampoco_entra(self):
        self._escenario_sin_gitignore()

        _, causa = _commit_push(_encargo(), self.espacio)

        self.assertEqual(causa, "")
        self._comprobar_commit()

    def test_interno_ya_rastreado_sale_del_commit(self):
        self._escenario_rastreado()

        _, causa = _commit_push(_encargo(), self.espacio)

        self.assertEqual(causa, "")
        self._comprobar_commit()

    def test_un_add_que_falla_por_otra_razon_sigue_siendo_visible(self):
        self._cambio_de_producto()
        (self.espacio / ".git" / "index.lock").write_text("", encoding="utf-8")

        sha, causa = _commit_push(_encargo(), self.espacio)

        self.assertEqual(sha, "")
        self.assertIn("git add fallo", causa)

    def test_una_cache_ya_rastreada_se_conserva(self):
        self._rastrear_cache()

        _, causa = _commit_push(_encargo(), self.espacio)

        self.assertEqual(causa, "")
        self._comprobar_cache_rastreada()


class CommitPendienteDeIntegracion(_Base):
    def test_con_interno_en_gitignore_commitea_el_resto(self):
        self._escenario_ignorado()
        antes = _git(self.espacio, "rev-parse", "HEAD").strip()

        causa = _commit_pendiente(_encargo(), self.espacio, "main")

        self.assertEqual(causa, "")
        self.assertNotEqual(_git(self.espacio, "rev-parse", "HEAD").strip(), antes)
        self._comprobar_commit(remoto=False)

    def test_sin_gitignore_interno_tampoco_entra(self):
        self._escenario_sin_gitignore()

        causa = _commit_pendiente(_encargo(), self.espacio, "main")

        self.assertEqual(causa, "")
        self._comprobar_commit(remoto=False)

    def test_interno_ya_rastreado_sale_del_commit(self):
        self._escenario_rastreado()

        causa = _commit_pendiente(_encargo(), self.espacio, "main")

        self.assertEqual(causa, "")
        self._comprobar_commit(remoto=False)

    def test_un_add_que_falla_por_otra_razon_sigue_siendo_visible(self):
        self._cambio_de_producto()
        (self.espacio / ".git" / "index.lock").write_text("", encoding="utf-8")

        causa = _commit_pendiente(_encargo(), self.espacio, "main")

        self.assertEqual(causa, "no se pudo preparar el producto para la mezcla")

    def test_una_cache_ya_rastreada_se_conserva(self):
        self._rastrear_cache()

        causa = _commit_pendiente(_encargo(), self.espacio, "main")

        self.assertEqual(causa, "")
        self._comprobar_cache_rastreada()


if __name__ == "__main__":
    unittest.main()
