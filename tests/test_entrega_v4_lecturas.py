"""Lecturas de entrega desde el plegado y reproducibilidad de hechos guardados."""

from __future__ import annotations

import contextlib
import io
import json
import random
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from motor.encargos.dominio import reducir
from motor.encargos.registro import Evento, Registro
from motor.encargos.reductor import TABLA
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import _entrega_pr


def _pedido(ident: str) -> Evento:
    return Evento("entrega", {
        "id": ident, "clave": ident, "huella": "h", "texto": "pedido",
        "hora": "2026-11-16T00:00:00Z",
    }, f"entrega:{ident}")


def _fases(ident: str) -> Evento:
    return Evento("fases", {
        "fases": [{"id": "F", "resultado": "r"}],
        "hora": "2026-11-16T00:01:00Z",
    }, f"fases:{ident}")


def _comprobar_secuencia(reg: Registro, otro: Registro, ident: str) -> None:
    guardados = reg.eventos(ident)
    estado = None
    try:
        for evento in guardados:
            estado = reducir(estado, evento, nuevo=False)
        replegado = otro.fold(ident)
    except Exception as exc:
        raise AssertionError(f"el plegado de {ident} rompió: {type(exc).__name__}") from exc
    assert asdict(estado) == asdict(reg.fold(ident)) == asdict(replegado)


class LecturasPlegadas(unittest.TestCase):
    def test_la_ultima_devolucion_sale_del_plegado_sin_recorrer_eventos(self) -> None:
        from motor.encargos.ciclo import (
            _entrega_devuelta_de_la_version, _entrega_devuelta_pendiente,
        )

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp))
            try:
                reg.append_many("x", (
                    _pedido("x"), _fases("x"),
                    Evento("entrega_devuelta", {"version": "", "generacion": 0},
                           "devuelta:x:1"),
                ))
                e = reg.fold("x")
                self.assertEqual(e.entrega_devuelta, ("", 0))
                with mock.patch.object(reg, "eventos", side_effect=AssertionError("log crudo")):
                    self.assertTrue(_entrega_devuelta_pendiente(e))
                    self.assertTrue(_entrega_devuelta_de_la_version(e))
                reg.anotar("x", {"kind": "entrega_devuelta", "data": {
                    "version": "", "generacion": "ilegible",
                }}, "devuelta:x:2")
                e = reg.fold("x")
                self.assertEqual(e.entrega_devuelta, ("", None))
                self.assertFalse(_entrega_devuelta_pendiente(e))
                self.assertTrue(_entrega_devuelta_de_la_version(e))
            finally:
                reg.close()

    def test_la_primera_hora_por_sha_conserva_la_hora_de_la_fila(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp))
            try:
                reg.append_many("x", (_pedido("x"), _fases("x")))
                def anotar(paso: str, sha: str, hora: str, ukey: str) -> None:
                    reg.anotar("x", {"kind": "entrega_externa", "data": {
                        "fase": "F", "paso": paso, "sha": sha, "numero": 7,
                        "hora": hora,
                    }}, ukey)

                anotar("commit", "a" * 40, "2026-11-16T00:02:00Z", "a:commit")
                anotar("commit", "b" * 40, "2026-11-16T00:03:00Z", "b:commit")
                anotar("pr_listo", "a" * 40, "2026-11-16T00:04:00Z", "a:listo")
                reg.db.execute(
                    "INSERT INTO events(ts, encargo, kind, data, ukey) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("2026-11-16T00:05:00Z", "x", "entrega_externa",
                     json.dumps({"fase": "F", "paso": "commit",
                                 "sha": "c" * 40, "numero": 7}), "c:sin-hora"),
                )
                reg.db.commit()
                self.assertEqual(reg.fold("x").primer_envio_por_sha, (
                    ("a" * 40, "2026-11-16T00:02:00Z"),
                    ("b" * 40, "2026-11-16T00:03:00Z"),
                    ("c" * 40, "2026-11-16T00:05:00Z"),
                ))
            finally:
                reg.close()

    def test_espera_deepseek_usa_la_primera_hora_plegada_del_sha(self) -> None:
        from motor.encargos import ciclo

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, recorrido = _entrega_pr(
                self, tmp, "lectura-deepseek-plegada", FakeGitHub, nivel="pr",
            )
            enc = vuelta("uno")
            sha = enc.pr_sha
            primera = dict(enc.primer_envio_por_sha)[sha]
            origen = datetime.fromisoformat(primera.replace("Z", "+00:00"))
            reciente = (origen + timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
            recorrido.reg.anotar(
                recorrido.enc, {"kind": "entrega_externa", "data": {
                    "fase": enc.fase_activa, "intento": enc.intento,
                    "paso": "pr_listo", "repo": enc.entrega.repo,
                    "numero": enc.pr_numero, "sha": sha, "hora": reciente,
                }}, "pr-listo-repetido",
            )
            self.assertEqual(dict(recorrido.fold().primer_envio_por_sha)[sha], primera)
            tarde = (origen + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            with mock.patch.object(ciclo, "_leer_deepseek",
                                   wraps=ciclo._leer_deepseek) as leer:
                final = ciclo._paso_entrega(
                    recorrido.reg, recorrido.fold(), vuelta.gh, tarde,
                )
            self.assertEqual(leer.call_args.args[4], primera)
            self.assertEqual(final.estado, "terminado")
            self.assertTrue(any("sin revisión de DeepSeek" in n for n in final.notas_pr))

    def test_dos_mil_secuencias_guardadas_son_totales_y_repetibles(self) -> None:  # no-roja-en-base: esta propiedad caracteriza el plegado anterior; la mutación vecina demuestra que detecta la omisión
        azar = random.Random(4510)
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp))
            excepciones: set[str] = set()
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    for numero in range(2000):
                        ident = f"r{numero}"
                        reg.append_many(ident, (_pedido(ident), _fases(ident)))
                        for paso in range(azar.randrange(2, 8)):
                            clase = azar.choice((
                                "entrega_devuelta", "entrega_externa", "orden",
                                "pr_observado", "limpieza", "limpio", "espera",
                            ))
                            dato = azar.choice((
                                None, "", "x", [], 0, float("inf"), float("nan"),
                            ))
                            if clase == "entrega_devuelta":
                                data = {"version": dato, "generacion": azar.choice(
                                    (0, numero, "ilegible", float("inf"))),
                                }
                            elif clase == "entrega_externa":
                                data = {"fase": "F", "paso": azar.choice((
                                    "commit", "pr_listo", "push", "deepseek_leido",
                                )), "sha": azar.choice(("a" * 40, "b" * 40, dato)),
                                        "numero": azar.choice((7, 0, dato))}
                            elif clase == "orden":
                                data = {"accion": azar.choice(("parar", "continuar", dato))}
                            elif clase == "pr_observado":
                                data = {"fase": "F", "estado": "abierto",
                                        "numero": dato, "hora": "t"}
                            elif clase == "limpieza":
                                data = {"recurso": "runtime", "clave": ident,
                                        "resultado": "fallo", "intento": dato, "hora": "t"}
                            elif clase == "limpio":
                                data = {"hora": "t"}
                            else:
                                data = {"motivo": dato, "hora": "t"}
                            try:
                                reg.anotar(ident, {"kind": clase, "data": data},
                                           f"{ident}:{paso}")
                            except Exception as exc:
                                excepciones.add(type(exc).__name__)
                self.assertTrue(excepciones)
                otro = Registro(Path(tmp))
                try:
                    for numero in range(2000):
                        _comprobar_secuencia(reg, otro, f"r{numero}")
                finally:
                    otro.close()
            finally:
                reg.close()

    def test_una_mutacion_sembrada_rompe_la_comprobacion(self) -> None:  # no-roja-en-base: prueba la potencia de la comprobación reproducible
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp))
            try:
                reg.append_many("x", (
                    _pedido("x"), _fases("x"),
                    Evento("entrega_devuelta", {"version": "", "generacion": 0},
                           "devuelta:x"),
                ))
                original = TABLA["entrega_devuelta"]

                def mutante(e, d, hora, nuevo):
                    if not nuevo:
                        raise ValueError("mutación sembrada")
                    return original.aplicar(e, d, hora, nuevo)

                otro = Registro(Path(tmp))
                try:
                    with mock.patch.dict(TABLA, {
                        "entrega_devuelta": replace(original, aplicar=mutante),
                    }):
                        with self.assertRaisesRegex(AssertionError, "rompió: ValueError"):
                            _comprobar_secuencia(reg, otro, "x")
                finally:
                    otro.close()
            finally:
                reg.close()
