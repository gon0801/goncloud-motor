from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos.ciclo import _cuerpo_pr
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import (
    Recorrido,
    _entrega_pr,
    _interno,
    sonda_sin_aislamiento_del_host,
)

LIMITE_GITHUB = 65_536


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _pr_unico(gh) -> dict:
    estado = json.loads((gh.d / "github.json").read_text(encoding="utf-8"))
    return next(iter(estado["prs"].values()))


class CuerpoDelPrAlAbrir(unittest.TestCase):
    def test_pr_nuevo_lleva_el_cuerpo_del_archivo_y_la_linea_final(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-ok", FakeGitHub)
            texto = "# Cambios\n\nSe agrega saludo.txt.\n\nSe comprueba leyendo el archivo."
            (_interno(r) / "cuerpo-pr.md").write_text(texto, encoding="utf-8")

            e = vuelta("hola")

            fase = e.fase_activa
            self.assertEqual(
                _pr_unico(vuelta.gh).get("cuerpo"),
                f"{texto}\n\nEncargo {r.enc}. Fase: {fase}.",
            )

    def test_archivo_ausente_conserva_el_cuerpo_de_hoy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-ausente", FakeGitHub)

            vuelta("hola")

            self.assertEqual(
                _pr_unico(vuelta.gh).get("cuerpo"),
                f"Encargo {r.enc}. Revision independiente aprobada.",
            )

    def test_archivo_vacio_conserva_el_cuerpo_de_hoy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-vacio", FakeGitHub)
            (_interno(r) / "cuerpo-pr.md").write_text(" \n\t\n", encoding="utf-8")

            vuelta("hola")

            self.assertEqual(
                _pr_unico(vuelta.gh).get("cuerpo"),
                f"Encargo {r.enc}. Revision independiente aprobada.",
            )

    def test_texto_largo_se_recorta_al_limite_y_declara_donde_esta_completo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-largo", FakeGitHub)
            cabecera = "# Cambios\n\n"
            relleno = "detalle " * 20_000
            (_interno(r) / "cuerpo-pr.md").write_text(
                cabecera + relleno, encoding="utf-8"
            )

            e = vuelta("hola")

            cuerpo = _pr_unico(vuelta.gh).get("cuerpo") or ""
            self.assertLessEqual(len(cuerpo), LIMITE_GITHUB)
            self.assertTrue(cuerpo.startswith(cabecera))
            self.assertIn(".interno/cuerpo-pr.md", cuerpo)
            self.assertTrue(
                cuerpo.endswith(f"Encargo {r.enc}. Fase: {e.fase_activa}.")
            )

    def test_enlace_simbolico_al_host_no_se_publica_y_conserva_el_cuerpo_de_hoy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-enlace-blando", FakeGitHub)
            externo = r.base / "secreto-fuera-del-espacio.txt"
            externo.write_text(
                "oauth_token: gho_SECRETO_FUERA_DEL_ESPACIO", encoding="utf-8"
            )
            enlace = _interno(r) / "cuerpo-pr.md"
            try:
                enlace.symlink_to(externo)
            except OSError as exc:
                self.skipTest(f"el sistema no permite enlaces simbólicos: {exc}")

            vuelta("hola")

            self.assertEqual(
                _pr_unico(vuelta.gh).get("cuerpo"),
                f"Encargo {r.enc}. Revision independiente aprobada.",
            )

    def test_enlace_duro_al_host_no_se_publica_y_conserva_el_cuerpo_de_hoy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-enlace-duro", FakeGitHub)
            externo = r.base / "secreto-fuera-del-espacio.txt"
            externo.write_text(
                "oauth_token: gho_SECRETO_FUERA_DEL_ESPACIO", encoding="utf-8"
            )
            enlace = _interno(r) / "cuerpo-pr.md"
            try:
                os.link(externo, enlace)
            except OSError as exc:
                self.skipTest(f"el sistema no permite enlaces duros: {exc}")

            vuelta("hola")

            self.assertEqual(
                _pr_unico(vuelta.gh).get("cuerpo"),
                f"Encargo {r.enc}. Revision independiente aprobada.",
            )

    def test_bucle_de_enlaces_en_el_directorio_cae_al_cuerpo_de_hoy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp)
            try:
                os.symlink(".interno", espacio / ".interno")
            except OSError as exc:
                self.skipTest(f"el sistema no permite enlaces simbólicos: {exc}")
            enc = SimpleNamespace(id="enc_x", fase_activa="fase_x")

            cuerpo = _cuerpo_pr(enc, espacio)

            self.assertEqual(cuerpo, "Encargo enc_x. Revision independiente aprobada.")

    @unittest.skipIf(os.name == "nt", "Windows no tiene FIFO POSIX")
    def test_fifo_no_bloquea_y_cae_al_cuerpo_de_hoy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp)
            interno = espacio / ".interno"
            interno.mkdir()
            os.mkfifo(interno / "cuerpo-pr.md")
            enc = SimpleNamespace(id="enc_x", fase_activa="fase_x")
            resultado: dict = {}

            def leer() -> None:
                resultado["cuerpo"] = _cuerpo_pr(enc, espacio)

            hilo = threading.Thread(target=leer, daemon=True)
            hilo.start()
            hilo.join(5)

            self.assertFalse(hilo.is_alive(), "la lectura de la FIFO se quedó bloqueada")
            self.assertEqual(
                resultado.get("cuerpo"),
                "Encargo enc_x. Revision independiente aprobada.",
            )


if __name__ == "__main__":
    unittest.main()
