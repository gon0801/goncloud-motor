"""Los avisos dicen lo que pasó: el relevo agotado no promete un cambio de
modelo que no hubo, y el rojo del check deepseek-sin-abiertos nombra los
hallazgos abiertos y su descarte.

Caso real (enc_fd795218afa32147, PR #442): el aviso final dijo "Se cambió de
modelo" cuando el relevo no encontró otro modelo autorizado, y el aviso del
check rojo dijo "no puedo saber con certeza si es del código del cambio o de
la tubería" cuando la causa estaba en el registro (F3 abierto en DeepSeek).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import recibir
from motor.encargos.registro import Registro
from motor.github import FakeGitHub
from motor.integraciones import directores

HORA = "2026-11-11T12:00:00Z"
GLM = "zai-coding-plan/glm-5.3-flash"
OTRO = "opencode-go/deepseek-v4.1-flash"

TITULO_F3 = "Tres returns nuevos exceden la línea base de pyright"

F3 = {
    "id": "F3",
    "gravedad": "bloqueante",
    "criterio": "operativo",
    "titulo": TITULO_F3,
    "ancla": "saludo.txt:1",
    "origen": "deepseek",
    "estado": "abierto",
}


def setUpModule():
    # Estas pruebas no ejercen la jaula: la sonda se da por buena también donde
    # sandbox-exec existe pero no se puede anidar. El parche se retira al
    # terminar el módulo para no ensuciar al resto del banco.
    parche = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    parche.start()
    globals()["_PARCHE_SONDA"] = parche


def tearDownModule():
    parche = globals().pop("_PARCHE_SONDA", None)
    if parche is not None:
        parche.stop()


def _encargo_con_relevo(reg: Registro, modelos: tuple[tuple[str, str], ...]):
    """Un encargo con un escalón de relevo y los intentos que de verdad
    corrieron: `modelos` son pares (agente, modelo) en orden."""
    ident = recibir(
        reg, "termina el pedido", "relevo-aviso", HORA,
        repo="ejemplo/repo", base_rama="main",
    )["encargo"]
    reg.anotar(
        ident,
        {
            "kind": "entrega_externa",
            "data": {"paso": "pr_listo", "numero": 9, "hora": HORA},
        },
        "pr-listo",
    )
    for indice, (agente, modelo) in enumerate(modelos):
        reg.anotar(
            ident,
            {
                "kind": "reserva",
                "data": {"rol": "responsable", "generacion": indice + 1},
            },
            f"reserva-{indice}",
        )
        reg.anotar(
            ident,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": f"intento-{indice}", "pid": 1, "inicio": "ensayo",
                    "rol": "responsable", "agente": agente, "modelo": modelo,
                    "hora": HORA,
                },
            },
            f"abierto-{indice}",
        )
        reg.anotar(
            ident, {"kind": "intento_cerrado", "data": {"hora": HORA}},
            f"cerrado-{indice}",
        )
    reg.anotar(
        ident,
        {
            "kind": "sin_progreso",
            "data": {
                "huella": "huella-de-ensayo", "escalar": True,
                "rol": "responsable", "hora": HORA,
            },
        },
        "relevo-1",
    )
    return reg.fold(ident)


class ElAvisoDelRelevo(unittest.TestCase):
    def test_agotado_sin_otro_modelo_no_promete_cambio_y_lo_dice_en_llano(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                enc = _encargo_con_relevo(reg, (("opencode", GLM),))

                cuerpo = directores._cuerpo_de_cierre(enc)
                self.assertNotIn("Se cambió de modelo", cuerpo)
                self.assertIn("no había otro modelo autorizado para seguir", cuerpo)

                hechos = directores.hechos_de_avance(enc)
                relevo = [h for h in hechos if h["tipo"] == "relevo"]
                self.assertEqual(len(relevo), 1)
                self.assertNotIn("Cambié de modelo", relevo[0]["texto"])
                self.assertIn(
                    "no había otro modelo autorizado para seguir",
                    relevo[0]["texto"],
                )
            finally:
                reg.close()

    def test_un_relevo_con_otro_modelo_conserva_la_frase(self):  # no-roja-en-base: la base ya dice que se cambió de modelo cuando corrió otro modelo; la prueba lo conserva como guarda
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                enc = _encargo_con_relevo(
                    reg, (("opencode", GLM), ("opencode", OTRO))
                )

                cuerpo = directores._cuerpo_de_cierre(enc)
                self.assertIn(
                    "Se cambió de modelo para continuar sin esperar a nadie.",
                    cuerpo,
                )
                hechos = directores.hechos_de_avance(enc)
                relevo = [h for h in hechos if h["tipo"] == "relevo"]
                self.assertEqual(len(relevo), 1)
                self.assertIn("Cambié de modelo", relevo[0]["texto"])
            finally:
                reg.close()


SHA_ENSAYO = "a" * 40


class ElAvisoDelCheckDeepseek(unittest.TestCase):
    """El encargo vive solo de eventos del registro, como el incidente: la
    fase quedó bloqueada (terminado con entrega parcial), DeepSeek mantiene F3
    abierto en el registro y el check deepseek-sin-abiertos salió rojo."""

    def _con_f3_abierto(self, tmp, clave: str, descarte: bool):
        config = {
            "corridas": {"deepseek-sin-abiertos": "failure"},
            "logs_job": {
                "deepseek-sin-abiertos": "estado=falla nota=hay hallazgos"
            },
        }
        fake = Path(tmp) / "fake"
        fake.mkdir()
        (fake / "repos.json").write_text(json.dumps({"ejemplo/repo": config}))
        gh = FakeGitHub(fake)
        reg = Registro(Path(tmp) / "base")
        ident = recibir(
            reg, "termina el pedido", clave, HORA,
            repo="ejemplo/repo", base_rama="main", entrega_nivel="pr",
        )["encargo"]
        reg.anotar(
            ident,
            {
                "kind": "entrega_externa",
                "data": {
                    "paso": "push", "numero": 9, "sha": SHA_ENSAYO, "hora": HORA,
                },
            },
            "push-ensayo",
        )
        reg.anotar(
            ident,
            {
                "kind": "revision",
                "data": {
                    "fase": "", "veredicto": "cambios", "revisor": "deepseek",
                    "origen": "deepseek", "version": "", "alcance": "completa",
                    "hallazgos": [F3], "hora": HORA,
                },
            },
            "revision-ds-ensayo",
        )
        if descarte:
            reg.anotar(
                ident,
                {
                    "kind": "excepcion",
                    "data": {
                        "tipo": "descarte", "sha": SHA_ENSAYO, "id": "F3",
                        "motivo": "el candado del repo da sin errores nuevos",
                        "hora": HORA,
                    },
                },
                "excepcion-descarte-ensayo",
            )
        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "causa": "bloqueante abierto: " + TITULO_F3,
                    "evidencia": "el relevo agotó los modelos del responsable",
                    "hora": HORA,
                },
            },
            "bloqueo-ensayo",
        )
        return reg, gh, reg.fold(ident)

    def _aviso(self, reg, gh, e) -> str:
        try:
            e = ciclo._revisar_ci_del_pr(reg, e, gh, HORA)
            hechos = [
                h for h in directores.hechos_de_avance(e)
                if h["tipo"] == "ci_rojo"
            ]
            if not hechos:
                self.fail("el rojo del CI no dejó aviso")
            return hechos[0]["texto"]
        finally:
            reg.close()

    def test_un_rojo_del_check_nombra_el_hallazgo_abierto(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(tmp, "check-ds-abierto", False)
            texto = self._aviso(reg, gh, e)
            self.assertNotIn("no puedo saber con certeza", texto)
            self.assertIn("F3", texto)
            self.assertIn(TITULO_F3, texto)
            self.assertIn("nadie lo ha descartado", texto)

    def test_un_rojo_del_check_dice_si_el_revisor_lo_descarto(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(tmp, "check-ds-descartado", True)
            texto = self._aviso(reg, gh, e)
            self.assertNotIn("no puedo saber con certeza", texto)
            self.assertIn("F3", texto)
            self.assertIn("el revisor independiente lo descartó", texto)


if __name__ == "__main__":
    unittest.main()
