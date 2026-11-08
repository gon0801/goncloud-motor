"""Los marcadores que decidian viven en el registro, con migracion idempotente.

Cada archivo de una instalacion anterior se convierte una sola vez en su
evento. Una caida entre persistir el evento y borrar el archivo no duplica
nada, y dos conexiones a la vez tampoco.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.ciclo import ahora_iso, barrer, recibir
from motor.encargos.registro import Registro


def _encargo(tmp: str) -> tuple[Registro, str, Path]:
    reg = Registro(Path(tmp) / "base")
    enc = recibir(
        reg, "un pedido de prueba", "marcadores", ahora_iso(), via="directa"
    )["encargo"]
    directorio = Path(reg.base) / "ejecucion" / enc
    directorio.mkdir(parents=True, exist_ok=True)
    return reg, enc, directorio


def _archivos_viejos(directorio: Path) -> None:
    sha = "a" * 40
    (directorio / "incorporado").write_text(ahora_iso(), encoding="utf-8")
    (directorio / "quietud.json").write_text(
        json.dumps({"evento": 7, "vueltas": 2, "desde": 100.0, "visto": 130.0}),
        encoding="utf-8",
    )
    (directorio / "deepseek_lectura.json").write_text(
        json.dumps({"sha": sha, "epoch": 100.0, "error": "", "aviso": 0}),
        encoding="utf-8",
    )
    (directorio / "deepseek_cerrado.json").write_text(
        json.dumps(
            {
                "shas": {sha: 100.0},
                "inicios": {sha: 90.0},
                "desde": 50.0,
            }
        ),
        encoding="utf-8",
    )
    (directorio / "ci-sondeo.json").write_text(
        json.dumps({"sha": sha, "primero": "h1", "ultimo": "h2", "sondeos": 3}),
        encoding="utf-8",
    )
    (directorio / "cierre.json").write_text(
        json.dumps({"clase": "cierre_comprobado", "evidencia": "cierre viejo"}),
        encoding="utf-8",
    )


def _eventos(reg: Registro, enc: str, kind: str) -> list[dict]:
    return [ev for ev in reg.eventos(enc) if ev["kind"] == kind]


class MigracionDeMarcadores(unittest.TestCase):
    def test_los_archivos_viejos_se_convierten_en_eventos_una_sola_vez(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg, enc, directorio = _encargo(tmp)
            self.addCleanup(reg.close)
            _archivos_viejos(directorio)

            barrer(reg, ahora_iso(), 1_000.0, None)
            barrer(reg, ahora_iso(), 1_030.0, None)

            self.assertEqual(len(_eventos(reg, enc, "incorporado")), 1)
            self.assertEqual(len(_eventos(reg, enc, "quietud_vigilada")), 1)
            self.assertEqual(len(_eventos(reg, enc, "deepseek_lectura")), 1)
            self.assertEqual(len(_eventos(reg, enc, "deepseek_cerrado")), 1)
            self.assertEqual(len(_eventos(reg, enc, "ci_sondeo")), 1)
            for nombre in (
                "incorporado",
                "quietud.json",
                "deepseek_lectura.json",
                "deepseek_cerrado.json",
                "ci-sondeo.json",
                "cierre.json",
            ):
                self.assertFalse(
                    (directorio / nombre).exists(), f"{nombre} sigue decidiendo"
                )

            encargo = reg.fold(enc)
            self.assertIn(0, encargo.incorporado)
            self.assertEqual(encargo.lectura_deepseek.sha, "a" * 40)
            self.assertEqual(encargo.sondeo_ci.sondeos, 3)
            self.assertEqual(dict(encargo.deepseek_cerrado.shas)["a" * 40], 100.0)

    def test_una_caida_tras_persistir_el_evento_no_lo_duplica(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg, enc, directorio = _encargo(tmp)
            self.addCleanup(reg.close)
            _archivos_viejos(directorio)
            real = Path.unlink

            def falla(ruta, missing_ok=False):
                if ruta.name == "deepseek_lectura.json":
                    raise OSError("caida simulada")
                return real(ruta, missing_ok=missing_ok)

            with mock.patch.object(Path, "unlink", falla):
                with self.assertRaises(OSError):
                    barrer(reg, ahora_iso(), 1_000.0, None)

            self.assertTrue(
                (directorio / "deepseek_lectura.json").exists(),
                "el archivo queda si la caida fue antes de borrarlo",
            )
            self.assertEqual(len(_eventos(reg, enc, "deepseek_lectura")), 1)

            barrer(reg, ahora_iso(), 1_030.0, None)
            self.assertEqual(len(_eventos(reg, enc, "deepseek_lectura")), 1)
            self.assertFalse((directorio / "deepseek_lectura.json").exists())

    def test_dos_conexiones_a_la_vez_no_duplican_la_migracion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg, enc, directorio = _encargo(tmp)
            self.addCleanup(reg.close)
            _archivos_viejos(directorio)
            migrar = getattr(ciclo, "migrar_marcadores", None)
            migrados = getattr(ciclo, "_MARCADORES_MIGRADOS", None)
            if migrados is not None:
                migrados.clear()
            barrera = threading.Barrier(2)

            def correr() -> None:
                base = Registro(Path(reg.base))
                try:
                    barrera.wait()
                    if migrados is not None:
                        migrados.clear()
                    if migrar is None:
                        barrer(base, ahora_iso(), 1_000.0, None)
                    else:
                        migrar(base, ahora_iso())
                finally:
                    base.close()

            hilos = [threading.Thread(target=correr) for _ in range(2)]
            for hilo in hilos:
                hilo.start()
            for hilo in hilos:
                hilo.join(timeout=30)
                self.assertFalse(hilo.is_alive(), "la migracion no termino")

            for kind in (
                "incorporado",
                "quietud_vigilada",
                "deepseek_lectura",
                "deepseek_cerrado",
                "ci_sondeo",
            ):
                self.assertEqual(len(_eventos(reg, enc, kind)), 1, kind)


class HechosDeMarcadorEnElReducer(unittest.TestCase):
    def test_cada_marcador_actualiza_su_hecho_en_el_fold(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg, enc, _directorio = _encargo(tmp)
            self.addCleanup(reg.close)
            casos = [
                (
                    {"kind": "incorporado", "data": {"generacion": 1}},
                    "incorporado",
                    "uno",
                ),
                (
                    {
                        "kind": "quietud_vigilada",
                        "data": {
                            "evento": 4,
                            "vueltas": 2,
                            "desde": 10.0,
                            "visto": 40.0,
                        },
                    },
                    "quietud_vigilada",
                    "dos",
                ),
                (
                    {
                        "kind": "deepseek_lectura",
                        "data": {
                            "sha": "b" * 40,
                            "epoch": 20.0,
                            "error": "",
                            "aviso": 0,
                        },
                    },
                    "lectura_deepseek",
                    "tres",
                ),
                (
                    {
                        "kind": "deepseek_cerrado",
                        "data": {"shas": {}, "inicios": {}, "desde": 5.0},
                    },
                    "deepseek_cerrado",
                    "cuatro",
                ),
                (
                    {
                        "kind": "ci_sondeo",
                        "data": {
                            "sha": "b" * 40,
                            "primero": "h1",
                            "ultimo": "h2",
                            "sondeos": 2,
                        },
                    },
                    "sondeo_ci",
                    "cinco",
                ),
            ]
            for evento, _campo, clave in casos:
                resultado = reg.anotar(enc, evento, f"hecho-{clave}")
                self.assertEqual(resultado, "nuevo", evento["kind"])

            encargo = reg.fold(enc)
            self.assertIn(1, encargo.incorporado)
            self.assertEqual(encargo.quietud_vigilada, (4, 2, 10.0, 40.0))
            self.assertEqual(encargo.lectura_deepseek.sha, "b" * 40)
            self.assertEqual(encargo.deepseek_cerrado.desde, 5.0)
            self.assertEqual(encargo.sondeo_ci.sondeos, 2)


if __name__ == "__main__":
    unittest.main()
