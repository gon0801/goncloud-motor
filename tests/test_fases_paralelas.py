"""Fases paralelas: la entrega no sale sin las puntas finales ya revisadas.

A y B corren en paralelo y C consume solo B (o las dos). La rama de C nace de
B, asi que hoy se entrega sin A; estas pruebas exigen que la entrega contenga
la punta final revisada de cada fase terminada, o que el motor falle nombrando
la que falta. Todas usan git real en un temporal.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.encargos import ciclo
from motor.encargos.dominio import Encargo, EntregaAutorizada, Fase, VersionProducto


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@l", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _es_ancestro(cwd: Path, sha: str) -> bool:
    r = _git(cwd, "merge-base", "--is-ancestor", sha, "HEAD", check=False)
    return r.returncode == 0


class _Banco(unittest.TestCase):
    """Repo bare real, dos fases paralelas y el espacio de una tercera."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.bare = self.raiz / "repo.git"
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", "--quiet", str(self.bare)],
            check=True,
        )
        semilla = self.raiz / "semilla"
        semilla.mkdir()
        (semilla / "base.txt").write_text("base\n")
        _git(semilla, "init", "-b", "main", "--quiet")
        _git(semilla, "add", "-A")
        _git(semilla, "commit", "--quiet", "-m", "base")
        _git(semilla, "push", "--quiet", str(self.bare), "main")

    def _rama(self, nombre: str, archivo: str, texto: str) -> str:
        trabajo = self.raiz / "trabajo"
        if not trabajo.exists():
            subprocess.run(
                ["git", "clone", "--quiet", str(self.bare), str(trabajo)],
                check=True,
            )
        _git(trabajo, "checkout", "--quiet", "-B", nombre, "origin/main")
        (trabajo / archivo).write_text(texto)
        _git(trabajo, "add", "-A")
        _git(trabajo, "commit", "--quiet", "-m", nombre)
        sha = _git(trabajo, "rev-parse", "HEAD").stdout.strip()
        _git(trabajo, "push", "--quiet", "origin", f"{nombre}:refs/heads/{nombre}")
        return sha

    def _encargo(self, requiere, conflicto: bool = False):
        texto_a, texto_b = "de A\n", "de B\n"
        archivo_a, archivo_b = "a.txt", "b.txt"
        if conflicto:
            archivo_a = archivo_b = "base.txt"
            texto_a, texto_b = "A pisa base\n", "B pisa base\n"
        sha_a = self._rama("encargo/enc_x-A", archivo_a, texto_a)
        sha_b = self._rama("encargo/enc_x-B", archivo_b, texto_b)

        self.espacio = self.raiz / "espacio"
        subprocess.run(
            ["git", "clone", "--quiet", str(self.bare), str(self.espacio)],
            check=True,
        )
        _git(
            self.espacio,
            "checkout", "--quiet", "-B", "encargo/enc_x-C",
            "origin/encargo/enc_x-B",
        )

        a = Fase(
            id="A", resultado="a", estado="preparada", evidencia="listo",
            producto=VersionProducto(firma="fA", manifiesto=(), sha_git=sha_a),
        )
        b = Fase(
            id="B", resultado="b", estado="preparada", evidencia="listo",
            producto=VersionProducto(firma="fB", manifiesto=(), sha_git=sha_b),
        )
        c = Fase(id="C", resultado="c", estado="trabajo", requiere=requiere)
        enc = Encargo(
            id="enc_x",
            clave="clave-x",
            huella="huella",
            director="cli",
            texto="pedido",
            adjuntos=(),
            restricciones=(),
            canal="canal",
            entrega=EntregaAutorizada(
                nivel="pr", repo="o/r", rama="encargo/enc_x", base="main"
            ),
            fases=(a, b, c),
            fase_activa="C",
            estado="ejecutando",
            generacion=1,
        )
        return enc, sha_a, sha_b


class LaEntregaCubreTodasLasPuntas(_Banco):
    def test_requiere_solo_b_e_incorpora_a(self):
        enc, sha_a, _sha_b = self._encargo((("B", "fB"),))

        causa = ciclo._incorporar_entradas(enc, self.espacio)

        self.assertEqual(causa, "")
        self.assertTrue(
            _es_ancestro(self.espacio, sha_a),
            "la entrega de C no incorporo la punta final de la fase A",
        )
        self.assertEqual((self.espacio / "a.txt").read_text(), "de A\n")

    def test_requiere_las_dos_y_contiene_ambas(self):
        enc, sha_a, sha_b = self._encargo((("A", "fA"), ("B", "fB")))

        causa = ciclo._incorporar_entradas(enc, self.espacio)

        self.assertEqual(causa, "")
        self.assertTrue(_es_ancestro(self.espacio, sha_a), "falta la punta de A")
        self.assertTrue(_es_ancestro(self.espacio, sha_b), "falta la punta de B")

    def test_conflicto_entre_a_y_b_falla_nombrando_la_fase(self):
        enc, _sha_a, _sha_b = self._encargo(
            (("A", "fA"), ("B", "fB")), conflicto=True
        )

        causa = ciclo._incorporar_entradas(enc, self.espacio)

        self.assertIn("conflicto", causa)
        self.assertIn("A", causa)
        self.assertNotEqual(
            _git(
                self.espacio,
                "rev-parse", "--verify", "--quiet", "MERGE_HEAD", check=False,
            ).returncode,
            0,
            "un conflicto visible no deja la mezcla a medias",
        )


class LaBaseConVariasDependenciasQuedaDefinida(_Banco):
    def test_dos_dependencias_vivas_usan_la_base_de_entrega(self):
        enc, sha_a, sha_b = self._encargo((("A", "fA"), ("B", "fB")))

        self.assertEqual(ciclo._base_fase(enc), "main")
        self.assertEqual(ciclo._incorporar_entradas(enc, self.espacio), "")
        self.assertTrue(_es_ancestro(self.espacio, sha_a))
        self.assertTrue(_es_ancestro(self.espacio, sha_b))

    def test_el_cuerpo_del_pr_explica_la_base_y_el_motivo(self):
        enc, _sha_a, _sha_b = self._encargo((("A", "fA"), ("B", "fB")))

        cuerpo = ciclo._cuerpo_pr(enc, self.espacio)

        self.assertIn("Base del PR", cuerpo)
        self.assertIn("main", cuerpo)
        self.assertIn("A, B", cuerpo)


if __name__ == "__main__":
    unittest.main()
