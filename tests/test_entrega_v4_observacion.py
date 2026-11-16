"""Puerto de GitHub y observación idempotente, sin llamar a GitHub real."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motor.encargos.entrega.tipos import (
    PrAbierto, PrBorrador, PrCerrado, PrMergeado, PrSin,
)
from motor.encargos.registro import Evento, Registro
from motor.github import FakeGitHub, PrView
from motor.workspace import git
from tests.test_github_contrato import _bare_con_rama


class ObservacionDelPR(unittest.TestCase):
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
