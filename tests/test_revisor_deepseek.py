"""La respuesta única a «¿este repo tiene el revisor DeepSeek en este SHA?».

Solo se salta la espera un repo cuyo SHA y cuya base se leyeron y no nombran al
revisor en ningún workflow. Todo lo dudoso espera el tope de la política.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from motor.encargos.politica import TOPE_DEEPSEEK_MIN
from motor.encargos.revisor_deepseek import revisor_deepseek
from tests import git_env_limpio

WORKER = ".github/workflows/ai-review-worker.yml"
PUBLISH = ".github/workflows/ai-review-publish.yml"
CI = ".github/workflows/ci.yml"


def _git(donde: Path, *args: str) -> str:
    hecho = subprocess.run(
        ["git", "-C", str(donde), *args],
        capture_output=True,
        text=True,
        env=git_env_limpio(),
        check=True,
    )
    return hecho.stdout.strip()


class _Repo:
    """Un remoto con su `main` y el espacio clonado, como el del encargo."""

    def __init__(self, tmp: Path, base: dict[str, str]):
        self.remoto = tmp / "repo.git"
        self.semilla = tmp / "semilla"
        self.espacio = tmp / "espacio"
        _git(tmp, "init", "--bare", "-q", "-b", "main", str(self.remoto))
        self.semilla.mkdir()
        _git(self.semilla, "init", "-q", "-b", "main")
        self.publicar_en_main({"base.txt": "base\n", **base})
        _git(tmp, "clone", "-q", str(self.remoto), str(self.espacio))

    def _commit(self, donde: Path, archivos: dict[str, str | None]) -> str:
        for ruta, texto in archivos.items():
            destino = donde / ruta
            if texto is None:
                _git(donde, "rm", "-q", ruta)
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(texto, encoding="utf-8")
            _git(donde, "add", ruta)
        _git(
            donde, "-c", "user.name=t", "-c", "user.email=t@local",
            "commit", "-q", "-m", "cambio",
        )
        return _git(donde, "rev-parse", "HEAD")

    def publicar_en_main(self, archivos: dict[str, str | None]) -> None:
        self._commit(self.semilla, archivos)
        _git(self.semilla, "push", "-q", str(self.remoto), "main")

    def sha_del_pr(self, archivos: dict[str, str | None]) -> str:
        return self._commit(self.espacio, archivos)


class LaRespuestaUnica(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="revisor-deepseek-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_sin_el_revisor_en_el_sha_ni_en_la_base_no_se_espera(self):
        repo = _Repo(self.tmp, {CI: "name: ci\non: push\n"})
        sha = repo.sha_del_pr({"saludo.txt": "hola\n"})
        revisor = revisor_deepseek(repo.espacio, sha, "main")
        self.assertEqual(revisor.espera_min, 0)
        self.assertIsNone(revisor.disparo)
        self.assertIn("sin revisor", revisor.nota)

    def test_el_worker_en_el_sha_espera_el_tope_y_dispara_la_recuperacion(self):
        for archivos, disparado in (
            ({WORKER: "name: ai-review-worker\n"}, "ai-review-worker.yml"),
            (
                {WORKER: "name: ai-review-worker\n", PUBLISH: "name: ai-review-publish\n"},
                "ai-review-publish.yml",
            ),
        ):
            with self.subTest(disparado=disparado):
                tmp = Path(tempfile.mkdtemp(dir=self.tmp))
                repo = _Repo(tmp, archivos)
                sha = repo.sha_del_pr({"saludo.txt": "hola\n"})
                revisor = revisor_deepseek(repo.espacio, sha, "main")
                self.assertEqual(revisor.espera_min, TOPE_DEEPSEEK_MIN)
                self.assertEqual(revisor.nota, "")
                self.assertIsNotNone(revisor.disparo)
                assert revisor.disparo is not None
                self.assertEqual(revisor.disparo[0], disparado)

    def test_un_pr_que_borra_el_revisor_espera_porque_la_base_lo_corre(self):
        repo = _Repo(
            self.tmp,
            {WORKER: "name: ai-review-worker\n", PUBLISH: "name: ai-review-publish\n"},
        )
        sha = repo.sha_del_pr({WORKER: None, PUBLISH: None})
        revisor = revisor_deepseek(repo.espacio, sha, "main")
        self.assertEqual(revisor.espera_min, TOPE_DEEPSEEK_MIN)
        self.assertIsNone(revisor.disparo)

    def test_la_base_que_recibio_el_revisor_despues_del_clon_espera(self):
        repo = _Repo(self.tmp, {})
        sha = repo.sha_del_pr({"saludo.txt": "hola\n"})
        repo.publicar_en_main({PUBLISH: "name: ai-review-publish\n"})
        revisor = revisor_deepseek(repo.espacio, sha, "main")
        self.assertEqual(revisor.espera_min, TOPE_DEEPSEEK_MIN)

    def test_el_revisor_con_otro_nombre_de_archivo_espera(self):
        for ruta, texto in (
            (".github/workflows/revision.yml", "uses: gon0801/goncloud-pr-review@abc\n"),
            (".github/workflows/revision.yaml", "run: deepseek-review\n"),
            (".github/workflows/ai-review.yml", "name: revisor\n"),
        ):
            with self.subTest(ruta=ruta):
                tmp = Path(tempfile.mkdtemp(dir=self.tmp))
                repo = _Repo(tmp, {})
                sha = repo.sha_del_pr({ruta: texto})
                revisor = revisor_deepseek(repo.espacio, sha, "main")
                self.assertEqual(revisor.espera_min, TOPE_DEEPSEEK_MIN)

    def test_lo_que_no_se_puede_leer_espera(self):
        repo = _Repo(self.tmp, {})
        sha = repo.sha_del_pr({"saludo.txt": "hola\n"})
        for espacio, commit, base in (
            (repo.espacio, "f" * 40, "main"),
            (repo.espacio, sha, "rama-que-no-existe"),
            (self.tmp / "no-es-un-repo", sha, "main"),
        ):
            with self.subTest(commit=commit[:7], base=base, espacio=espacio.name):
                revisor = revisor_deepseek(espacio, commit, base)
                self.assertEqual(revisor.espera_min, TOPE_DEEPSEEK_MIN)


if __name__ == "__main__":
    unittest.main()
