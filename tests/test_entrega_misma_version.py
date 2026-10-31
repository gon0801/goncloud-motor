"""Devolver el trabajo por un bloqueante de DeepSeek o de checks y recibir la
misma version no puede dejar el encargo quieto: el producto conserva su commit
y el freno aplica su escalera."""

from __future__ import annotations

import subprocess
import tempfile
import unittest

from motor.encargos.ciclo import _aplica_entrega, _paso_entrega, ahora_iso
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import _entrega_pr, _GhConDeepSeek, _lanzaria


def _turno_sin_llm(r, gh):
    """Un barrido del supervisor: la entrega que aplica corre sin agente."""
    e = r.fold()
    if _aplica_entrega(e):
        e = _paso_entrega(r.reg, e, gh, ahora_iso())
    return e


class MismaVersionTrasDevolucion(unittest.TestCase):
    def test_deepseek_misma_version_recupera_sha_y_escala_sin_quedar_quieto(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-ds", _GhConDeepSeek)
            e = vuelta("uno")
            self.assertEqual(e.estrategia, "investigar")
            commit = e.producto.sha_git
            self.assertTrue(commit)
            self.assertEqual(e.deepseek_sha, commit)

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.producto.sha_git, commit)
            self.assertFalse(_aplica_entrega(e))
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertTrue(_lanzaria(e))
            antes = len(r.reg.eventos(r.enc))
            self.assertEqual(_turno_sin_llm(r, vuelta.gh).estrategia, e.estrategia)
            self.assertEqual(len(r.reg.eventos(r.enc)), antes)

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("DeepSeek", e.motivo)
            self.assertIn("Se intento", e.motivo)
            self.assertFalse(_aplica_entrega(e))
            self.assertFalse(_lanzaria(e))

    def test_checks_en_rojo_misma_version_recupera_sha_y_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "mv-ci", FakeGitHub,
                {"requeridos": ["ci"], "checks": {"ci": "exit 1"}},
            )
            e = vuelta("uno")
            self.assertEqual(e.estrategia, "investigar")
            commit = e.producto.sha_git
            self.assertTrue(commit)

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.producto.sha_git, commit)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertTrue(_lanzaria(e))

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("checks requeridos en rojo: ci", e.motivo)
            self.assertIn("Se intento", e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_una_version_distinta_no_hereda_el_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-otra", _GhConDeepSeek)
            e = vuelta("uno")
            commit = e.producto.sha_git
            e = vuelta("dos")
            self.assertNotEqual(e.producto.sha_git, commit)
            self.assertEqual(e.deepseek_sha, e.producto.sha_git)

    def test_misma_firma_con_otra_cabeza_de_git_no_hereda_el_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-cabeza", _GhConDeepSeek)
            vuelta("uno")
            subprocess.run(
                ["git", "-C", str(r.espacio), "reset", "--quiet", "--soft", "HEAD~1"],
                check=True,
            )
            r.incorporar()
            self.assertEqual(r.fold().producto.sha_git, "")


if __name__ == "__main__":
    unittest.main()
