"""Devolver el trabajo por un bloqueante de DeepSeek o de checks y recibir la
misma version no puede dejar el encargo quieto: el producto conserva su commit
y el freno aplica su escalera."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest

from motor.encargos.ciclo import _aplica_entrega, _paso_entrega, ahora_iso, barrer
from motor.encargos.dominio import relevos_de
from tests.test_flujo_revisiones import (
    _entrega_pr,
    _lanzaria,
    sonda_sin_aislamiento_del_host,
)

from tests.dobles import github_con_hallazgos


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _turno_sin_llm(r, gh):
    """Un barrido del supervisor: la entrega que aplica corre sin agente."""
    e = r.fold()
    if _aplica_entrega(e):
        e = _paso_entrega(r.reg, e, gh, ahora_iso())
    return e


class MismaVersionTrasDevolucion(unittest.TestCase):
    def test_deepseek_misma_version_recupera_sha_y_releva_sin_quedar_quieto(self):  # no-roja-en-base: la base ya releva con la misma version; la prueba migra del contador a relevos_de
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-ds", github_con_hallazgos)
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
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertTrue(_lanzaria(e))
            antes = len(r.reg.eventos(r.enc))
            self.assertEqual(_turno_sin_llm(r, vuelta.gh).estrategia, e.estrategia)
            self.assertEqual(len(r.reg.eventos(r.enc)), antes)

            r.incorporar()
            e = r.fold()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(relevos_de(e, "responsable"), 1)
            self.assertIn("DeepSeek", e.motivo)
            self.assertFalse(_aplica_entrega(e))
            self.assertTrue(_lanzaria(e))
            r.incorporar()
            e = r.fold()
            self.assertTrue(e.pendientes_entrega)

    def test_una_version_distinta_no_hereda_el_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-otra", github_con_hallazgos)
            e = vuelta("uno")
            commit = e.producto.sha_git
            e = vuelta("dos")
            self.assertNotEqual(e.producto.sha_git, commit)
            self.assertEqual(e.deepseek_sha, e.producto.sha_git)

    def test_misma_firma_con_otra_cabeza_de_git_no_hereda_el_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-cabeza", github_con_hallazgos)
            vuelta("uno")
            subprocess.run(
                ["git", "-C", str(r.espacio), "reset", "--quiet", "--soft", "HEAD~1"],
                check=True,
            )
            r.incorporar()
            self.assertEqual(r.fold().producto.sha_git, "")


def _rerun_del_check(gh, repo, nombre, comando):
    """Vuelve a correr `nombre` sobre el mismo SHA con otro resultado: el
    veredicto guardado se olvida y el check pasa a ejecutar `comando`."""
    cfg = json.loads((gh.d / "repos.json").read_text())
    cfg[repo]["checks"][nombre] = comando
    (gh.d / "repos.json").write_text(json.dumps(cfg))
    estado = json.loads((gh.d / "github.json").read_text())
    estado["checks"] = {
        k: v for k, v in estado["checks"].items() if not k.endswith(f"#{nombre}")
    }
    (gh.d / "github.json").write_text(json.dumps(estado))


def _barrido(r, gh, t=None):
    t = t if t is not None else time.time()
    barrer(r.reg, ahora_iso(t), t, None, gh=gh)
    return r.fold()


def _pasos(r, paso):
    return [
        ev for ev in r.reg.eventos(r.enc)
        if ev["kind"] == "entrega_externa" and ev["data"].get("paso") == paso
    ]
