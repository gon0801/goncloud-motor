"""Puerto de GitHub y observación idempotente, sin llamar a GitHub real."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from motor.encargos.entrega.tipos import (
    PrAbierto, PrBorrador, PrCerrado, PrMergeado, PrSin,
)
from motor.encargos.registro import Evento, Registro
from motor.github import FakeGitHub, GhGitHub, PrView
from motor.workspace import git
from tests.test_github_contrato import _bare_con_rama


class ObservacionDelPR(unittest.TestCase):
    def test_el_real_consulta_head_del_repo_solo_en_pr_abiertos(self) -> None:
        class GhSinRed(GhGitHub):
            def __init__(self) -> None:
                super().__init__()
                self.llamadas: list[tuple[str, ...]] = []

            @staticmethod
            def nombre(repo: str) -> str:
                return repo

            def _gh(self, *args: str) -> str:
                self.llamadas.append(args)
                return "7\n8\n"

        gh = GhSinRed()
        self.assertEqual(gh.prs_con_head("o/r", "encargo/f"), (7, 8))
        self.assertEqual(gh.llamadas, [
            ("api", "--paginate",
             "repos/o/r/pulls?state=open&head=o%3Aencargo%2Ff&per_page=100",
             "--jq", ".[].number"),
        ])

    def test_estado_de_lee_json_de_gh_para_todos_los_estados(self) -> None:
        from motor.encargos.entrega.tipos import estado_de

        class GhSinRed(GhGitHub):
            def __init__(self) -> None:
                super().__init__()
                self.estado = "OPEN"
                self.borrador = False

            @staticmethod
            def nombre(repo: str) -> str:
                return repo

            def _gh(self, *args: str) -> str:
                self.assert_llamada = args
                return json.dumps({
                    "number": 7, "headRefName": "encargo/x",
                    "baseRefName": "main", "isDraft": self.borrador,
                    "headRefOid": "sha", "state": self.estado,
                    "mergeCommit": None if self.estado != "MERGED" else {"oid": "merge"},
                    "body": "",
                })

        gh = GhSinRed()
        for estado, borrador, tipo in (
            ("OPEN", False, PrAbierto), ("OPEN", True, PrBorrador),
            ("MERGED", False, PrMergeado), ("CLOSED", False, PrCerrado),
        ):
            with self.subTest(estado=estado, borrador=borrador):
                gh.estado, gh.borrador = estado, borrador
                vista = gh.ver("o/r", 7)
                self.assertIn("state", gh.assert_llamada[-1])
                convertido = estado_de(vista)
                self.assertIsInstance(convertido, tipo)
                if isinstance(convertido, PrMergeado):
                    self.assertEqual(convertido.merge_commit, "merge")

    def test_estado_de_convierte_la_vista_de_gh_sin_red(self) -> None:
        from motor.encargos.entrega.tipos import estado_de

        casos = (
            ("sin_pr", False, PrSin),
            ("abierto", True, PrBorrador),
            ("abierto", False, PrAbierto),
            ("mergeado", False, PrMergeado),
            ("cerrado", False, PrCerrado),
        )
        for estado, borrador, tipo in casos:
            with self.subTest(estado=estado, borrador=borrador):
                vista = PrView(7, "encargo/x", "otra", borrador, "sha", estado, "")
                convertido = estado_de(vista)
                self.assertIsInstance(convertido, tipo)
                if isinstance(convertido, PrMergeado):
                    self.assertEqual(convertido.merge_commit, "")
        with self.assertRaisesRegex(ValueError, "desconocido"):
            estado_de(PrView(7, "encargo/x", "main", False, "sha", "invalido"))

    def test_observar_solo_escribe_al_cambiar_y_permite_volver_al_anterior(self) -> None:
        from motor.encargos.entrega.observacion import anotar_pr

        with tempfile.TemporaryDirectory() as tmp:
            registro = Registro(Path(tmp))
            registro.append_many("x", (
                Evento("entrega", {"id": "x", "clave": "k", "huella": "h",
                                    "texto": "pedido", "hora": "t0"}, "entrega:x"),
                Evento("fases", {"fases": [{"id": "F", "resultado": "r"}]}, "fases:x"),
            ))
            previo = registro.fold("x")
            abierto = PrView(7, "encargo/x", "main", False, "sha", "abierto")
            cerrado = PrView(7, "encargo/x", "main", False, "sha", "cerrado")
            visto = anotar_pr(registro, previo, "F", abierto, "t1")
            primero = registro.ultimo_evento("x")
            repetido = anotar_pr(registro, visto, "F", abierto, "t2")
            self.assertEqual(registro.ultimo_evento("x"), primero)
            self.assertEqual(repetido.prs, visto.prs)
            visto = anotar_pr(registro, repetido, "F", cerrado, "t3")
            visto = anotar_pr(registro, visto, "F", abierto, "t4")
            self.assertGreater(registro.ultimo_evento("x"), primero)
            self.assertEqual(visto.prs[0].pr.estado, "abierto")
            self.assertEqual(visto.prs[0].pr.numero, 7)


class PuertoDeGitHubParaLimpieza(unittest.TestCase):
    def test_puerto_real_construye_comandos_y_distingue_404(self) -> None:  # no-roja-en-base: protege el contrato del puerto que ya existía en main
        class GhSinRed(GhGitHub):
            def __init__(self) -> None:
                super().__init__()
                self.llamadas: list[tuple[str, ...]] = []
                self.cerrado = False
                self.sin_rama = False

            @staticmethod
            def nombre(repo: str) -> str:
                return repo

            def _gh(self, *args: str) -> str:
                self.llamadas.append(args)
                if args[:2] == ("pr", "close"):
                    self.cerrado = True
                if args[:2] == ("pr", "view"):
                    return json.dumps({
                        "number": 7, "headRefName": "encargo/f",
                        "baseRefName": "main", "isDraft": False,
                        "headRefOid": "sha",
                        "state": "CLOSED" if self.cerrado else "OPEN",
                        "mergeCommit": None, "body": "",
                    })
                if args[:2] == ("api", "--paginate"):
                    return "7\n8\n"
                if args[0] == "api" and "git/ref/heads/" in args[-1] and self.sin_rama:
                    raise RuntimeError("HTTP 404")
                return ""

        gh = GhSinRed()
        self.assertEqual(gh.cerrar_pr("o/r", 7).estado, "cerrado")
        self.assertEqual(gh.cerrar_pr("o/r", 7).estado, "cerrado")
        self.assertEqual(gh.llamadas.count(("pr", "close", "7", "-R", "o/r")), 1)
        self.assertTrue(gh.rama_existe("o/r", "encargo/f"))
        self.assertEqual(gh.prs_con_base("o/r", "encargo/f"), (7, 8))
        gh.borrar_rama("o/r", "encargo/f")
        self.assertIn(
            ("api", "-X", "DELETE", "repos/o/r/git/refs/heads/encargo/f"),
            gh.llamadas,
        )
        self.assertIn(
            ("api", "--paginate", "repos/o/r/pulls?state=open&base=encargo%2Ff&per_page=100",
             "--jq", ".[].number"),
            gh.llamadas,
        )
        gh.sin_rama = True
        self.assertFalse(gh.rama_existe("o/r", "encargo/f"))
        gh.llamadas.clear()
        gh.borrar_rama("o/r", "encargo/f")
        self.assertFalse(any(args[:2] == ("api", "-X") for args in gh.llamadas))

    def test_doble_distingue_ramas_y_pr_abiertos_sin_borrados_reales(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            repo, _sha, trabajo = _bare_con_rama(raiz)
            git("push", "-q", repo, "HEAD:refs/heads/encargo/f", cwd=trabajo)
            gh = FakeGitHub(raiz / "gh")
            propio = gh.abrir_pr(repo, "encargo/f", "main", "uno", "texto")
            apilado = gh.abrir_pr(repo, "feat", "encargo/f", "dos", "texto")
            self.assertTrue(gh.rama_existe(repo, "encargo/f"))
            self.assertEqual(gh.prs_con_base(repo, "encargo/f"), (apilado.numero,))
            self.assertEqual(gh.cerrar_pr(repo, propio.numero).estado, "cerrado")
            self.assertEqual(gh.cerrar_pr(repo, propio.numero).estado, "cerrado")
            gh.borrar_rama(repo, "encargo/f")
            self.assertFalse(gh.rama_existe(repo, "encargo/f"))
            self.assertEqual(gh.prs_con_base(repo, "encargo/f"), (apilado.numero,))


if __name__ == "__main__":
    unittest.main()
