"""C.2: `ops/pstack/recorta_registro.py` limpia toda forma de secreto y `--comprueba` compara uno por uno."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops" / "pstack"))
import recorta_registro as rr  # noqa: E402

CAMPOS = ("command", "description", "file_path", "subagent_type", "model")


def _claude(*llamadas: tuple[str, dict]) -> str:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": n, "input": a} for n, a in llamadas
                    ]
                },
            }
        )
        + "\n"
    )


def _comprueba(d: Path) -> tuple[int, str]:
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        rc = rr.comprueba(d)
    return rc, salida.getvalue()


# Ejemplos escritos aquí, armados por partes e independientes de `rr.SECRETOS`: si alguien
# quita una forma o la cambia de largo, esta prueba lo ve. (ejemplo, es_secreto)
EJEMPLOS = (
    ("ghp" + "_" + "a1B2" * 9, True),
    ("github" + "_pat_" + "a1B2" * 9, True),
    ("123456789" + ":" + "aB3_-" * 7, True),
    ("1234567890" + ":" + "aB3_-" * 7, True),
    ("12345678" + ":" + "aB3_-" * 7, True),
    ("sk" + "-" + "a" * 16, True),
    ("sk" + "-" + "a" * 15, False),
    ("task" + "-" + "a" * 16, False),
    ("AKIA" + "ABCD1234" * 2, True),
    ("-----" + "BEGIN PRIVATE KEY", True),
)


class TestSecretos(unittest.TestCase):
    def test_cada_forma_de_secreto_sale_de_cada_campo_que_se_guarda(self):
        for ejemplo, es_secreto in EJEMPLOS:
            for campo in CAMPOS:
                with self.subTest(ejemplo=ejemplo, campo=campo):
                    registro = _claude(("Agent", {campo: f"antes {ejemplo} después"}))
                    salida = rr.recorta(registro, "claude")
                    self.assertEqual(ejemplo not in salida, es_secreto)
                    self.assertEqual("<secreto>" in salida, es_secreto)
                    self.assertIn("Agent", salida, "la llamada se conserva")

    def test_los_ejemplos_de_la_lista_cumplen_su_forma(self):
        for patron, ejemplo in rr.SECRETOS:
            self.assertRegex(ejemplo, patron)

    def test_una_clave_pegada_a_otra_sale_entera(self):
        pegadas = "AKIA" + "ABCD1234" * 2 + "sk" + "-" + "a" * 16
        salida = rr.recorta(_claude(("Bash", {"command": pegadas})), "claude")
        self.assertNotIn("sk" + "-", salida)
        self.assertNotIn("AKIA", salida)

    def test_la_limpieza_entra_en_listas_y_dicts(self):
        ejemplo = "ghp" + "_" + "a1B2" * 9
        limpio = rr.limpia({"a": [ejemplo, {"b": ejemplo}]})
        self.assertEqual(limpio, {"a": ["<secreto>", {"b": "<secreto>"}]})

    def test_task_notification_no_es_un_secreto(self):
        salida = rr.recorta(
            _claude(("Bash", {"command": "echo <task-notification-abcdefghijklmnop>"})),
            "claude",
        )
        self.assertIn("task-notification-abcdefghijklmnop", salida)

    def test_el_comando_se_limpia_antes_de_cortarlo(self):
        telegram = "123456789" + ":" + "aB3_-" * 7
        comando = "x" * (rr.TOPE_COMANDO - 6) + telegram
        salida = rr.recorta(_claude(("Bash", {"command": comando})), "claude")
        self.assertNotIn("123456", salida)

    def test_items_queda_como_una_lista_de_vacios(self):
        salida = rr.recorta(_claude(("Agent", {"items": ["a", "b", "c"]})), "claude")
        entrada = json.loads(salida)["message"]["content"][0]["input"]
        self.assertEqual(entrada["items"], [{}, {}, {}])


class TestComprueba(unittest.TestCase):
    def _dir(self, original: str, recorte: str, origen: Path | str | None = None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        orig = d / "original.jsonl"
        orig.write_text(original, encoding="utf-8")
        (d / "r.jsonl").write_text(recorte, encoding="utf-8")
        (d / "fuentes.json").write_text(
            json.dumps(
                [{"salida": "r.jsonl", "cli": "claude", "origen": str(origen or orig)}]
            ),
            encoding="utf-8",
        )
        return d

    def _dos_agentes(self, a: str, b: str) -> str:
        return _claude(
            ("Agent", {"subagent_type": "x", "description": a}),
            ("Agent", {"subagent_type": "x", "description": b}),
        )

    def test_un_recorte_fiel_cuadra(self):
        original = self._dos_agentes("uno", "dos")
        d = self._dir(original, rr.recorta(original, "claude"))
        self.assertEqual(_comprueba(d)[0], 0)

    def test_un_subagente_cambiado_se_detecta_aunque_el_conteo_sea_el_mismo(self):
        original = self._dos_agentes("uno", "dos")
        cambiado = self._dos_agentes("uno", "otro")
        d = self._dir(original, rr.recorta(cambiado, "claude"))
        rc, texto = _comprueba(d)
        self.assertEqual(rc, 1)
        self.assertIn("r.jsonl", texto)

    def test_la_herramienta_cambiada_se_detecta(self):
        original = _claude(("Agent", {"description": "uno"}))
        cambiado = _claude(("Task", {"description": "uno"}))
        d = self._dir(original, rr.recorta(cambiado, "claude"))
        self.assertEqual(_comprueba(d)[0], 1)

    def test_un_original_que_falta_sale_distinto_de_cero_y_se_nombra(self):
        original = self._dos_agentes("uno", "dos")
        d = self._dir(
            original, rr.recorta(original, "claude"), origen="/no/existe.jsonl"
        )
        rc, texto = _comprueba(d)
        self.assertNotEqual(rc, 0)
        self.assertIn("/no/existe.jsonl", texto)

    def test_un_recorte_que_falta_se_nombra_y_sigue_con_los_demas(self):
        original = self._dos_agentes("uno", "dos")
        d = self._dir(original, rr.recorta(original, "claude"))
        (d / "r.jsonl").unlink()
        rc, texto = _comprueba(d)
        self.assertEqual(rc, 1)
        self.assertIn("r.jsonl", texto)

    def test_una_ruta_con_tilde_se_lee(self):
        original = self._dos_agentes("uno", "dos")
        with tempfile.TemporaryDirectory() as home:
            Path(home, "reg.jsonl").write_text(original, encoding="utf-8")
            d = self._dir(original, rr.recorta(original, "claude"), "~/reg.jsonl")
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
                rc, texto = _comprueba(d)
        self.assertEqual(rc, 0, texto)

    def test_el_original_con_secreto_cuadra_con_su_recorte_limpio(self):
        ejemplo = "ghp" + "_" + "a1B2" * 9
        original = _claude(("Agent", {"description": f"usa {ejemplo}"}))
        d = self._dir(original, rr.recorta(original, "claude"))
        self.assertEqual(_comprueba(d)[0], 0)


if __name__ == "__main__":
    unittest.main()
