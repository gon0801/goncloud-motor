from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos.ciclo import (
    _cuerpo_pr,
    _leer_cuerpo_del_espacio,
    _paso_entrega,
    ahora_iso,
)
from motor.encargos.comprobacion import instrucciones_responsable
from motor.github import FakeGitHub, GhGitHub
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


class _GhFallaCuerpo(FakeGitHub):
    """El primer intento de actualizar el cuerpo falla, como un error de `gh`."""

    fallar = True

    def actualizar_cuerpo(self, repo, numero, cuerpo):
        if self.fallar:
            raise RuntimeError("gh pr edit: fallo simulado")
        return super().actualizar_cuerpo(repo, numero, cuerpo)


class _GhCuentaCuerpo(FakeGitHub):
    def __init__(self, d):
        super().__init__(d)
        self.actualizaciones = 0

    def actualizar_cuerpo(self, repo, numero, cuerpo):
        self.actualizaciones += 1
        return super().actualizar_cuerpo(repo, numero, cuerpo)


class _GhCapturaCuerpo(GhGitHub):
    """Captura el cuerpo que `gh` recibe por archivo, sin salir a la red."""

    def __init__(self):
        super().__init__()
        self.llamadas: list[tuple] = []
        self.cuerpo_recibido: str | None = None
        self.ruta_cuerpo: str | None = None

    def nombre(self, repo):
        return "o/r"

    def _gh(self, *a):
        self.llamadas.append(a)
        if a[:2] == ("pr", "list"):
            return "[]"
        if a[:2] in (("pr", "create"), ("pr", "edit")):
            i = a.index("--body-file")
            self.ruta_cuerpo = a[i + 1]
            self.cuerpo_recibido = Path(self.ruta_cuerpo).read_text(encoding="utf-8")
            return "https://github.com/o/r/pull/7" if a[:2] == ("pr", "create") else ""
        if a[:2] == ("pr", "view"):
            return json.dumps(
                {
                    "number": 7,
                    "headRefName": "f",
                    "baseRefName": "main",
                    "isDraft": True,
                    "headRefOid": "a" * 40,
                    "state": "OPEN",
                    "mergeCommit": None,
                }
            )
        return ""


class _GhFallaCreate(_GhCapturaCuerpo):
    def _gh(self, *a):
        if a[:2] == ("pr", "create"):
            self.llamadas.append(a)
            self.ruta_cuerpo = a[a.index("--body-file") + 1]
            raise RuntimeError("gh pr create: fallo simulado")
        return super()._gh(*a)


class CuerpoPorArchivo(unittest.TestCase):
    def test_gh_crea_el_pr_con_el_cuerpo_en_archivo_no_en_argv(self):
        gh = _GhCapturaCuerpo()
        cuerpo = "😀" * 65_000

        gh.abrir_pr("o/r", "f", "main", "t", cuerpo)

        self.assertEqual(gh.cuerpo_recibido, cuerpo)
        self.assertFalse(Path(gh.ruta_cuerpo).exists())
        creacion = next(a for a in gh.llamadas if a[:2] == ("pr", "create"))
        self.assertIn("--body-file", creacion)
        self.assertNotIn("--body", creacion)
        self.assertNotIn(cuerpo, creacion)

    def test_gh_actualiza_el_cuerpo_en_archivo_no_en_argv(self):
        gh = _GhCapturaCuerpo()
        cuerpo = "á" * 65_000

        gh.actualizar_cuerpo("o/r", 7, cuerpo)

        self.assertEqual(gh.cuerpo_recibido, cuerpo)
        self.assertFalse(Path(gh.ruta_cuerpo).exists())
        edicion = next(a for a in gh.llamadas if a[:2] == ("pr", "edit"))
        self.assertIn("--body-file", edicion)
        self.assertNotIn("--body", edicion)
        self.assertNotIn(cuerpo, edicion)

    def test_el_temporal_se_borra_si_gh_falla(self):
        gh = _GhFallaCreate()

        with self.assertRaises(RuntimeError):
            gh.abrir_pr("o/r", "f", "main", "t", "cuerpo")

        self.assertIsNotNone(gh.ruta_cuerpo)
        self.assertFalse(Path(gh.ruta_cuerpo).exists())


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
            self.assertIn("fuera del PR", cuerpo)
            self.assertTrue(
                cuerpo.endswith(f"Encargo {r.enc}. Fase: {e.fase_activa}.")
            )

    def test_texto_multibyte_se_recorta_tambien_por_bytes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-multibyte", FakeGitHub)
            (_interno(r) / "cuerpo-pr.md").write_text(
                "😀" * 65_000, encoding="utf-8"
            )

            e = vuelta("hola")

            cuerpo = _pr_unico(vuelta.gh).get("cuerpo") or ""
            self.assertLessEqual(len(cuerpo), LIMITE_GITHUB)
            self.assertLessEqual(len(cuerpo.encode("utf-8")), LIMITE_GITHUB)
            self.assertTrue(cuerpo.startswith("😀"))
            self.assertIn(".interno/cuerpo-pr.md", cuerpo)
            self.assertTrue(
                cuerpo.endswith(f"Encargo {r.enc}. Fase: {e.fase_activa}.")
            )

    def test_texto_con_nul_se_sanea_antes_de_publicar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-nul", FakeGitHub)
            (_interno(r) / "cuerpo-pr.md").write_text(
                "# Cambios\n\x00oculto", encoding="utf-8"
            )

            vuelta("hola")

            cuerpo = _pr_unico(vuelta.gh).get("cuerpo") or ""
            self.assertNotIn("\x00", cuerpo)
            self.assertTrue(cuerpo.startswith("# Cambios\noculto"))

    def test_la_lectura_se_acota_al_limite_mas_uno(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp)
            interno = espacio / ".interno"
            interno.mkdir()
            (interno / "cuerpo-pr.md").write_text(
                "x" * (LIMITE_GITHUB + 10_000), encoding="utf-8"
            )

            texto = _leer_cuerpo_del_espacio(espacio)

            self.assertIsNotNone(texto)
            self.assertLessEqual(len(texto or ""), LIMITE_GITHUB + 1)

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


class CuerpoDelPrAlRepublicar(unittest.TestCase):
    def test_pr_existente_recibe_el_cuerpo_actualizado_al_publicar_otra_version(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-republica", FakeGitHub)
            (_interno(r) / "cuerpo-pr.md").write_text(
                "primera version", encoding="utf-8"
            )
            vuelta("uno")
            primer_pr = _pr_unico(vuelta.gh)
            primero = primer_pr.get("cuerpo")

            (_interno(r) / "cuerpo-pr.md").write_text(
                "segunda version", encoding="utf-8"
            )
            e = vuelta("dos")

            segundo_pr = _pr_unico(vuelta.gh)
            segundo = segundo_pr.get("cuerpo")
            self.assertEqual(segundo_pr["numero"], primer_pr["numero"])
            self.assertNotEqual(primero, segundo)
            self.assertEqual(
                segundo, f"segunda version\n\nEncargo {r.enc}. Fase: {e.fase_activa}."
            )

    def test_un_pr_abierto_por_fuera_recibe_el_cuerpo_de_la_primera_publicacion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "cuerpo-recupera", FakeGitHub)
            e0 = r.fold()
            viejo = vuelta.gh.abrir_pr(
                e0.entrega.repo, e0.entrega.rama, "main", "t", "cuerpo viejo"
            )
            (_interno(r) / "cuerpo-pr.md").write_text(
                "contenido", encoding="utf-8"
            )

            e = vuelta("hola")

            pr = _pr_unico(vuelta.gh)
            self.assertEqual(pr["numero"], viejo.numero)
            self.assertEqual(
                pr.get("cuerpo"),
                f"contenido\n\nEncargo {r.enc}. Fase: {e.fase_activa}.",
            )

    def test_actualiza_una_vez_por_version_y_no_repite_el_mismo_sha(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "cuerpo-idempotente", _GhCuentaCuerpo
            )
            (_interno(r) / "cuerpo-pr.md").write_text(
                "primera version", encoding="utf-8"
            )
            vuelta("uno")
            self.assertEqual(vuelta.gh.actualizaciones, 1)

            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertEqual(vuelta.gh.actualizaciones, 1)

            (_interno(r) / "cuerpo-pr.md").write_text(
                "segunda version", encoding="utf-8"
            )
            vuelta("dos")
            self.assertEqual(vuelta.gh.actualizaciones, 2)

            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertEqual(vuelta.gh.actualizaciones, 2)

    def test_fallo_al_actualizar_no_detiene_y_se_reintenta(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "cuerpo-republica-fallo", _GhFallaCuerpo
            )
            (_interno(r) / "cuerpo-pr.md").write_text(
                "primera version", encoding="utf-8"
            )
            vuelta("uno")

            (_interno(r) / "cuerpo-pr.md").write_text(
                "segunda version", encoding="utf-8"
            )
            e = vuelta("dos")

            self.assertTrue(e.pr_numero)
            fallos = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "instrumento_fallo"
                and "cuerpo" in str(ev["data"].get("causa") or "")
            ]
            self.assertTrue(fallos)

            vuelta.gh.fallar = False
            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            self.assertEqual(
                _pr_unico(vuelta.gh).get("cuerpo"),
                f"segunda version\n\nEncargo {r.enc}. Fase: {e.fase_activa}.",
            )


class InstruccionesDelResponsable(unittest.TestCase):
    def test_piden_escribir_el_cuerpo_del_pr(self):
        texto = instrucciones_responsable(Path("/espacio/.interno"))

        self.assertIn("cuerpo-pr.md", texto)
        self.assertIn("rojo", texto)
        self.assertIn("verde", texto)


if __name__ == "__main__":
    unittest.main()
