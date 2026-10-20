"""Clasificación de fallas por proveedor. Recorre `tests/datos/fallas/<proveedor>-<caso>.txt`: cada archivo
es la salida real que dejó ese CLI (sin editar, salvo recortar un `--help` largo), y el nombre dice qué
debe clasificarse. `<caso>` es el kind (auth, cuota, rate_limit, modelo_no_disponible, transitorio, falla),
con un número si hay varias variantes (`claude-auth2.txt`). El origen de cada captura está en ORIGEN.md."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from motor import machine as m
from motor.fallas import PROVEEDORES, clasifica, retry_after_s

DATOS = Path(__file__).parent / "datos" / "fallas"
KINDS = {"auth", "cuota", "rate_limit", "modelo_no_disponible", "transitorio", "falla"}
NOMBRE = re.compile(r"^(?P<proveedor>[a-z]+)-(?P<kind>[a-z_]+?)\d*\.txt$")


def casos() -> list[tuple[str, str, Path]]:
    out = []
    for f in sorted(DATOS.glob("*.txt")):
        n = NOMBRE.match(f.name)
        assert n, (
            f"nombre de caso inválido: {f.name} (se espera <proveedor>-<caso>.txt)"
        )
        out.append((n["proveedor"], n["kind"], f))
    return out


class SalidasRealesTest(unittest.TestCase):
    def test_cada_salida_real_se_clasifica_como_dice_su_nombre(self):
        hechos = 0
        for proveedor, kind, f in casos():
            with self.subTest(caso=f.name):
                self.assertIn(proveedor, PROVEEDORES)
                self.assertIn(kind, KINDS, f.name)
                got = clasifica(proveedor, f.read_text())
                self.assertEqual(got["kind"], kind, f"{f.name}: {got}")
                hechos += 1
        self.assertGreaterEqual(hechos, 15)

    def test_hay_al_menos_una_salida_por_proveedor_y_un_caso_de_auth_cada_uno(self):
        por = {}
        for proveedor, kind, _ in casos():
            por.setdefault(proveedor, set()).add(kind)
        self.assertEqual(
            set(por), set(PROVEEDORES), "falta un proveedor en tests/datos/fallas"
        )
        for proveedor in ("claude", "kimi", "grok", "muse", "qwen", "deepseek"):
            self.assertIn("auth", por[proveedor], proveedor)

    def test_el_origen_de_cada_captura_esta_documentado(self):
        origen = (DATOS / "ORIGEN.md").read_text()
        for _, _, f in casos():
            self.assertIn(f.name, origen, f"{f.name} sin origen en ORIGEN.md")

    def test_el_motor_clasifica_igual_por_machine_classify(self):
        for proveedor, kind, f in casos():
            with self.subTest(caso=f.name):
                self.assertEqual(
                    m.classify(1, False, False, f.read_text(), proveedor)["kind"], kind
                )

    def test_una_salida_real_clasificada_con_otro_proveedor_no_cambia_lo_universal(
        self,
    ):
        # el patrón común sigue valiendo si el proveedor no se conoce
        for archivo in (
            "claude-cuota.txt",
            "claude-rate_limit.txt",
            "claude-transitorio.txt",
        ):
            kind = archivo.split("-", 1)[1].removesuffix(".txt")
            self.assertEqual(
                clasifica("", (DATOS / archivo).read_text())["kind"], kind, archivo
            )


class DetallesTest(unittest.TestCase):
    def test_la_cuota_trae_cuando_se_reinicia(self):
        c = clasifica("claude", (DATOS / "claude-cuota.txt").read_text())
        self.assertEqual(c["reset_at"], "2026-10-23T09:00:00Z")

    def test_el_freno_corto_trae_cuanto_esperar(self):
        self.assertEqual(
            clasifica(
                "opencode",
                "429 Output token rate limit exceeded. Please retry after 20 seconds.",
            )["retry_after_s"],
            20.0,
        )

    def test_un_freno_que_nombra_el_plan_es_cuota(self):
        self.assertEqual(
            clasifica(
                "opencode", "Rate limit exceeded for your plan. Resets at 3:00 PM."
            )["kind"],
            "cuota",
        )
        self.assertEqual(
            clasifica("opencode", "429 Too Many Requests: limit resets in 5h 12m")[
                "kind"
            ],
            "cuota",
        )

    def test_un_reinicio_de_segundos_sigue_siendo_freno(self):
        self.assertEqual(
            clasifica("opencode", "Rate limit exceeded, resets in 30 seconds")["kind"],
            "rate_limit",
        )

    def test_kimi_envuelve_su_limite_semanal_como_auth_y_es_cuota(self):
        txt = "provider.auth_error 403: You've reached your weekly (7-day) usage limit."
        self.assertEqual(clasifica("kimi", txt)["kind"], "cuota")

    def test_el_codigo_suelto_en_la_salida_del_modelo_no_es_un_codigo(self):
        self.assertEqual(
            clasifica(
                "claude", "The mobile nav uses a 400 breakpoint across 429 items."
            )["kind"],
            "falla",
        )

    def test_el_codigo_suelto_en_stderr_si_cuenta(self):
        self.assertEqual(clasifica("opencode", "", "Error: 429")["kind"], "rate_limit")
        self.assertEqual(clasifica("opencode", "", "Error: 401")["kind"], "auth")

    def test_un_codigo_con_pista_http_en_la_salida_si_cuenta(self):
        self.assertEqual(
            clasifica("opencode", "request failed with status code 429")["kind"],
            "rate_limit",
        )
        self.assertEqual(
            clasifica("opencode", "Error: 401 Unauthorized")["kind"], "auth"
        )

    def test_un_modelo_deshabilitado_no_es_un_problema_de_credenciales(self):
        self.assertEqual(
            clasifica("opencode", '{"message":"Model is disabled","statusCode":401}')[
                "kind"
            ],
            "modelo_no_disponible",
        )

    def test_saldo_agotado_es_cuota(self):
        self.assertEqual(
            clasifica("deepseek", "API Error: 402 Insufficient Balance")["kind"],
            "cuota",
        )

    def test_los_colores_de_la_terminal_no_estorban(self):
        self.assertEqual(
            clasifica(
                "opencode", "\x1b[91m\x1b[1mError: \x1b[0mModel unavailable: x/y"
            )["kind"],
            "modelo_no_disponible",
        )

    def test_sin_pistas_es_falla_con_el_final_de_lo_que_dijo(self):
        c = clasifica("grok", "algo raro " * 100)
        self.assertEqual(c["kind"], "falla")
        self.assertLessEqual(len(c["detalle"]), 300)

    def test_retry_after_lee_unidades_y_descarta_lo_ilegible(self):
        for txt, esperado in (
            ("retry after 30 seconds", 30.0),
            ("try again in 1.5s", 1.5),
            ("retry-after: 2 minutes", 120.0),
            ("retry after 500ms", 0.5),
            ("retry after 4 hours", 14400.0),
            ("retry-after: 45;", 45.0),
            ("retry after 3 attempts", None),
            ("sin pista", None),
        ):
            self.assertEqual(retry_after_s(txt), esperado, txt)


if __name__ == "__main__":
    unittest.main()
