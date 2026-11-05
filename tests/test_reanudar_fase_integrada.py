"""Un encargo legado desplegado retoma la fase siguiente sin repetir A integrada."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motor.encargos import ciclo
from motor.github import FakeGitHub
from motor.workspace import git
from tests.test_flujo_revisiones import Recorrido, sonda_sin_aislamiento_del_host
from tests.test_github_contrato import _bare_con_rama


PLAN = """# Producto

## Bloque A — Documentación y SQL
lane: fast
depende: base-preexistente

## Bloque B — Receta disponible
lane: gate
depende: A
prueba-real: test -f ops/deploy.sh

## Bloque D — Servicio desplegado
lane: release
depende: B
despliegue: desplegar=ops/deploy.sh checklist=ops/check.sh rollback=ops/back.sh
"""


def setUpModule():
    sonda_sin_aislamiento_del_host()


class _GitHubContado(FakeGitHub):
    def __init__(self, directorio):
        super().__init__(directorio)
        self.merges = []

    def merge(self, repo, numero, sha, metodo, mensaje):
        self.merges.append((numero, sha))
        return super().merge(repo, numero, sha, metodo, mensaje)

    def comentarios_deepseek(self, repo, numero, sha=""):
        return [{"texto": "DeepSeek: APPROVED", "autor": "DeepSeek", "sha": sha}]


class ReanudarFaseIntegrada(unittest.TestCase):
    def legado_integrado(self, tmp, ids):
        """Recibos anteriores a politica_despliegue, con un merge local real."""
        repo, _, _ = _bare_con_rama(Path(tmp))
        r = Recorrido(
            tmp, "legado-fases", "Ejecuta plans/producto.md",
            entrega_nivel="desplegado", repo=repo, base_rama="main",
        )
        self.addCleanup(r.reg.close)
        r.reg.anotar(r.enc, {"kind": "fases", "data": {"fases": [
            {"id": ids[0], "resultado": "Documentación y SQL"},
            {"id": ids[1], "resultado": "Receta disponible", "requiere": [ids[0]]},
            {"id": ids[2], "resultado": "Servicio desplegado", "requiere": [ids[1]]},
        ]}}, "fases-legadas")
        self.assertEqual(ciclo._asegurar_repo(r.fold(), r.espacio), "")
        (r.espacio / "plans").mkdir()
        (r.espacio / "plans/producto.md").write_text(PLAN, encoding="utf-8")
        (r.espacio / "documentacion.md").write_text("SQL documentado y comprobado.\n")
        r.incorporar()
        r.juicio({"veredicto": "aprobado", "evidencia": "Revisé documentación y SQL de A"})
        r.incorporar()
        e = r.fold()
        sha, causa = ciclo._commit_push(e, r.espacio)
        self.assertEqual(causa, "")
        rama = git("branch", "--show-current", cwd=r.espacio).stdout.strip()
        gh = _GitHubContado(Path(tmp) / "github")
        pr = gh.abrir_pr(repo, rama, "main", "A: documentación y SQL", "Aprobado")
        gh.marcar_listo(repo, pr.numero)

        def entrega(paso, commit):
            r.reg.anotar(r.enc, {"kind": "entrega_externa", "data": {
                "fase": ids[0], "intento": e.intento, "paso": paso,
                "repo": repo, "rama": rama, "base": "main", "numero": pr.numero,
                "sha": commit, "recibo": commit, "hora": r.hora,
            }}, f"legado:{paso}:{commit}")

        for paso in ("commit", "push", "pr_listo"):
            entrega(paso, sha)
        ciclo._anotar_lectura_deepseek(
            r.reg, r.fold(), pr.numero, sha,
            gh.comentarios_deepseek(repo, pr.numero, sha), r.hora,
        )
        fusionado = gh.merge(repo, pr.numero, sha, "squash", "A integrada")
        entrega("integrado", fusionado.merge_commit)
        self.assertEqual(r.fold().fase_activa, ids[0])
        self.assertEqual(r.fold().nivel_alcanzado, "integrado")
        self.assertEqual(r.eventos("politica_despliegue"), [])
        return r, gh, fusionado

    def test_parar_continuar_avanza_a_b_sin_repetir_el_trabajo_integrado_de_a(self):
        self.comprobar_continuacion(("A", "B", "D"))

    def test_continuar_propuesta_avanza_a_motor_despliegue_sin_repetir_revision_o_merge(self):
        self.comprobar_continuacion(("propuesta", "motor-despliegue", "meta-sombra"))

    def comprobar_continuacion(self, ids):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r, gh, fusionado = self.legado_integrado(tmp, ids)
            revisiones = r.eventos("revision")
            reservas = r.eventos("reserva")
            generacion = r.fold().generacion
            integraciones = [e for e in r.eventos("entrega_externa") if e["data"]["paso"] == "integrado"]
            sha_aprobado = r.fold().pr_sha

            ciclo.ordenar(r.reg, r.enc, "parar", "parar-legado", ciclo.ahora_iso())
            r.reg.anotar(r.enc, {"kind": "arbol", "data": {
                "observacion": "ausente", "hora": ciclo.ahora_iso(),
            }}, "parada-sin-procesos")
            self.assertEqual(r.fold().estado, "detenido")
            ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-legado", ciclo.ahora_iso())
            self.assertEqual(r.fold().estado, "recibido")

            final = ciclo._paso_entrega(r.reg, r.fold(), gh, ciclo.ahora_iso())

            self.assertEqual(final.fase_activa, ids[1])
            self.assertEqual(final.estado, "recibido")
            self.assertEqual(final.entrega.nivel, "desplegado")
            a, b, d = final.fases
            self.assertEqual(a.estado, "preparada")
            self.assertEqual(a.nivel_alcanzado, "integrado")
            self.assertEqual(a.integrado_sha, fusionado.merge_commit)
            self.assertEqual(a.pr_sha, sha_aprobado)
            self.assertNotEqual(d.estado, "preparada")
            self.assertEqual(b.requiere, ((ids[0], a.producto.firma),))
            self.assertEqual(final.generacion, generacion)
            self.assertEqual(r.eventos("reserva"), reservas)
            self.assertEqual(r.eventos("revision"), revisiones)
            self.assertEqual(gh.merges, [(fusionado.numero, sha_aprobado)])
            self.assertEqual([
                e for e in r.eventos("entrega_externa") if e["data"]["paso"] == "integrado"
            ], integraciones)
            self.assertFalse(any(
                e["data"]["paso"] == "desplegado" for e in r.eventos("entrega_externa")
            ))
