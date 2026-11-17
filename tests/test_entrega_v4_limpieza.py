"""La limpieza solo acredita borrados comprobados y conserva lo que se usa."""

from __future__ import annotations

import tempfile
import unittest
import json
import hashlib
import io
import os
import subprocess
import sys
import contextlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo, politica
from motor.encargos import despliegue
from motor.encargos.registro import Evento, Registro
from motor.github import FakeGitHub, PrView
from motor.workspace import git
from tests.test_github_contrato import _bare_con_rama


HORA = datetime(2026, 11, 16, tzinfo=timezone.utc)


def hora(minutos: int = 0) -> str:
    return (HORA + timedelta(minutes=minutos)).isoformat()


class LimpiezaPorHechos(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.reg = Registro(self.base / "base")
        self.addCleanup(self.reg.close)
        self.repo, _sha, self.trabajo = _bare_con_rama(self.base)
        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        self.gh = FakeGitHub(self.base / "fake")

    def _final(self, con_pr: bool = False, cancelar: bool = True,
               nivel: str = "", receta: bool = False) -> int:
        numero = 0
        if con_pr:
            numero = self.gh.abrir_pr(self.repo, "encargo/x", "main",
                                       "fase", "cuerpo").numero
        self.reg.append_many("x", (
            Evento("entrega", {
                "id": "x", "clave": "k", "huella": "h", "texto": "pedido",
                "entrega_nivel": nivel or ("pr" if con_pr else "terminado"),
                "repo": self.repo, "rama": "encargo/x", "base": "main",
                "hora": hora(),
            }, "entrega:x"),
            Evento("fases", {"fases": [{
                "id": "F", "resultado": "r",
                **({"despliegue": {
                    "desplegar": "deploy.sh", "checklist": "check.sh",
                    "rollback": "rollback.sh",
                }} if receta else {}),
            }],
                             "hora": hora()}, "fases:x"),
        ))
        if con_pr:
            self.reg.anotar("x", {"kind": "entrega_externa", "data": {
                "fase": "F", "paso": "pr_listo", "numero": numero,
                "sha": "h", "hora": hora(),
            }}, "pr:x")
        if cancelar:
            self.reg.anotar("x", {"kind": "orden", "data": {
                "accion": "cancelar", "hora": hora(),
            }}, "cancelar:x")
            self.reg.anotar("x", {"kind": "arbol", "data": {
                "observacion": "ausente", "hora": hora(),
            }}, "arbol:x")
            self.assertEqual(self.reg.fold("x").estado, "cancelado")
        return numero

    def _limpiar(self, minutos: int = 0, presupuesto: list[int] | None = None,
                enc=None):
        from motor.encargos.entrega.limpieza import barrer_limpieza

        return barrer_limpieza(self.reg, enc or self.reg.fold("x"), self.gh,
                              hora(minutos), presupuesto or [20])

    def test_interruptor_apagado_deja_rama_y_espacio_sin_hechos_nuevos(self) -> None:  # no-roja-en-base: la conducta anterior debe conservarse con el interruptor apagado
        self._final()
        espacio = self.reg.base / "espacio" / "x"
        espacio.mkdir(parents=True)
        with mock.patch.object(politica, "ENTREGA_V4", False):
            ciclo.barrer(self.reg, hora(60 * 24 * 8), 0, None, gh=self.gh)
        self.assertTrue(espacio.exists())
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertFalse([e for e in self.reg.eventos("x")
                          if e["kind"] in ("limpieza", "limpio")])

    def test_pr_cancelado_y_rama_se_borran_y_se_observan(self) -> None:
        numero = self._final(con_pr=True)
        enc = self._limpiar()
        self.assertEqual(self.gh.ver(self.repo, numero).estado, "cerrado")
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertEqual(enc.prs[0].pr.estado, "cerrado")
        self.assertEqual({recurso: resultado for recurso, _, resultado, _, _
                          in enc.limpieza}["pr"], "borrado")
        self.assertTrue(enc.limpio)

    def test_terminado_sin_pr_registrado_borra_rama(self) -> None:
        self._final(cancelar=False)
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "fase terminada", "hora": hora(),
        }}, "cierre-sin-pr:x")
        enc = self._limpiar()
        self.assertTrue(enc.limpio)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_terminado_sin_pr_con_head_abierto_conserva_rama(self) -> None:  # no-roja-en-base: antes conservaba toda rama sin PR propio, incluido el head abierto
        self._final(cancelar=False)
        self.gh.abrir_pr(self.repo, "encargo/x", "main", "ajeno", "cuerpo")
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "fase terminada", "hora": hora(),
        }}, "cierre-con-head:x")
        enc = self._limpiar()
        self.assertTrue(enc.limpio)
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertNotIn("rama", {tipo for tipo, _, _, _, _ in enc.limpieza})

    def test_terminado_sin_pr_con_base_abierta_conserva_rama(self) -> None:  # no-roja-en-base: ya se conservaba la rama base de un PR abierto
        self._final(cancelar=False)
        self.gh.abrir_pr(self.repo, "feat", "encargo/x", "apilado", "cuerpo")
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "fase terminada", "hora": hora(),
        }}, "cierre-con-base:x")
        enc = self._limpiar()
        self.assertTrue(enc.limpio)
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_cerrado_limpio_no_repliega_historia_en_otra_pasada(self) -> None:
        import motor.encargos.entrega.limpieza as limpieza

        self._final(con_pr=True)
        self.assertTrue(self._limpiar().limpio)
        self._limpiar(1)
        eventos = self.reg.eventos("x")
        with mock.patch.object(limpieza, "reducir", wraps=limpieza.reducir) as reducir:
            ciclo.barrer(self.reg, hora(2), 120, None, gh=self.gh)
        self.assertEqual(reducir.call_count, 0)
        self.assertEqual(self.reg.eventos("x"), eventos)

    def _probar_pr_reemplazado(self, apilado: bool) -> None:
        import motor.encargos.entrega.limpieza as limpieza

        antiguo = self._final(con_pr=True, cancelar=False)
        self.gh.cerrar_pr(self.repo, antiguo)
        self._limpiar()
        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        nuevo = self.gh.abrir_pr(self.repo, "encargo/x", "main", "nuevo", "cuerpo").numero
        self.gh.cerrar_pr(self.repo, nuevo)
        self.reg.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "F", "paso": "pr_listo", "numero": nuevo,
            "sha": "nuevo", "hora": hora(1),
        }}, "pr-reemplazado:x")
        if apilado:
            self.gh.abrir_pr(self.repo, "feat", "encargo/x", "apilado", "cuerpo")
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "fase cerrada", "hora": hora(2),
        }}, "cierre-reemplazado:x")
        enc = self._limpiar(2)
        self.assertEqual(enc.fases[0].pr_numero, nuevo)
        self.assertTrue(enc.limpio)
        self.assertEqual({clave for tipo, clave, resultado, _, _ in enc.limpieza
                          if tipo == "pr" and resultado == "borrado"},
                         {str(antiguo), str(nuevo)})
        self.assertEqual(self.gh.rama_existe(self.repo, "encargo/x"), apilado)

        self._limpiar(3)
        eventos = self.reg.eventos("x")
        with (mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver,
              mock.patch.object(self.gh, "rama_existe", wraps=self.gh.rama_existe) as rama,
              mock.patch.object(self.gh, "prs_con_base", wraps=self.gh.prs_con_base) as prs,
              mock.patch.object(limpieza, "reducir", wraps=limpieza.reducir) as reducir,
              mock.patch.object(limpieza, "candidatos", wraps=limpieza.candidatos) as candidatos):
            enc = self._limpiar(4)
        self.assertTrue(enc.limpio)
        self.assertEqual((ver.call_count, rama.call_count, prs.call_count), (0, 0, 0))
        self.assertEqual(reducir.call_count, 0)
        candidatos.assert_not_called()
        self.assertEqual(self.reg.eventos("x"), eventos)

    def test_pr_reemplazado_no_bloquea_limpio(self) -> None:
        self._probar_pr_reemplazado(apilado=False)

    def test_pr_reemplazado_con_apilado_no_gasta_cuota(self) -> None:
        self._probar_pr_reemplazado(apilado=True)

    def test_una_rama_compartida_se_conserva_aunque_el_pr_propio_cierre(self) -> None:
        numero = self._final(con_pr=True)
        self.gh.abrir_pr(self.repo, "feat", "encargo/x", "apilado", "cuerpo")
        enc = self._limpiar()
        self.assertEqual(self.gh.ver(self.repo, numero).estado, "cerrado")
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertNotIn("rama", {r for r, _, _, _, _ in enc.limpieza})
        self.assertTrue(enc.limpio)

    def test_rama_compartida_se_borra_cuando_termina_su_dependiente(self) -> None:
        self._final(con_pr=True)
        dependiente = self.gh.abrir_pr(
            self.repo, "feat", "encargo/x", "apilado", "cuerpo",
        ).numero
        self.assertTrue(self._limpiar().limpio)
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))
        self.gh.cerrar_pr(self.repo, dependiente)
        enc = self._limpiar(10)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertTrue(enc.limpio)

    def test_rama_compartida_no_consulta_github_cada_segundo(self) -> None:
        self._final(con_pr=True)
        dependiente = self.gh.abrir_pr(
            self.repo, "feat", "encargo/x", "apilado", "cuerpo",
        ).numero
        self.assertTrue(self._limpiar().limpio)
        with (mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver,
              mock.patch.object(self.gh, "rama_existe",
                                wraps=self.gh.rama_existe) as rama):
            for minuto in range(1, 10):
                self._limpiar(minuto)
            self.assertEqual((ver.call_count, rama.call_count), (0, 0))
            self.gh.cerrar_pr(self.repo, dependiente)
            self._limpiar(10)
            self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_no_borra_rama_activa_que_aun_no_tiene_pr(self) -> None:
        self._final(cancelar=False)
        enc = self._limpiar()
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertNotIn("rama", {r for r, _, _, _, _ in enc.limpieza})

    def test_no_acredita_rama_ausente_antes_del_push_y_la_borra_al_cancelar(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        self._final(cancelar=False)
        git("push", "-q", self.repo, ":refs/heads/encargo/x", cwd=self.trabajo)
        enc = barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(),
                              PresupuestoLimpieza(self.reg, 0), 0)
        self.assertNotIn("rama", {r for r, _, _, _, _ in enc.limpieza})

        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        numero = self.gh.abrir_pr(self.repo, "encargo/x", "main", "fase", "cuerpo").numero
        self.reg.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "F", "paso": "pr_listo", "numero": numero,
            "sha": "h", "hora": hora(),
        }}, "pr:x")
        self.reg.anotar("x", {"kind": "orden", "data": {
            "accion": "cancelar", "hora": hora(),
        }}, "cancelar:x")
        self.reg.anotar("x", {"kind": "arbol", "data": {
            "observacion": "ausente", "hora": hora(),
        }}, "arbol:x")
        enc = barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(1),
                              PresupuestoLimpieza(self.reg, 61), 61)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertTrue(enc.limpio)

    def test_rama_borrada_en_entrega_parcial_se_recomprueba_tras_el_cierre(self) -> None:  # no-roja-en-base: la base ya reconsultaba ramas borradas
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        numero = self._final(con_pr=True, cancelar=False)
        self.gh.cerrar_pr(self.repo, numero)
        barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(),
                         PresupuestoLimpieza(self.reg, 0), 0)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "entrega parcial", "hora": hora(),
        }}, "cierre-parcial:x")
        with mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver:
            enc = barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(),
                                  PresupuestoLimpieza(self.reg, 61), 61)
        self.assertEqual(ver.call_count, 1)
        self.assertTrue(enc.limpio)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))
        self.assertEqual(sum(
            evento["kind"] == "limpieza" and evento["data"]["recurso"] == "rama"
            and evento["data"]["resultado"] == "borrado"
            for evento in self.reg.eventos("x")
        ), 2)
        eventos = self.reg.eventos("x")
        with (mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver,
              mock.patch.object(self.gh, "rama_existe", wraps=self.gh.rama_existe) as rama):
            self._limpiar(1)
        self.assertEqual((ver.call_count, rama.call_count), (0, 0))
        self.assertEqual(self.reg.eventos("x"), eventos)

    def test_rama_borrada_antes_de_reabrir_se_borra_tras_cancelar(self) -> None:  # no-roja-en-base: la base ya reconsultaba ramas borradas
        numero = self._final(con_pr=True, cancelar=False)
        self.gh.cerrar_pr(self.repo, numero)
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "cierre provisional", "hora": hora(1),
        }}, "cierre-provisional:x")
        self.assertTrue(self._limpiar(1).limpio)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

        salida = ciclo.ordenar(self.reg, "x", "reabrir", "reabrir:x", hora(2))
        self.assertFalse(salida.get("rechazada"), salida)
        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        salida = ciclo.ordenar(self.reg, "x", "cancelar", "cancelar-reabierto:x", hora(3))
        self.assertFalse(salida.get("rechazada"), salida)
        self.reg.anotar("x", {"kind": "arbol", "data": {
            "observacion": "ausente", "hora": hora(3),
        }}, "arbol-reabierto:x")
        self.assertEqual(self.reg.fold("x").estado, "cancelado")

        enc = self._limpiar(4)
        self.assertTrue(enc.limpio)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_rama_reaparecida_con_pr_dependiente_respeta_cadencia(self) -> None:
        numero = self._final(con_pr=True, cancelar=False)
        self.gh.cerrar_pr(self.repo, numero)
        self._limpiar()
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        dependiente = self.gh.abrir_pr(
            self.repo, "feat", "encargo/x", "apilado", "cuerpo",
        ).numero
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "entrega parcial", "hora": hora(1),
        }}, "cierre-dependiente:x")
        enc = self._limpiar(1)
        self.assertTrue(enc.limpio)
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))

        with (mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver,
              mock.patch.object(self.gh, "rama_existe", wraps=self.gh.rama_existe) as rama,
              mock.patch.object(self.gh, "prs_con_base", wraps=self.gh.prs_con_base) as prs):
            self._limpiar(2)
        self.assertEqual((ver.call_count, rama.call_count, prs.call_count), (0, 0, 0))

        self.gh.cerrar_pr(self.repo, dependiente)
        self._limpiar(11)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_pr_reabierto_tras_borrado_no_gasta_cuota_en_cada_pasada(self) -> None:
        numero = self._final(con_pr=True, cancelar=False)
        self.gh.cerrar_pr(self.repo, numero)
        self._limpiar()
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "entrega parcial", "hora": hora(1),
        }}, "cierre-pr-reabierto:x")
        visto = self.gh.ver(self.repo, numero)
        abierto = PrView(
            visto.numero, visto.rama, visto.base, visto.borrador,
            visto.head, "abierto", visto.merge_commit, visto.cuerpo,
        )
        with mock.patch.object(self.gh, "ver", return_value=abierto) as ver:
            enc = self._limpiar(1)
        self.assertEqual(ver.call_count, 1)
        self.assertFalse(enc.limpio)

        eventos = self.reg.eventos("x")
        with mock.patch.object(self.gh, "ver", side_effect=AssertionError("cadencia")) as ver:
            enc = self._limpiar(2)
        self.assertEqual(ver.call_count, 0)
        self.assertFalse(enc.limpio)
        self.assertEqual(self.reg.eventos("x"), eventos)
        with mock.patch.object(self.gh, "ver", return_value=abierto) as ver:
            self._limpiar(11)
        self.assertEqual(ver.call_count, 1)
        with mock.patch.object(self.gh, "ver", side_effect=AssertionError("vencido")) as ver:
            enc = self._limpiar(8 * 24 * 60)
        self.assertEqual(ver.call_count, 0)
        self.assertFalse(enc.limpio)

    def test_rama_reaparecida_pendiente_no_repite_consultas(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        def barrer(minutos: int):
            return barrer_limpieza(
                self.reg, self.reg.fold("x"), self.gh, hora(minutos),
                PresupuestoLimpieza(self.reg, minutos * 60), minutos * 60,
            )

        numero = self._final(con_pr=True, cancelar=False)
        self.gh.cerrar_pr(self.repo, numero)
        self._limpiar()
        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "entrega parcial", "hora": hora(1),
        }}, "cierre-rama-pendiente:x")
        with mock.patch.object(self.gh, "borrar_rama", return_value=None):
            enc = barrer(1)
        self.assertFalse(enc.limpio)
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))

        eventos = self.reg.eventos("x")
        with (mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver,
              mock.patch.object(self.gh, "rama_existe", wraps=self.gh.rama_existe) as rama,
              mock.patch.object(self.gh, "prs_con_base", wraps=self.gh.prs_con_base) as prs):
            barrer(2)
        self.assertEqual((ver.call_count, rama.call_count, prs.call_count), (0, 0, 0))
        self.assertEqual(self.reg.eventos("x"), eventos)
        enc = barrer(11)
        self.assertTrue(enc.limpio)
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_revalidacion_sin_sondeo_no_sale_de_ventana_de_siete_dias(self) -> None:
        numero = self._final(con_pr=True, cancelar=False)
        self.gh.cerrar_pr(self.repo, numero)
        self._limpiar()
        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x", cwd=self.trabajo)
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "entrega parcial", "hora": hora(1),
        }}, "cierre-fuera-de-ventana:x")
        with (mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver,
              mock.patch.object(self.gh, "rama_existe", wraps=self.gh.rama_existe) as rama,
              mock.patch.object(self.gh, "prs_con_base", wraps=self.gh.prs_con_base) as prs):
            enc = self._limpiar(8 * 24 * 60)
        self.assertEqual((ver.call_count, rama.call_count, prs.call_count), (0, 0, 0))
        self.assertFalse(enc.limpio)
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_un_pr_ya_cerrado_se_observa_sin_cerrarlo_de_nuevo(self) -> None:
        numero = self._final(con_pr=True)
        self.gh.cerrar_pr(self.repo, numero)
        with mock.patch.object(self.gh, "cerrar_pr",
                               side_effect=AssertionError("ya estaba cerrado")):
            enc = self._limpiar()
        self.assertEqual(enc.prs[0].pr.estado, "cerrado")
        self.assertTrue(enc.limpio)

    def test_presupuesto_limita_los_borrados_de_una_pasada(self) -> None:
        self._final()
        for tipo in ("espacio", "ejecucion", "runtime"):
            (self.reg.base / tipo / "x").mkdir(parents=True)
        presupuesto = [1]
        enc = self._limpiar(60 * 24 * 8, presupuesto)
        self.assertEqual(presupuesto[0], 0)
        self.assertFalse(enc.limpio)
        self.assertTrue((self.reg.base / "runtime" / "x").exists())
        enc = self._limpiar(60 * 24 * 8 + 1)
        self.assertTrue(enc.limpio)

    def test_limpieza_final_quita_actual_pero_no_ejecucion_antes_del_plazo(self) -> None:
        self._final()
        directorio = self.reg.base / "ejecucion" / "x"
        directorio.mkdir(parents=True)
        puntero = directorio / "actual"
        puntero.write_text("intento-1", encoding="utf-8")
        enc = self._limpiar()
        self.assertFalse(puntero.exists())
        self.assertTrue(directorio.exists())
        self.assertTrue(enc.limpio)

    def test_puntero_de_despliegue_incierto_no_se_descarta_ni_se_declara_limpio(self) -> None:
        self._final()
        puntero = self.reg.base / "despliegues" / "x.actual"
        puntero.parent.mkdir(parents=True)
        puntero.write_text("clave ilegible", encoding="utf-8")
        enc = self._limpiar()
        self.assertTrue(puntero.exists())
        self.assertFalse(enc.limpio)

    def _recibo(self, presente: bool, terminado: bool = False,
                reintegrado: bool = False, receta: bool = False) -> Path:
        numero = self._final(
            con_pr=True, cancelar=False, nivel="desplegado" if terminado else "pr",
            receta=receta,
        )
        fase = self.reg.fold("x").fases[0]
        identidad = {
            "encargo": "x", "fase": "F", "repo": self.repo, "sha": "m",
            "receta": {k: getattr(fase, k) for k in ("desplegar", "checklist", "rollback")},
        }
        clave = (hashlib.sha256(json.dumps(identidad, sort_keys=True).encode()).hexdigest()
                 if receta else "a" * 64)
        ruta = self.reg.base / "despliegues" / clave / "recibo.json"
        ruta.parent.mkdir(parents=True)
        (ruta.parent / "pedido.json").write_text(
            json.dumps({"identidad": {"encargo": "x"}}), encoding="utf-8",
        )
        (ruta.parent / "proceso.json").write_text(
            '{"boot_id":"anterior","pid":999999}', encoding="utf-8",
        )
        (ruta.parent / "comando.json").write_text(
            '{"estado":"no_enviado"}', encoding="utf-8",
        )
        (ruta.parent / "trabajador.log").write_text("resto", encoding="utf-8")
        puntero = ruta.parent.parent / "x.actual"
        puntero.write_text(ruta.parent.name, encoding="utf-8")
        if presente:
            ruta.write_text('{"encargo":"x","estado":"aplicado"}', encoding="utf-8")
        for paso, recibo in (("integrado", "m"), ("desplegado", str(ruta))):
            self.reg.anotar("x", {"kind": "entrega_externa", "data": {
                "fase": "F", "paso": paso, "numero": 1,
                "sha": "m", "recibo": recibo, "hora": hora(),
            }}, "entrega:" + paso)
        if reintegrado:
            self.reg.anotar("x", {"kind": "entrega_externa", "data": {
                "fase": "F", "paso": "integrado", "numero": numero,
                "recibo": "n", "hora": hora(),
            }}, "reintegrado:x")
        if terminado:
            self.reg.anotar("x", {"kind": "comprobacion", "data": {
                "fase": "F", "clase": "cierre_comprobado",
                "evidencia": "entregado", "hora": hora(),
            }}, "cierre:x")
            estado = self.gh._cargar()
            self.gh._pr(estado, self.repo, numero).update(
                estado="mergeado", merge_commit="m", head_merge="h",
            )
            self.gh._guardar(estado)
            self.assertEqual(self.reg.fold("x").estado, "terminado")
        else:
            self.reg.anotar("x", {"kind": "orden", "data": {
                "accion": "cancelar", "hora": hora(),
            }}, "cancelar:x")
            self.reg.anotar("x", {"kind": "arbol", "data": {
                "observacion": "ausente", "hora": hora(),
            }}, "arbol:x")
        return ruta

    def test_despliegue_borra_puntero_primero_y_conserva_recibo_y_pedido(self) -> None:
        ruta = self._recibo(presente=True)
        puntero = ruta.parent.parent / "x.actual"
        from motor.encargos.entrega import limpieza

        borrados = []
        real = limpieza.borrar_arbol

        def borrar(path):
            borrados.append(path)
            real(path)

        with mock.patch.object(limpieza, "borrar_arbol", side_effect=borrar):
            enc = self._limpiar()
        self.assertEqual(borrados[0], puntero)
        self.assertEqual({p.name for p in ruta.parent.iterdir()},
                         {"recibo.json", "pedido.json"})
        self.assertTrue(enc.limpio)
        self.assertNotEqual(despliegue.observacion(self.reg, enc), "incierto")
        self.assertNotEqual(despliegue.observacion(self.reg, enc, enc.fases[0]),
                            "incierto")

    def test_sin_recibo_no_es_limpio(self) -> None:
        ruta = self._recibo(presente=False)
        enc = self._limpiar()
        self.assertFalse(ruta.exists())
        self.assertFalse(enc.limpio)

    def test_observacion_por_fase_no_queda_incierta_tras_limpieza(self) -> None:
        ruta = self._recibo(presente=True, receta=True)
        enc = self.reg.fold("x")
        self.assertEqual(despliegue.observacion(self.reg, enc, enc.fases[0]), "ausente")
        enc = self._limpiar()
        self.assertFalse((ruta.parent / "proceso.json").exists())
        self.assertEqual(despliegue.observacion(self.reg, enc), "ausente")
        self.assertEqual(despliegue.observacion(self.reg, enc, enc.fases[0]), "ausente")
        (ruta.parent / "comando.json").write_text(
            '{"estado":"enviado"}', encoding="utf-8",
        )
        self.assertEqual(despliegue.observacion(self.reg, enc, enc.fases[0]), "incierto")

    def test_terminado_desplegado_con_recibo_si_queda_limpio(self) -> None:
        ruta = self._recibo(presente=True, terminado=True)
        enc = self._limpiar()
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(enc.entrega.nivel, "desplegado")
        self.assertTrue(ruta.exists())
        self.assertTrue(enc.limpio)

    def test_reporte_de_registro_viejo_no_infiere_limpieza_del_recibo(self) -> None:
        from motor.encargos.reporte import reporte

        ruta = self._recibo(presente=True, terminado=True, receta=True)
        anterior = self.reg.fold("x")
        self.assertEqual(anterior.prs, (), "no hay evento pr_observado")
        self.assertTrue(ruta.exists())
        self.assertEqual(reporte(anterior, False).palabra, "falta limpiar")
        limpio = self._limpiar()
        self.assertTrue(limpio.limpio)
        self.assertEqual(reporte(limpio, True).palabra, "entregado")

    def test_un_barrido_completo_no_recrea_carpetas_del_terminado(self) -> None:
        self._recibo(presente=True, terminado=True)
        for nombre in ("espacio", "ejecucion"):
            (self.reg.base / nombre / "x").mkdir(parents=True)
        self._limpiar(60 * 24 * 8)
        with mock.patch.object(politica, "ENTREGA_V4", True):
            ciclo.barrer(
                self.reg, hora(60 * 24 * 8 + 1),
                (HORA + timedelta(days=8, minutes=1)).timestamp(),
                None, gh=self.gh,
            )
        for nombre in ("espacio", "ejecucion"):
            self.assertFalse((self.reg.base / nombre / "x").exists())

    def test_un_segundo_borrado_del_runtime_vuelve_a_dejar_hecho(self) -> None:
        self._final()
        runtime = self.reg.base / "runtime" / "x"
        runtime.mkdir(parents=True)
        self._limpiar()
        runtime.mkdir(parents=True)
        self._limpiar(1)
        self.assertEqual(len([
            ev for ev in self.reg.eventos("x") if ev["kind"] == "limpieza"
            and ev["data"]["recurso"] == "runtime"
        ]), 2)

    def test_runtime_de_solo_lectura_se_borra_con_v4(self) -> None:  # no-roja-en-base: verifica la ruta nueva con el mismo borde de permisos del cierre legado
        from tests.test_limpieza_cierre import unlink_de_windows

        self._final()
        runtime = self.reg.base / "runtime" / "x"
        runtime.mkdir(parents=True)
        archivo = runtime / "solo-lectura"
        archivo.write_text("dato", encoding="utf-8")
        archivo.chmod(0o444)
        with unlink_de_windows():
            enc = self._limpiar()
        self.assertFalse(runtime.exists())
        self.assertTrue(enc.limpio)

    def test_reintegrar_no_invalida_la_limpieza_del_recibo_viejo(self) -> None:
        ruta = self._recibo(presente=True, reintegrado=True)
        enc = self._limpiar()
        self.assertEqual(enc.fases[0].integrado_sha, "n")
        self.assertEqual(enc.fases[0].desplegado_recibo, str(ruta))
        self.assertTrue(enc.limpio)

    def test_reintegrado_con_receta_sin_directorio_nuevo_limpia_el_viejo(self) -> None:
        ruta = self._recibo(presente=True, reintegrado=True, receta=True)
        self.reg.anotar("x", {"kind": "orden", "data": {
            "accion": "cancelar", "hora": hora(),
        }}, "cancelar:x")
        self.reg.anotar("x", {"kind": "arbol", "data": {
            "observacion": "ausente", "hora": hora(),
        }}, "arbol:x")
        enc = self._limpiar()
        self.assertTrue(ruta.is_file())
        self.assertFalse((ruta.parent / "trabajador.log").exists())
        self.assertTrue(enc.limpio)

    def test_el_presupuesto_partido_no_deja_despliegue_atascado(self) -> None:
        ruta = self._recibo(presente=True, receta=True, terminado=True)
        presupuesto = [4]
        enc = self._limpiar(presupuesto=presupuesto)
        self.assertEqual(presupuesto[0], 0)
        self.assertFalse(enc.limpio)
        self.assertTrue((ruta.parent / "proceso.json").exists())
        enc = self._limpiar(1)
        self.assertFalse((ruta.parent / "trabajador.log").exists())
        self.assertTrue(ruta.is_file())
        self.assertTrue(enc.limpio)

    def test_sin_proceso_ni_envio_se_verifica_y_termina_limpieza(self) -> None:
        ruta = self._recibo(presente=True, receta=True, terminado=True)
        (ruta.parent / "proceso.json").unlink()
        enc = self._limpiar()
        self.assertTrue(ruta.is_file())
        self.assertFalse((ruta.parent / "trabajador.log").exists())
        self.assertTrue(enc.limpio)

    def test_sin_proceso_con_grupo_ausente_se_verifica_limpieza(self) -> None:
        ruta = self._recibo(presente=True, receta=True, terminado=True)
        (ruta.parent / "proceso.json").unlink()
        (ruta.parent / "comando.json").write_text(json.dumps({
            "estado": "enviado", "pid": 7311, "inicio": "t0",
            "boot_id": plataforma.boot_id(),
        }), encoding="utf-8")
        with mock.patch.object(plataforma, "presencia_grupo", return_value="ausente"):
            enc = self._limpiar()
        self.assertTrue(ruta.is_file())
        self.assertTrue(enc.limpio)

    def _presupuesto_partido_sin_proceso(self, enviado: bool) -> None:
        from motor.encargos.entrega.limpieza import _despliegue

        ruta = self._recibo(presente=True, receta=True, terminado=True)
        (ruta.parent / "proceso.json").unlink()
        (ruta.parent.parent / "x.actual").unlink()
        if enviado:
            (ruta.parent / "comando.json").write_text(json.dumps({
                "estado": "enviado", "pid": 7311, "inicio": "t0",
                "boot_id": plataforma.boot_id(),
            }), encoding="utf-8")
        with mock.patch.object(plataforma, "presencia_grupo", return_value="ausente"):
            self.assertFalse(_despliegue(self.reg, self.reg.fold("x"), ruta.parent, [1]))
            self.assertTrue((ruta.parent / "comando.json").exists())
            self.assertFalse((ruta.parent / "trabajador.log").exists())
            enc = self._limpiar(1)
        self.assertTrue(enc.limpio)
        self.assertTrue(ruta.is_file())
        self.assertFalse([
            resultado for tipo, _, resultado, _, _ in enc.limpieza
            if tipo == "despliegue" and resultado in ("fallo", "agotado")
        ])

    def test_presupuesto_partido_sin_proceso_ni_envio(self) -> None:
        self._presupuesto_partido_sin_proceso(False)

    def test_presupuesto_partido_sin_proceso_con_grupo_ausente(self) -> None:
        self._presupuesto_partido_sin_proceso(True)

    def test_un_paso_ausente_no_prueba_que_murio_el_trabajador(self) -> None:
        ruta = self._recibo(presente=True, receta=True, terminado=True)
        ruta.write_text('{"encargo":"x","estado":"ejecutando"}', encoding="utf-8")
        (ruta.parent / "proceso.json").unlink()
        (ruta.parent / "comando.json").write_text(json.dumps({
            "estado": "enviado", "pid": 7311, "inicio": "t0",
            "boot_id": plataforma.boot_id(),
        }), encoding="utf-8")
        with mock.patch.object(plataforma, "presencia_grupo", return_value="ausente"):
            enc = self._limpiar()
        self.assertTrue((ruta.parent / "trabajador.log").exists())
        self.assertFalse(enc.limpio)

    def test_sin_proceso_y_recibo_esperando_se_verifica_limpieza(self) -> None:
        ruta = self._recibo(presente=True, receta=True, terminado=True)
        ruta.write_text('{"encargo":"x","estado":"esperando"}', encoding="utf-8")
        (ruta.parent / "proceso.json").unlink()
        enc = self._limpiar()
        self.assertTrue(ruta.is_file())
        self.assertTrue(enc.limpio)

    def test_sin_proceso_y_recibo_ocupado_se_verifica_limpieza(self) -> None:
        ruta = self._recibo(presente=True, receta=True, terminado=True)
        ruta.write_text('{"encargo":"x","estado":"ocupado"}', encoding="utf-8")
        (ruta.parent / "proceso.json").unlink()
        (ruta.parent / "comando.json").write_text(json.dumps({
            "estado": "enviado", "pid": 7311, "inicio": "t0",
            "boot_id": plataforma.boot_id(),
        }), encoding="utf-8")
        with mock.patch.object(plataforma, "presencia_grupo", return_value="ausente"):
            enc = self._limpiar()
        self.assertTrue(ruta.is_file())
        self.assertTrue(enc.limpio)

    def test_sin_metadatos_verificables_agota_sin_acreditar_limpio(self) -> None:
        ruta = self._recibo(presente=True, receta=True, terminado=True)
        (ruta.parent / "proceso.json").unlink()
        (ruta.parent / "comando.json").unlink()
        enc = self._limpiar()
        self.assertEqual(next(
            (resultado, intento) for tipo, clave, resultado, intento, _
            in enc.limpieza if tipo == "despliegue" and clave == ruta.parent.name
        ), ("fallo", 1))
        self.assertTrue((ruta.parent / "trabajador.log").exists())
        self.assertFalse(enc.limpio)
        self._limpiar(10)
        self.assertEqual(next(
            (resultado, intento) for tipo, clave, resultado, intento, _
            in self._limpiar(20).limpieza
            if tipo == "despliegue" and clave == ruta.parent.name
        ), ("agotado", 3))

    def test_limpio_no_sigue_consultando_github_ni_el_directorio_global(self) -> None:
        self._final(con_pr=True)
        enc = self._limpiar()
        self.assertTrue(enc.limpio)
        with (mock.patch.object(self.gh, "ver", side_effect=AssertionError("PR leído de nuevo")),
              mock.patch.object(self.gh, "prs_con_base", side_effect=AssertionError("bases leídas")),
              mock.patch("motor.encargos.entrega.limpieza._despliegues",
                         side_effect=AssertionError("escaneo global"))):
            self._limpiar(1)

    def test_indice_de_despliegues_se_calcula_una_vez_por_pasada(self) -> None:
        from motor.encargos.entrega import limpieza

        self._final(con_pr=True)
        presupuesto = limpieza.PresupuestoLimpieza(self.reg)
        with mock.patch.object(limpieza, "_indice_despliegues",
                               wraps=limpieza._indice_despliegues) as buscar:
            enc = self.reg.fold("x")
            for _ in range(2):
                limpieza.barrer_limpieza(self.reg, enc, None, hora(), presupuesto)
        self.assertEqual(buscar.call_count, 1)

    def test_parada_local_no_cierra_pr_ni_escribe_limpieza(self) -> None:  # no-roja-en-base: antes de 4.3 no existía el barrido nuevo que rompía la parada
        from motor.encargos.frontera import activar_parada_local

        numero = self._final(con_pr=True)
        activar_parada_local(self.reg.base, "pausa", hora())
        antes = self.reg.eventos("x")
        with mock.patch.object(politica, "ENTREGA_V4", True):
            ciclo.barrer(self.reg, hora(1), (HORA + timedelta(minutes=1)).timestamp(),
                         None, gh=self.gh)
        self.assertEqual(self.gh.ver(self.repo, numero).estado, "abierto")
        self.assertEqual(self.reg.eventos("x"), antes)

    def test_parada_local_cierra_proceso_sin_tocar_pr_ni_registro(self) -> None:  # no-roja-en-base: la limpieza legada cerraba procesos, la ruta v4 omitía ese cierre
        from motor.encargos.frontera import activar_parada_local

        numero = self._final(con_pr=True)
        intento = self.reg.base / "ejecucion" / "x" / "intento-1"
        intento.mkdir(parents=True)
        proceso = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            **plataforma.kwargs_grupo(),
        )
        self.addCleanup(proceso.wait)
        self.addCleanup(lambda: proceso.poll() is None and proceso.kill())
        (intento / "proc.json").write_text(json.dumps({
            "pid": proceso.pid, "inicio": plataforma.hora_inicio(proceso.pid),
            "boot_id": plataforma.boot_id(),
        }), encoding="utf-8")
        activar_parada_local(self.reg.base, "pausa", hora())
        antes = self.reg.eventos("x")
        with mock.patch.object(politica, "ENTREGA_V4", True):
            ciclo.barrer(self.reg, hora(1), (HORA + timedelta(minutes=1)).timestamp(),
                         None, gh=self.gh)
        proceso.wait(timeout=10)
        self.assertEqual(self.gh.ver(self.repo, numero).estado, "abierto")
        self.assertEqual(self.reg.eventos("x"), antes)

    def test_tope_de_borrados_sigue_la_politica_del_turno(self) -> None:
        self._final()
        runtime = self.reg.base / "runtime" / "x"
        runtime.mkdir(parents=True)
        with (mock.patch.object(politica, "ENTREGA_V4", True),
              mock.patch.object(politica, "LIMPIEZA",
                                replace(politica.LIMPIEZA, por_pasada=0))):
            ciclo.barrer(self.reg, hora(1), (HORA + timedelta(minutes=1)).timestamp(),
                         None, gh=self.gh)
        self.assertTrue(runtime.exists())

    def test_no_limpia_trabajador_vivo_de_un_sha_anterior(self) -> None:
        ruta = self._recibo(presente=True, reintegrado=True)
        (ruta.parent.parent / "x.actual").unlink()
        (ruta.parent / "proceso.json").write_text(
            json.dumps({"boot_id": plataforma.boot_id(), "pid": os.getpid()}),
            encoding="utf-8",
        )
        enc = self._limpiar()
        self.assertTrue((ruta.parent / "trabajador.log").exists())
        self.assertFalse(enc.limpio)

    def test_una_fase_cancelada_no_bloquea_limpio(self) -> None:
        git("push", "-q", self.repo, "HEAD:refs/heads/encargo/x-F",
            cwd=self.trabajo)
        numero = self.gh.abrir_pr(
            self.repo, "encargo/x-F", "main", "fase", "cuerpo",
        ).numero
        self.reg.append_many("x", (
            Evento("entrega", {
                "id": "x", "clave": "k", "huella": "h", "texto": "pedido",
                "entrega_nivel": "pr", "repo": self.repo, "rama": "encargo/x",
                "base": "main", "hora": hora(),
            }, "entrega:x"),
            Evento("fases", {"fases": [
                {"id": "F", "resultado": "r"},
                {"id": "G", "resultado": "g"},
            ], "hora": hora()}, "fases:x"),
            Evento("entrega_externa", {
                "fase": "F", "paso": "pr_listo", "numero": numero,
                "sha": "h", "hora": hora(),
            }, "pr:x"),
        ))
        self.gh.cerrar_pr(self.repo, numero)
        self.reg.anotar("x", {"kind": "fase_cancelada", "data": {
            "fase": "F", "numero": numero, "causa": "pr_cerrado",
            "origen": "", "hora": hora(),
        }}, "cancelada:f")
        enc = self._limpiar()
        self.assertEqual(enc.canceladas, ("F",))
        self.assertFalse(self.gh.rama_existe(self.repo, "encargo/x-F"))
        self.assertTrue(enc.limpio)

    def test_credenciales_del_turno_incorporado_se_borran_sin_perder_sesion(self) -> None:
        self._final(cancelar=False)
        runtime = self.reg.base / "runtime" / "x"
        sesion = runtime / "sesion-responsable"
        sesion.mkdir(parents=True)
        (sesion / "historia").write_text("continúa", encoding="utf-8")
        for generacion in (1, 2):
            token = f"token{generacion}"
            intento = (self.reg.base / "ejecucion" / "x"
                       / f"intento-{generacion}-{token}")
            intento.mkdir(parents=True)
            (intento / "arranque.json").write_text(
                json.dumps({"token": token}), encoding="utf-8",
            )
            (intento / "outcome.json").write_text("{}", encoding="utf-8")
            credencial = runtime / token
            credencial.mkdir(parents=True)
            (credencial / "llave").write_text("efímera", encoding="utf-8")
            enc = replace(self.reg.fold("x"), incorporado=tuple(range(1, generacion + 1)))
            self._limpiar(generacion, enc=enc)
            self.assertFalse(credencial.exists())
            self.assertEqual((sesion / "historia").read_text(encoding="utf-8"),
                             "continúa")

    def test_credencial_se_conserva_si_la_copia_de_sesion_quedo_incompleta(self) -> None:
        self._final(cancelar=False)
        intento = self.reg.base / "ejecucion" / "x" / "intento-1-token1"
        intento.mkdir(parents=True)
        (intento / "arranque.json").write_text(
            '{"token":"token1","rol":"responsable"}', encoding="utf-8",
        )
        (intento / "outcome.json").write_text("{}", encoding="utf-8")
        runtime = self.reg.base / "runtime" / "x"
        credencial = runtime / "token1"
        credencial.mkdir(parents=True)
        sesion = runtime / "sesion-responsable"
        sesion.mkdir()
        incompleta = sesion / ".copia-incompleta"
        incompleta.touch()
        enc = replace(self.reg.fold("x"), incorporado=(1,))
        self._limpiar(enc=enc)
        self.assertTrue(credencial.exists())
        incompleta.unlink()
        self._limpiar(1, enc=enc)
        self.assertFalse(credencial.exists())

    def test_herramienta_solo_cuenta_y_no_escribe_ni_revela_ids(self) -> None:
        self._final()
        registro = self.reg.base / "registro.sqlite"
        antes = (registro.stat().st_size, registro.stat().st_mtime_ns)
        salida = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] /
                                 "tools" / "limpieza_pendiente.py"), str(self.reg.base)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONPATH": ""},
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("Cota superior:", salida)
        self.assertIn("rama:", salida)
        self.assertNotIn(str(self.repo), salida)
        self.assertNotIn(str(self.reg.base), salida)
        self.assertEqual((registro.stat().st_size, registro.stat().st_mtime_ns), antes)

    def test_herramienta_oculta_rutas_incluso_si_el_registro_esta_roto(self) -> None:
        base = self.base / "base-privada"
        base.mkdir()
        (base / "registro.sqlite").write_bytes(b"no soy sqlite")
        resultado = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] /
                                 "tools" / "limpieza_pendiente.py"), str(base)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONPATH": ""},
            capture_output=True, text=True,
        )
        self.assertEqual(resultado.returncode, 2)
        self.assertIn("no se pudo leer el registro", resultado.stderr)
        self.assertNotIn(str(base), resultado.stdout + resultado.stderr)

    def test_tres_fallos_agotan_y_no_reinician_al_reabrir_registro(self) -> None:
        self._final()
        runtime = self.reg.base / "runtime" / "x"
        runtime.mkdir(parents=True)
        (runtime / "token").write_text("prueba", encoding="utf-8")
        with mock.patch("motor.encargos.entrega.limpieza.borrar_arbol",
                        side_effect=PermissionError("token=secreto /Users/alguien")) as borrar:
            self._limpiar()
            self._limpiar(9)
            self.assertEqual(borrar.call_count, 1)
            self._limpiar(10)
            self.reg.close()
            otra = Registro(self.reg.base)
            self.addCleanup(otra.close)
            from motor.encargos.entrega.limpieza import barrer_limpieza

            barrer_limpieza(otra, otra.fold("x"), self.gh, hora(20), [20])
            barrer_limpieza(otra, otra.fold("x"), self.gh, hora(30), [20])
        self.assertEqual(borrar.call_count, 3)
        enc = otra.fold("x")
        self.assertEqual(next((x[2:4] for x in enc.limpieza if x[0] == "runtime")),
                         ("agotado", 3))
        self.assertFalse(enc.limpio)
        fallos = [ev["data"] for ev in otra.eventos("x") if ev["kind"] == "limpieza"
                  and ev["data"]["recurso"] == "runtime"]
        self.assertEqual(len(fallos), 3)
        for fallo in fallos:
            self.assertEqual(fallo["error_clase"], "PermissionError")
            self.assertIn("permiso", fallo["error_mensaje"])
            self.assertNotIn("secreto", json.dumps(fallo))
            self.assertNotIn("/Users/", json.dumps(fallo))

    def test_cerrado_sin_cambios_no_recrea_carpetas_ni_escribe_en_segunda_pasada(self) -> None:
        numero = self._final(con_pr=True, cancelar=False)
        self.reg.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "F", "paso": "pr_con_pendientes", "numero": numero,
            "sha": "h", "hora": hora(),
        }}, "nivel:x")
        self.reg.anotar("x", {"kind": "comprobacion", "data": {
            "fase": "F", "clase": "cierre_comprobado",
            "evidencia": "listo para el merge", "hora": hora(),
        }}, "cierre:x")
        self.assertEqual(self.reg.fold("x").estado, "terminado")
        vencido = 60 * 24 * 8
        for recurso, resultado, intento in (
            ("runtime", "agotado", 3), ("espacio", "borrado", 1),
            ("ejecucion", "fallo", 1),
        ):
            self.reg.anotar("x", {"kind": "limpieza", "data": {
                "recurso": recurso, "clave": "x", "resultado": resultado,
                "intento": intento, "hora": hora(vencido),
            }}, f"semilla:{recurso}")
        with mock.patch.object(politica, "ENTREGA_V4", True):
            ciclo.barrer(self.reg, hora(vencido + 1), 0, None, gh=self.gh)
            antes = self.reg.ultimo_evento("x")
            ciclo.barrer(self.reg, hora(vencido + 2), 1, None, gh=self.gh)
        nuevos = [ev for ev in self.reg.eventos("x") if ev["id"] > antes]
        self.assertEqual([ev for ev in nuevos if ev["kind"] == "limpieza"], [], nuevos)
        self.assertFalse((self.reg.base / "espacio" / "x").exists())

    def test_agotado_borrado_y_fallo_reciente_no_escriben_en_segunda_pasada(self) -> None:  # no-roja-en-base: el cancelado sin PR ya era estable; el PR cerrado que recreaba espacio se prueba arriba
        self._final()
        for recurso, resultado, intento in (
            ("runtime", "agotado", 3), ("ejecucion", "borrado", 1),
            ("espacio", "fallo", 1),
        ):
            self.reg.anotar("x", {"kind": "limpieza", "data": {
                "recurso": recurso, "clave": "x", "resultado": resultado,
                "intento": intento, "hora": hora(60 * 24 * 8),
            }}, f"semilla:{recurso}")
        with mock.patch.object(politica, "ENTREGA_V4", True):
            ciclo.barrer(self.reg, hora(60 * 24 * 8 + 1), 0, None, gh=self.gh)
            antes = self.reg.ultimo_evento("x")
            ciclo.barrer(self.reg, hora(60 * 24 * 8 + 2), 1, None, gh=self.gh)
        self.assertEqual(self.reg.ultimo_evento("x"), antes)

    def test_supervisor_no_recrea_carpetas_de_terminado_con_conductor(self) -> None:
        from motor.encargos.cli import _paso_del_supervisor

        self._final(cancelar=False)
        self.reg.anotar("x", {"kind": "conductor", "data": {
            "token": "conductor-1", "host": "prueba", "hora": hora(),
        }}, "conductor:x")
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "cierre", "hora": hora(),
        }}, "fin:x")
        self.assertEqual(self.reg.fold("x").estado, "terminado")
        vencido = 60 * 24 * 8
        with (
            mock.patch("motor.encargos.transporte.cargar", return_value={}),
            mock.patch("motor.encargos.transporte.operar", return_value={
                "alcanzada": True, "salida": {"estado": "recibido"},
            }),
            mock.patch("motor.integraciones.directores.conciliar_autoridad"),
        ):
            _paso_del_supervisor(self.reg, hora(vencido), None, False, 10**9,
                                 self.gh, mock.Mock())
            antes = self.reg.ultimo_evento("x")
            _paso_del_supervisor(self.reg, hora(vencido + 1), None, False, 10**9,
                                 self.gh, mock.Mock())
        self.assertEqual(self.reg.ultimo_evento("x"), antes,
                         [ev for ev in self.reg.eventos("x") if ev["id"] > antes])
        for tipo in ("ejecucion", "espacio"):
            self.assertFalse((self.reg.base / tipo / "x").exists())

    def test_supervisor_no_recrea_carpetas_por_respuesta_de_cerrado(self) -> None:
        from motor.encargos.cli import _paso_del_supervisor

        self._final(cancelar=False)
        self.reg.anotar("x", {"kind": "solicitud", "data": {
            "id": "s-1", "fase": "F", "intento": "int-1",
            "operacion": "remoto.leer", "recurso": "ci-github",
            "estado": "pregunta", "pregunta": "¿leer?", "hora": hora(),
        }}, "solicitud:x")
        self.reg.anotar("x", {"kind": "respuesta", "data": {
            "solicitud": "s-1", "texto": "sí", "hora": hora(),
        }}, "respuesta:x")
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "cierre", "hora": hora(),
        }}, "fin:x")
        self.assertEqual(self.reg.fold("x").solicitudes[0].estado, "respuesta")
        with (
            mock.patch("motor.encargos.transporte.cargar", return_value=None),
            mock.patch("motor.integraciones.directores.conciliar_autoridad"),
        ):
            _paso_del_supervisor(self.reg, hora(), None, False, 10**9,
                                 self.gh, mock.Mock())
            antes = self.reg.ultimo_evento("x")
            _paso_del_supervisor(self.reg, hora(1), None, False, 10**9,
                                 self.gh, mock.Mock())
        self.assertEqual(self.reg.ultimo_evento("x"), antes)
        for tipo in ("ejecucion", "espacio"):
            self.assertFalse((self.reg.base / tipo / "x").exists())

    def test_supervisor_lee_resultado_plegado_sin_crear_espacio(self) -> None:
        from motor.encargos.cli import _paso_del_supervisor

        self._final(cancelar=False)
        self.reg.anotar("x", {"kind": "producto_listo", "data": {
            "fase": "F", "version": "v2",
            "manifiesto": [["saludo.txt", "aaa"]],
            "sha_git": "", "hora": hora(),
        }}, "producto:x")
        self.reg.anotar("x", {"kind": "revision", "data": {
            "fase": "F", "revision_id": "r1", "version": "v2",
            "veredicto": "aprobado", "origen": "independiente",
            "revisor": "revisor", "evidencia": "listo",
            "hora": hora(),
        }}, "revision:x")
        self.reg.anotar("x", {"kind": "cierre_forzado", "data": {
            "evidencia": "cierre", "hora": hora(),
        }}, "fin:x")
        self.assertEqual(self.reg.fold("x").estado, "terminado")
        with (
            mock.patch("motor.encargos.transporte.cargar", return_value={}),
            mock.patch("motor.encargos.transporte.operar", return_value={
                "alcanzada": True, "salida": {"estado": "recibido"},
            }) as operar,
            mock.patch("motor.integraciones.directores.conciliar_autoridad"),
        ):
            _paso_del_supervisor(self.reg, hora(), None, False, 10**9,
                                 self.gh, mock.Mock())
            antes = self.reg.ultimo_evento("x")
            _paso_del_supervisor(self.reg, hora(1), None, False, 10**9,
                                 self.gh, mock.Mock())
        self.assertTrue(any(call.args[1].get("op") == "resultado"
                            for call in operar.call_args_list))
        self.assertEqual(self.reg.ultimo_evento("x"), antes)
        self.assertFalse((self.reg.base / "espacio" / "x").exists())
        self.assertFalse((self.reg.base / "ejecucion" / "x").exists())

    def test_recursos_github_terminales_no_consumen_consultas(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        numero = self._final(con_pr=True)
        self.gh.cerrar_pr(self.repo, numero)
        for tipo, clave, resultado, intento in (
            ("pr", str(numero), "borrado", 1),
            ("rama", "encargo/x", "borrado", 1),
            ("runtime", "x", "agotado", 3),
        ):
            self.reg.anotar("x", {"kind": "limpieza", "data": {
                "recurso": tipo, "clave": clave, "resultado": resultado,
                "intento": intento, "hora": hora(),
            }}, f"semilla:{tipo}")
        with (mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver,
              mock.patch.object(self.gh, "rama_existe", wraps=self.gh.rama_existe) as rama):
            for minuto in (0, 3):
                barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(minuto),
                                PresupuestoLimpieza(self.reg, minuto * 60), minuto * 60)
            self.assertEqual((ver.call_count, rama.call_count), (0, 0))

    def test_pr_agotado_no_permite_borrar_su_rama_sin_comprobar_el_cierre(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        numero = self._final(con_pr=True)
        self.reg.anotar("x", {"kind": "limpieza", "data": {
            "recurso": "pr", "clave": str(numero), "resultado": "agotado",
            "intento": 3, "hora": hora(),
        }}, "agotado:pr")
        with mock.patch.object(self.gh, "borrar_rama",
                               wraps=self.gh.borrar_rama) as borrar:
            barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(),
                            PresupuestoLimpieza(self.reg, 0), 0)
            borrar.assert_not_called()
        self.assertTrue(self.gh.rama_existe(self.repo, "encargo/x"))

    def test_cuatro_fases_canceladas_convergen_sin_releer_prs(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        fases = ("F", "G", "H", "I")
        self.reg.append_many("x", (
            Evento("entrega", {
                "id": "x", "clave": "k", "huella": "h", "texto": "pedido",
                "entrega_nivel": "pr", "repo": self.repo,
                "rama": "encargo/x", "base": "main", "hora": hora(),
            }, "entrega:x"),
            Evento("fases", {"fases": [{"id": f, "resultado": "r"} for f in fases],
                             "hora": hora()}, "fases:x"),
        ))
        ramas = {"encargo/x"}
        for fase in fases:
            rama = f"encargo/x-{fase}"
            git("push", "-q", self.repo, f"HEAD:refs/heads/{rama}", cwd=self.trabajo)
            numero = self.gh.abrir_pr(self.repo, rama, "main", fase, "cuerpo").numero
            self.gh.cerrar_pr(self.repo, numero)
            self.reg.anotar("x", {"kind": "entrega_externa", "data": {
                "fase": fase, "paso": "pr_listo", "numero": numero,
                "sha": "h", "hora": hora(),
            }}, f"pr:{fase}")
            if fase != fases[-1]:
                self.reg.anotar("x", {"kind": "entrega_externa", "data": {
                    "fase": fase, "paso": "pr_con_pendientes", "numero": numero,
                    "sha": "h", "hora": hora(),
                }}, f"nivel:{fase}")
                self.reg.anotar("x", {"kind": "comprobacion", "data": {
                    "fase": fase, "clase": "cierre_comprobado",
                    "evidencia": "cerrada", "hora": hora(),
                }}, f"cierre:{fase}")
            ramas.add(rama)
        self.reg.anotar("x", {"kind": "orden", "data": {
            "accion": "cancelar", "hora": hora(),
        }}, "cancelar:x")
        self.reg.anotar("x", {"kind": "arbol", "data": {
            "observacion": "ausente", "hora": hora(),
        }}, "arbol:x")
        enc = self.reg.fold("x")
        self.assertEqual(enc.estado, "cancelado")
        self.assertEqual(len({f.pr_numero for f in enc.fases if f.pr_numero}), 4)
        with mock.patch.object(self.gh, "ver", wraps=self.gh.ver) as ver:
            for vuelta in range(6):
                presupuesto = PresupuestoLimpieza(self.reg, vuelta * 130)
                anterior = ver.call_count
                anterior_evento = self.reg.ultimo_evento("x")
                enc = barrer_limpieza(self.reg, self.reg.fold("x"), self.gh,
                                      hora(vuelta * 130 // 60), presupuesto, vuelta * 130)
                vistos = [x.args[1] for x in ver.call_args_list[anterior:]]
                self.assertEqual(len(vistos), len(set(vistos)))
                if vuelta >= 2:
                    self.assertEqual(self.reg.ultimo_evento("x"), anterior_evento)
                    self.assertEqual(presupuesto.consultas, politica.LIMPIEZA.consultas_gh)
            self.assertLessEqual(ver.call_count, 4)
        self.assertTrue(enc.limpio)
        self.assertTrue(all(not self.gh.rama_existe(self.repo, rama) for rama in ramas))

    def test_freno_de_github_no_gasta_intentos_ni_reintenta_esa_pasada(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        self._final(con_pr=True)
        errores = (
            RuntimeError("API rate limit already exceeded"),
            RuntimeError("secondary rate limit: token=secreto"),
            RuntimeError("HTTP 502 Bad Gateway"),
            RuntimeError("gh api: could not resolve host"),
            RuntimeError("gh: dial tcp api.github.com: i/o timeout"),
            RuntimeError("gh: net/http: TLS handshake timeout"),
            RuntimeError("gh: context deadline exceeded"),
            RuntimeError("gh: error connecting to api.github.com"),
            RuntimeError("gh: This may be the result of a timeout"),
            subprocess.TimeoutExpired("gh", 30),
        )
        for exc in errores:
            with self.subTest(error=type(exc).__name__, mensaje=str(exc)), mock.patch.object(
                self.gh, "ver", side_effect=exc,
            ) as ver:
                enc = barrer_limpieza(self.reg, self.reg.fold("x"), self.gh,
                                      hora(), PresupuestoLimpieza(self.reg), 0)
                self.assertEqual(ver.call_count, 1)
                self.assertFalse(enc.limpio)
        self.assertFalse([ev for ev in self.reg.eventos("x") if ev["kind"] == "limpieza"
                          and ev["data"]["recurso"] in ("pr", "rama")])

    def test_reintento_que_borra_recupera_limpio(self) -> None:  # no-roja-en-base: cubre la recuperación verificable tras un fallo de limpieza bajo V4
        self._final()
        runtime = self.reg.base / "runtime" / "x"
        runtime.mkdir(parents=True)
        (runtime / "ocupado").write_text("dato", encoding="utf-8")
        with mock.patch("motor.encargos.entrega.limpieza.borrar_arbol",
                        side_effect=PermissionError("ocupado")):
            enc = self._limpiar()
        self.assertFalse(enc.limpio)
        self.assertEqual(next(x[2] for x in enc.limpieza if x[0] == "runtime"), "fallo")
        self._limpiar(9)
        self.assertTrue(runtime.exists())
        enc = self._limpiar(10)
        self.assertFalse(runtime.exists())
        self.assertTrue(enc.limpio)
        self.assertEqual(next(x[2] for x in enc.limpieza if x[0] == "runtime"), "borrado")

    def test_timeout_de_github_no_gasta_intentos_y_se_reintenta_tras_el_freno(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        self._final(con_pr=True)
        with mock.patch.object(self.gh, "ver",
                               side_effect=subprocess.TimeoutExpired("gh", 30)) as ver:
            for minuto in (0, 1, 2):
                barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(minuto),
                                 PresupuestoLimpieza(self.reg, minuto * 60), minuto * 60)
            self.assertEqual(ver.call_count, 2)
        enc = self.reg.fold("x")
        self.assertFalse(enc.limpio)
        self.assertFalse([ev for ev in self.reg.eventos("x") if ev["kind"] == "limpieza"
                          and ev["data"]["recurso"] == "pr"])
        barrer_limpieza(self.reg, enc, self.gh, hora(4),
                        PresupuestoLimpieza(self.reg, 240), 240)
        self.assertEqual(self.reg.fold("x").prs[0].pr.estado, "cerrado")

    def test_presupuesto_github_cuenta_consultas_y_se_conserva_entre_pasadas(self) -> None:
        from motor.encargos.entrega.limpieza import PresupuestoLimpieza, barrer_limpieza

        self._final(con_pr=True)
        with mock.patch.object(politica, "LIMPIEZA", replace(
            politica.LIMPIEZA, consultas_gh=2,
        )):
            primero = PresupuestoLimpieza(self.reg, 0)
            barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(), primero, 0)
            self.assertEqual(primero.consultas, 0)
            despues = self.reg.ultimo_evento("x")
            segundo = PresupuestoLimpieza(self.reg, 1)
            barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(1), segundo, 1)
            self.assertEqual(segundo.consultas, 0)
            self.assertEqual(self.reg.ultimo_evento("x"), despues)
            tercero = PresupuestoLimpieza(self.reg, 120)
            barrer_limpieza(self.reg, self.reg.fold("x"), self.gh, hora(2), tercero, 120)
            self.assertEqual(tercero.consultas, 0)

    def test_reintentar_agotados_es_una_orden_explicita_y_no_un_borrado_fingido(self) -> None:
        from ops.limpieza_reintentar import main

        self._final()
        runtime = self.reg.base / "runtime" / "x"
        runtime.mkdir(parents=True)
        (runtime / "resto").write_text("dato", encoding="utf-8")
        for tipo, clave in (("runtime", "x"), ("rama", "encargo/x")):
            self.reg.anotar("x", {"kind": "limpieza", "data": {
                "recurso": tipo, "clave": clave, "resultado": "agotado",
                "intento": 3, "hora": hora(),
            }}, f"agotado:{tipo}")
        argv = ["x", "--base", str(self.reg.base), "--clave", "vez-1"]
        with contextlib.redirect_stdout(io.StringIO()) as salida:
            self.assertEqual(main(argv), 0)
            mitad = self.reg.ultimo_evento("x")
            self.assertEqual(main(argv), 0)
        self.assertEqual(self.reg.ultimo_evento("x"), mitad)
        self.assertEqual({(tipo, resultado, intento) for tipo, _, resultado, intento, _
                          in self.reg.fold("x").limpieza},
                         {("runtime", "pendiente", 0), ("rama", "pendiente", 0)})
        self.assertIn("reiniciados", salida.getvalue())
        self.assertFalse(self.reg.fold("x").limpio)
        enc = self._limpiar(1)
        self.assertTrue(enc.limpio)
        self.assertFalse(runtime.exists())

    def test_reintentar_tras_un_limpio_previo_vuelve_a_acreditar_limpio(self) -> None:
        from ops.limpieza_reintentar import main

        self._final()
        self.assertTrue(self._limpiar().limpio)
        runtime = self.reg.base / "runtime" / "x"
        runtime.mkdir(parents=True)
        with mock.patch("motor.encargos.entrega.limpieza.borrar_arbol",
                        side_effect=PermissionError("ocupado")):
            for minuto in (1, 11, 21):
                self._limpiar(minuto)
        self.assertEqual(next(r for tipo, _, r, _, _ in self.reg.fold("x").limpieza
                              if tipo == "runtime"), "agotado")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["x", "--base", str(self.reg.base), "--clave", "rescate"]), 0)
        enc = self._limpiar(22)
        self.assertFalse(runtime.exists())
        self.assertTrue(enc.limpio)

    def test_clave_de_reintento_usada_no_miente_tras_nuevo_agotamiento(self) -> None:
        from ops.limpieza_reintentar import main

        self._final()
        datos = {"recurso": "runtime", "clave": "x", "resultado": "agotado",
                 "intento": 3, "hora": hora()}
        self.reg.anotar("x", {"kind": "limpieza", "data": datos}, "agotado:primero")
        argv = ["x", "--base", str(self.reg.base), "--clave", "rescate"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv), 0)
        self.reg.anotar("x", {"kind": "limpieza", "data": {
            **datos, "hora": hora(10),
        }}, "agotado:segundo")
        anterior = self.reg.ultimo_evento("x")
        with contextlib.redirect_stdout(io.StringIO()) as salida:
            self.assertEqual(main(argv), 2)
        self.assertIn("rechazada", salida.getvalue())
        self.assertEqual(self.reg.ultimo_evento("x"), anterior)
        self.assertEqual(next(r for tipo, _, r, _, _ in self.reg.fold("x").limpieza
                              if tipo == "runtime"), "agotado")

    def test_las_carpetas_vencen_a_los_siete_dias_y_no_reaparecen(self) -> None:
        self._final()
        for nombre in ("espacio", "ejecucion"):
            carpeta = self.reg.base / nombre / "x"
            carpeta.mkdir(parents=True)
            (carpeta / "huella").write_text("temporal", encoding="utf-8")
        self.assertTrue(self._limpiar(60 * 24 * 6).limpio)
        self.assertTrue((self.reg.base / "espacio" / "x").exists())
        with mock.patch.object(politica, "ENTREGA_V4", True):
            ciclo.barrer(self.reg, hora(60 * 24 * 8), 0, None, gh=self.gh)
            ciclo.barrer(self.reg, hora(60 * 24 * 8 + 1), 0, None, gh=self.gh)
        for nombre in ("espacio", "ejecucion"):
            self.assertFalse((self.reg.base / nombre / "x").exists())

    def test_espacio_no_se_borra_si_el_pr_sigue_abierto(self) -> None:
        numero = self._final(con_pr=True, cancelar=False)
        self.reg.anotar("x", {"kind": "entrega_externa", "data": {
            "fase": "F", "paso": "pr_con_pendientes",
            "numero": numero, "sha": "h", "hora": hora(),
        }}, "nivel:x")
        self.reg.anotar("x", {"kind": "comprobacion", "data": {
            "fase": "F", "clase": "cierre_comprobado",
            "evidencia": "listo para David", "hora": hora(),
        }}, "cierre:x")
        self.assertEqual(self.reg.fold("x").estado, "terminado")
        espacio = self.reg.base / "espacio" / "x"
        espacio.mkdir(parents=True)
        enc = self._limpiar(60 * 24 * 8)
        self.assertTrue(espacio.exists())
        self.assertFalse(enc.limpio)
        self.gh.cerrar_pr(self.repo, numero)
        enc = self._limpiar(60 * 24 * 8 + 1)
        self.assertFalse(espacio.exists())
        self.assertTrue(enc.limpio)

    def test_sondeo_deepseek_todavia_abierto_conserva_las_carpetas(self) -> None:
        self._final()
        from motor.encargos.entrega.limpieza import Recurso, _disco

        ejecucion = self.reg.base / "ejecucion" / "x"
        ejecucion.mkdir(parents=True)
        enc = replace(self.reg.fold("x"), hora_cierre=hora(60 * 24 * 4))
        estado = _disco(self.reg, enc, Recurso("ejecucion", "x"),
                        hora(60 * 24 * 8), [20], self.gh)
        self.assertEqual(estado, "conservado")
        self.assertTrue(ejecucion.exists())


if __name__ == "__main__":
    unittest.main()
