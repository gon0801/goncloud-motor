"""Fronteras de caída de los marcadores y las series que no deben cortarse.

La unidad anterior movió los marcadores al registro. Estas pruebas fijan que
una caída entre el efecto y su marca no repite el efecto, que el sondeo no se
repite, y que una observación de marcador no corta una serie de reintentos.
"""

from __future__ import annotations

import itertools
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.ciclo import (
    ahora_iso,
    barrer,
    recibir,
)
from motor.encargos.registro import Registro
from tests.dobles import github_con_corridas
from tests.test_flujo_revisiones import (
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _eventos(reg: Registro, enc: str, kind: str) -> list[dict]:
    return [ev for ev in reg.eventos(enc) if ev["kind"] == kind]


def _falla_al_anotar(reg: Registro, kind: str):
    real = reg.anotar

    def anotar(encargo, evento, ukey):
        if evento["kind"] == kind:
            raise OSError(f"caída simulada antes de {kind}")
        return real(encargo, evento, ukey)

    return anotar


class QuietudTrasUnHueco(unittest.TestCase):
    def test_un_hueco_mayor_que_el_umbral_no_deja_la_cuenta_trabada(self):  # no-roja-en-base: con archivos la base ya no se trababa; la regresión fue de esta unidad
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            enc = recibir(reg, "pedido", "quietud-hueco", ahora_iso())["encargo"]
            decision = {"lanzar": False, "matar": False}
            epochs = [1000, 1030] + [2030 + 30 * i for i in range(120)]
            with mock.patch.object(ciclo, "sin_nada_en_curso", lambda _e: True):
                for epoch in epochs:
                    ciclo._vigilar_quietud(
                        reg,
                        reg.fold(enc),
                        decision,
                        "ausente",
                        False,
                        ahora_iso(),
                        float(epoch),
                    )
            self.assertGreaterEqual(len(_eventos(reg, enc, "quietud")), 1)
            observacion = reg.fold(enc).quietud_vigilada
            self.assertGreater(observacion[1], 0)


class SeriesDeReintentos(unittest.TestCase):
    def test_un_marcador_entre_dos_esperas_no_corta_la_serie_de_repo(self):  # no-roja-en-base: en la base el marcador era un archivo y no estaba en el registro; la regresión fue de esta unidad
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            enc = recibir(reg, "pedido", "serie-repo", ahora_iso())["encargo"]
            causa = "git fetch: fatal: repository not found"
            contador = itertools.count()
            with mock.patch.object(
                ciclo, "_reanuda_en", lambda _s: f"2030-01-01T00:00:{next(contador):02d}Z"
            ):
                for i in range(3):
                    ciclo._fallo_de_repo(
                        reg, reg.fold(enc), causa, ahora_iso(), f"repo-{i}"
                    )
                    ciclo._marcar_lectura_deepseek(
                        reg, reg.fold(enc), "a" * 40, 100.0 + i
                    )
            self.assertIn("repo", [i.clase for i in reg.fold(enc).impedimentos])


class CaidasEntreElEfectoYLaMarca(unittest.TestCase):
    def test_una_caida_antes_de_incorporar_no_duplica_el_bloqueo(self):  # no-roja-en-base: la base usa el archivo como marca; la prueba fija que la caída no repite el bloqueo
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            enc = recibir(reg, "pedido", "caida-incorporar", ahora_iso())["encargo"]
            hora = ahora_iso()
            reg.anotar(
                enc,
                {
                    "kind": "pendiente_entrega",
                    "data": {
                        "texto": ciclo._RELEVO_AGOTADO + " del rol responsable",
                        "hora": hora,
                    },
                },
                "pendiente-caida",
            )
            with mock.patch.object(
                reg, "anotar", _falla_al_anotar(reg, "incorporado")
            ):
                with self.assertRaises(OSError):
                    ciclo._entregar_con_pendientes(
                        reg, reg.fold(enc), None, hora
                    )
            # El efecto ya quedó escrito; la marca no. Al reanudar no se repite.
            self.assertEqual(len(_eventos(reg, enc, "bloqueo_fase")), 1)
            self.assertEqual(len(_eventos(reg, enc, "incorporado")), 0)
            ciclo._entregar_con_pendientes(reg, reg.fold(enc), None, hora)
            self.assertEqual(len(_eventos(reg, enc, "bloqueo_fase")), 1)
            final = reg.fold(enc)
            self.assertTrue(
                final.estado in ("terminado", "recibido")
                or any(f.estado == "bloqueada" for f in final.fases),
                final.estado,
            )

    def test_una_caida_antes_de_marcar_el_fallo_del_proveedor_no_lo_duplica(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            enc = recibir(reg, "pedido", "caida-proveedor", ahora_iso())["encargo"]
            directorio = Path(reg.base) / "ejecucion" / enc
            directorio.mkdir(parents=True, exist_ok=True)
            espacio = ciclo._espacio(reg.base, enc)
            (directorio / "outcome.json").write_text(
                json.dumps(
                    {
                        "fallo_runtime": "el CLI terminó con código 1",
                        "autenticacion_expirada": False,
                    }
                ),
                encoding="utf-8",
            )
            (directorio / "arranque.json").write_text(
                json.dumps(
                    {
                        "agente": "codex",
                        "modelo": "openai/gpt-6-astra",
                        "sesion_modo": "reanudada",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                reg, "anotar", _falla_al_anotar(reg, "incorporado")
            ):
                with self.assertRaises(OSError):
                    ciclo._incorporar(
                        reg, reg.fold(enc), espacio, ahora_iso(), directorio
                    )
            self.assertEqual(len(_eventos(reg, enc, "proveedor_fallo")), 1)
            self.assertEqual(len(_eventos(reg, enc, "incorporado")), 0)
            ciclo._incorporar(reg, reg.fold(enc), espacio, ahora_iso(), directorio)
            self.assertEqual(len(_eventos(reg, enc, "proveedor_fallo")), 1)
            self.assertEqual(len(_eventos(reg, enc, "incorporado")), 1)

    def test_una_caida_tras_el_sondeo_no_repite_la_lectura_del_ci(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "caida-ci",
                github_con_corridas,
                {"corridas": {"bateria": "success"}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            llamadas = []
            original = vuelta.gh.checks

            def checks(*args, **kwargs):
                llamadas.append(args)
                raise RuntimeError("caída tras la lectura")

            vuelta.gh.checks = checks
            try:
                barrer(r.reg, ahora_iso(), time.time(), None, gh=vuelta.gh)
                self.assertEqual(len(llamadas), 1)
                self.assertEqual(len(_eventos(r.reg, r.enc, "ci_sondeo")), 1)
                # El sondeo quedó fechado; la vuelta siguiente no lo repite.
                vuelta.gh.checks = original
                barrer(r.reg, ahora_iso(), time.time(), None, gh=vuelta.gh)
                self.assertEqual(len(llamadas), 1)
            finally:
                vuelta.gh.checks = original


if __name__ == "__main__":
    unittest.main()


class MigracionAislada(unittest.TestCase):
    def test_una_historia_rota_no_tumba_la_migracion_ni_el_barrido(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            bueno = recibir(
                reg, "pedido bueno", "migra-bueno", ahora_iso(), via="directa"
            )["encargo"]
            roto = recibir(
                reg, "pedido roto", "migra-roto", ahora_iso(), via="directa"
            )["encargo"]
            directorio = Path(reg.base) / "ejecucion" / bueno
            directorio.mkdir(parents=True, exist_ok=True)
            (directorio / "incorporado").write_text(ahora_iso(), encoding="utf-8")
            # Una historia que no pliega: el evento entra directo, sin `append`.
            reg.db.execute(
                "INSERT INTO events (ts, encargo, kind, data, ukey) "
                "VALUES (?, ?, ?, ?, ?)",
                (ahora_iso(), roto, "kind_desconocido", "{}", "roto:1"),
            )
            barrer(reg, ahora_iso(), 1.0, None)
            self.assertEqual(len(_eventos(reg, bueno, "incorporado")), 1)


class SerieDeEntrega(unittest.TestCase):
    def test_una_espera_transitoria_de_otra_clase_corta_la_serie(self):  # no-roja-en-base: la base ya cortaba la serie con otra clase; la regresión fue de esta unidad
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            enc = recibir(reg, "pedido", "serie-entrega", ahora_iso())["encargo"]
            permanente = "git push: remote rechazo el cambio"
            transitoria = "git pull: connection timed out"
            for i, motivo in enumerate((permanente, permanente, transitoria)):
                reg.anotar(
                    enc,
                    {
                        "kind": "espera",
                        "data": {
                            "motivo": motivo,
                            "reanuda_en": f"2030-01-01T00:00:0{i}Z",
                            "hora": ahora_iso(),
                        },
                    },
                    f"espera-serie:{i}",
                )
            e = reg.fold(enc)
            self.assertEqual(ciclo._fallos_de_entrega_seguidos(e, permanente), 0)
            self.assertEqual(ciclo._fallos_de_entrega_seguidos(e, transitoria), 0)
