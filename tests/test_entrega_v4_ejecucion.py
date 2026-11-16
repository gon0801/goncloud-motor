"""Los estados remotos producen hechos nuevos sin volver a abrir un PR."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo, politica
from motor.encargos.entrega.ejecutar import (
    base_objetivo_del_pr, conducir_pr, fase_final_por_sondear,
)
from motor.encargos.entrega.observacion import preparar_pr
from motor.encargos.registro import Evento, Registro
from motor.github import FakeGitHub, PrView


class EntregaSegunPR(unittest.TestCase):
    def _registro(self, nivel: str = "pr", fases: list[dict] | None = None,
                  fase_pr: str = "F") -> tuple[Registro, FakeGitHub]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        registro = Registro(base)
        self.addCleanup(registro.close)
        registro.append_many("x", (
            Evento("entrega", {
                "id": "x", "clave": "k", "huella": "h", "texto": "pedido",
                "entrega_nivel": nivel, "repo": "o/r", "rama": "encargo/x",
                "base": "main", "hora": "t0",
            }, "entrega:x"),
            Evento("fases", {"fases": fases or [{"id": "F", "resultado": "r"}]},
                   "fases:x"),
            Evento("entrega_externa", {
                "fase": fase_pr, "paso": "pr_listo", "numero": 7,
                "sha": "h", "hora": "t1",
            }, "pr:x"),
        ))
        return registro, FakeGitHub(base / "fake")

    def _observar(self, registro: Registro, gh: FakeGitHub, estado: str,
                  base: str, fase: str = "") -> tuple:
        gh.responde_ver = lambda _, numero: PrView(
            numero, "encargo/x", base, False, "h", estado,
            "m" if estado == "mergeado" else "",
        )
        enc = registro.fold("x")
        return conducir_pr(registro, enc, gh, "t2", fase)

    def test_seis_celdas_cambiadas_de_punta_a_punta(self) -> None:
        casos = (
            ("pr", "abierto", "main", "recibido", (), False),
            ("pr", "mergeado", "main", "terminado", (), True),
            ("pr", "mergeado", "otra", "detenido", (), True),
            ("integrado", "mergeado", "otra", "detenido", (), True),
            ("pr", "cerrado", "main", "cancelado", ("F",), True),
            ("integrado", "cerrado", "main", "cancelado", ("F",), True),
        )
        for nivel, estado, base, esperado, canceladas, atendido in casos:
            with self.subTest(nivel=nivel, estado=estado, base=base):
                registro, gh = self._registro(nivel)
                enc, consumido = self._observar(registro, gh, estado, base)
                self.assertEqual((enc.estado, enc.canceladas, consumido),
                                 (esperado, canceladas, atendido))
                self.assertEqual(enc.prs[0].pr.estado, estado)
                self.assertEqual(gh.merges, [])

    def test_cierre_de_david_conserva_hallazgos_y_no_exige_revision(self) -> None:
        registro, gh = self._registro()
        enc, _ = self._observar(registro, gh, "mergeado", "main")
        self.assertEqual(enc.estado, "terminado")
        self.assertIsNone(enc.revision_vigente)
        self.assertIn("David mergeó", enc.evidencia[-1])

    def test_el_merge_sin_commit_ni_head_no_acredita_integracion(self) -> None:
        registro, gh = self._registro()
        gh.responde_ver = lambda _, numero: PrView(
            numero, "encargo/x", "main", False, "", "mergeado", "",
        )
        enc, _ = conducir_pr(registro, registro.fold("x"), gh, "t2")
        self.assertEqual(enc.prs[0].pr.estado, "mergeado")
        self.assertEqual((enc.estado, enc.nivel_alcanzado), ("recibido", "pr_abierto"))
        repetido, _ = conducir_pr(registro, enc, gh, "t3")
        self.assertEqual(repetido, enc)

    def test_continuar_acepta_base_distinta_sin_abrir_otro_pr(self) -> None:
        registro, gh = self._registro("integrado")
        enc, _ = self._observar(registro, gh, "mergeado", "otra")
        self.assertEqual(enc.estado, "detenido")
        registro.anotar("x", {"kind": "orden", "data": {
            "accion": "continuar", "hora": "t3",
        }}, "continuar:x")
        enc, _ = conducir_pr(registro, registro.fold("x"), gh, "t4")
        self.assertEqual((enc.estado, enc.nivel_alcanzado),
                         ("terminado", "integrado"))
        self.assertEqual(enc.prs[0].aceptada_en, "otra")
        self.assertEqual(gh.merges, [])

    def test_merge_en_rama_de_otra_fase_espera_hasta_integrarla(self) -> None:
        registro, gh = self._registro(fases=[
            {"id": "F", "resultado": "r"}, {"id": "G", "resultado": "g"},
        ])
        pr_g = {
            "fase": "G", "estado": "abierto", "numero": 8, "head": "h",
            "base": "main", "merge_commit": "", "hora": "t1",
        }
        registro.anotar("x", {"kind": "pr_observado", "data": pr_g}, "observado:g")
        enc, _ = self._observar(registro, gh, "mergeado", "encargo/x-G")
        self.assertEqual((enc.estado, enc.nivel_alcanzado),
                         ("recibido", "pr_abierto"))
        registro.anotar("x", {"kind": "pr_observado", "data": {
            **pr_g, "estado": "mergeado", "merge_commit": "g", "hora": "t3",
        }}, "observado:g:merge")
        enc, _ = conducir_pr(registro, registro.fold("x"), gh, "t4")
        self.assertEqual(enc.fases[0].estado, "preparada")
        self.assertEqual(enc.fase_activa, "G")
        self.assertEqual(gh.merges, [])

    def test_cerrar_una_fase_cancela_dependientes_atomicos(self) -> None:
        fases = [
            {"id": "F", "resultado": "r"},
            {"id": "G", "resultado": "g",
             "requiere": [{"fase": "F", "version": "v"}]},
            {"id": "H", "resultado": "h"},
        ]
        registro, gh = self._registro(fases=fases)
        enc, _ = self._observar(registro, gh, "cerrado", "main")
        self.assertEqual(enc.canceladas, ("F", "G"))
        self.assertNotIn("H", enc.canceladas)
        self.assertEqual([f.estado for f in enc.fases[:2]],
                         ["bloqueada", "bloqueada"])

    def test_fases_no_activas_cerrada_y_mergeada_en_otra_base(self) -> None:
        fases = [{"id": "F", "resultado": "r"}, {"id": "G", "resultado": "g"}]
        for estado, base, esperado in (
            ("cerrado", "main", "recibido"),
            ("mergeado", "otra", "detenido"),
        ):
            with self.subTest(estado=estado):
                registro, gh = self._registro(fases=fases)
                registro.anotar("x", {"kind": "entrega_externa", "data": {
                    "fase": "F", "paso": "pr_con_pendientes", "numero": 7,
                    "sha": "h", "hora": "t1",
                }}, "nivel:x")
                registro.anotar("x", {"kind": "comprobacion", "data": {
                    "fase": "F", "clase": "cierre_comprobado",
                    "evidencia": "entrega con pendientes", "hora": "t1",
                }}, "cierre:x")
                self.assertEqual(registro.fold("x").fase_activa, "G")
                enc, _ = self._observar(registro, gh, estado, base, "F")
                self.assertEqual(enc.estado, esperado)
                self.assertEqual(enc.canceladas, ("F",) if estado == "cerrado" else ())
                self.assertEqual(enc.fase_activa, "G")

    def test_conciliacion_no_activa_acepta_el_merge_de_david(self) -> None:
        registro, gh = self._registro(fases=[
            {"id": "F", "resultado": "r"}, {"id": "G", "resultado": "g"},
        ])
        registro.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "F", "paso": "pr_con_pendientes",
            "numero": 7, "sha": "h", "hora": "t1",
        }}, "nivel:x")
        registro.anotar("x", {"kind": "comprobacion", "data": {
            "fase": "F", "clase": "cierre_comprobado",
            "evidencia": "fase lista", "hora": "t1",
        }}, "cierre:x")
        gh.responde_ver = lambda _, numero: PrView(
            numero, "encargo/x-F", "main", False, "h", "mergeado", "m",
        )
        enc = ciclo._conciliar_fases(registro, registro.fold("x"), gh, "t2")
        self.assertEqual(enc.fases[0].integrado_sha, "m")
        self.assertEqual(enc.fases[0].estado, "preparada")
        self.assertEqual(enc.fase_activa, "G")
        self.assertEqual(enc.prs[0].pr.estado, "mergeado")

    def test_barrido_prioriza_el_merge_aunque_no_haya_revision(self) -> None:
        registro, gh = self._registro()
        gh.responde_ver = lambda _, numero: PrView(
            numero, "encargo/x", "main", False, "h", "mergeado", "m",
        )
        ciclo.barrer(registro, "2026-11-16T12:00:00+00:00", 0, None, gh=gh)
        enc = registro.fold("x")
        self.assertEqual((enc.estado, enc.nivel_alcanzado),
                         ("terminado", "integrado"))
        self.assertIsNone(enc.revision_vigente)

    def test_terminado_de_nivel_pr_se_sondea_solo_con_interruptor(self) -> None:
        registro, gh = self._registro()
        registro.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "F", "paso": "pr_con_pendientes",
            "numero": 7, "sha": "h", "hora": "t1",
        }}, "nivel:x")
        registro.anotar("x", {"kind": "comprobacion", "data": {
            "fase": "F", "clase": "cierre_comprobado",
            "evidencia": "entregado para el merge", "hora": "t1",
        }}, "cierre:x")
        enc = registro.fold("x")
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(fase_final_por_sondear(enc), "F")
        gh.responde_ver = lambda _, numero: PrView(
            numero, "encargo/x", "main", False, "h", "mergeado", "m",
        )
        with mock.patch.object(politica, "ENTREGA_V4", False):
            ciclo.barrer(registro, "2026-11-16T12:00:00+00:00", 0, None, gh=gh)
        self.assertFalse(registro.fold("x").prs)
        with mock.patch.object(politica, "ENTREGA_V4", True):
            ciclo.barrer(registro, "2026-11-16T12:00:01+00:00", 0, None, gh=gh)
        self.assertEqual(registro.fold("x").prs[0].pr.estado, "mergeado")
        self.assertEqual(fase_final_por_sondear(registro.fold("x")), "")

    def test_pr_apilado_reapuntado_a_main_no_cambia_a_base_borrada(self) -> None:
        fases = [
            {"id": "F", "resultado": "r"},
            {"id": "G", "resultado": "g",
             "requiere": [{"fase": "F", "version": "v"}]},
        ]
        registro, gh = self._registro(fases=fases)
        registro.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "F", "paso": "pr_con_pendientes", "numero": 7,
            "sha": "h", "hora": "t1",
        }}, "nivel:x")
        registro.anotar("x", {"kind": "comprobacion", "data": {
            "fase": "F", "clase": "cierre_comprobado",
            "evidencia": "fase lista", "hora": "t1",
        }}, "cierre:x")
        registro.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "G", "paso": "pr_listo", "numero": 8,
            "sha": "g", "hora": "t2",
        }}, "pr:g")
        enc = registro.fold("x")
        self.assertEqual(enc.fase_activa, "G")
        gh.rama_existe = lambda _repo, _rama: False
        self.assertEqual(base_objetivo_del_pr(enc, gh), "main")
        gh.responde_ver = lambda _, numero: PrView(
            numero, "encargo/x-G", "main", False, "g", "abierto", "",
        )
        gh.cambiar_base = lambda *_: self.fail("no se reapunta a la rama borrada")
        preparar_pr(gh, enc, base_objetivo_del_pr(enc, gh))
        enc, consumido = conducir_pr(registro, enc, gh, "t3")
        self.assertFalse(consumido)
        self.assertEqual(enc.prs[-1].pr.estado, "abierto")
        self.assertEqual(enc.otra_base_de, ("", ""))


if __name__ == "__main__":
    unittest.main()
