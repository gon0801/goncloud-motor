"""Los avisos dicen lo que pasó: el relevo agotado no promete un cambio de
modelo ni una continuación que no hubo, y el rojo del check deepseek-sin-abiertos
nombra los hallazgos con la vigencia real de su descarte.

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

F9 = {
    "id": "F9",
    "gravedad": "bloqueante",
    "criterio": "operativo",
    "titulo": "El relevo revienta cuando la fase bloqueada deja de ser la actual",
    "ancla": "dominio.py:1387",
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


def _encargo_con_relevo(
    reg: Registro, modelos: tuple[tuple[str, str], ...], agotado: bool = False
):
    """Un encargo con un escalón de relevo y los intentos que de verdad
    corrieron: `modelos` son pares (agente, modelo) en orden. `agotado` deja
    el pendiente con el productor real (`marcar_relevo_agotado`), que es el
    único registro que demuestra que el relevo se agotó."""
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
    enc = reg.fold(ident)
    if agotado:
        enc = ciclo.marcar_relevo_agotado(reg, enc, "responsable", HORA)
    return enc


class ElAvisoDelRelevo(unittest.TestCase):
    def test_agotado_sin_otro_modelo_no_promete_cambio_y_lo_dice_en_llano(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                enc = _encargo_con_relevo(
                    reg, (("opencode", GLM),), agotado=True
                )

                cuerpo = directores._cuerpo_de_cierre(enc)
                self.assertNotIn("Se cambió de modelo", cuerpo)
                self.assertIn("no había otro modelo autorizado para seguir", cuerpo)
                self.assertNotIn("continuó con el mismo modelo", cuerpo)

                hechos = directores.hechos_de_avance(enc)
                relevo = [h for h in hechos if h["tipo"] == "relevo"]
                self.assertEqual(len(relevo), 1)
                self.assertNotIn("Cambié de modelo", relevo[0]["texto"])
                self.assertNotIn("continué con el mismo", relevo[0]["texto"])
                self.assertIn(
                    "no había otro modelo autorizado para seguir",
                    relevo[0]["texto"],
                )
            finally:
                reg.close()

    def test_un_relevo_pendiente_no_afirma_agotamiento_ni_continuacion(self):
        # El escalón existe y el mismo modelo volvió a correr: sin el pendiente
        # del agotamiento, el aviso no dice que se agotó ni que continuó nada.
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                enc = _encargo_con_relevo(reg, (("opencode", GLM),))

                cuerpo = directores._cuerpo_de_cierre(enc)
                self.assertNotIn("Se cambió de modelo", cuerpo)
                self.assertNotIn("no había otro modelo autorizado", cuerpo)
                self.assertNotIn("continuó con el mismo modelo", cuerpo)

                hechos = directores.hechos_de_avance(enc)
                self.assertEqual(
                    [h for h in hechos if h["tipo"] == "relevo"], []
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

    def test_el_agotamiento_sobrevive_cuando_otra_fase_queda_activa(self):
        # El relevo de la fase A se agotó y la fase quedó bloqueada con su
        # pendiente; el encargo sigue con la fase B. El agotamiento está
        # registrado en la fase bloqueada y el aviso no lo pierde por dejar
        # de ser la fase actual.
        from dataclasses import replace as dc_replace

        from motor.encargos.dominio import Fase, relevo_agotado

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                ident = recibir(
                    reg, "termina el pedido", "relevo-dos-fases", HORA,
                    repo="ejemplo/repo", base_rama="main",
                )["encargo"]
                reg.anotar(
                    ident,
                    {
                        "kind": "sin_progreso",
                        "data": {
                            "huella": "huella-A", "escalar": True,
                            "rol": "responsable", "hora": HORA,
                        },
                    },
                    "relevo-A",
                )
                ciclo.marcar_relevo_agotado(
                    reg, reg.fold(ident), "responsable", HORA
                )
                reg.anotar(
                    ident,
                    {
                        "kind": "bloqueo_fase",
                        "data": {
                            "causa": "sin producto que entregar",
                            "evidencia": "no hay producto", "hora": HORA,
                        },
                    },
                    "bloqueo-A",
                )
                e = reg.fold(ident)
                bloqueada = e.fase_actual
                self.assertTrue(bloqueada.pendientes)
                siguiente = Fase(id="B", resultado="fase B", estado="pendiente")
                e = dc_replace(
                    e, fases=(bloqueada, siguiente), fase_activa="B"
                )
                self.assertNotEqual(e.fase_actual.id, bloqueada.id)
                self.assertTrue(
                    relevo_agotado(e),
                    "el agotamiento quedó en la fase bloqueada y el aviso lo pierde",
                )
                cuerpo = directores._cuerpo_de_cierre(e)
                self.assertIn("no había otro modelo autorizado para seguir", cuerpo)
            finally:
                reg.close()


SHA_ENSAYO = "a" * 40


class ElAvisoDelCheckDeepseek(unittest.TestCase):
    """El encargo vive solo de eventos del registro, como el incidente: la
    fase quedó bloqueada (terminado con entrega parcial), DeepSeek mantiene F3
    abierto en el registro y el check deepseek-sin-abiertos salió rojo."""

    def _con_f3_abierto(
        self, tmp, clave: str, descarte_sha: str = "",
        f3_resuelto: bool = False, corridas: dict | None = None,
        logs: dict | None = None, f9_descartado: bool = False,
    ):
        config = {
            "corridas": corridas or {"deepseek-sin-abiertos": "failure"},
            "logs_job": logs
            or {"deepseek-sin-abiertos": "estado=falla nota=hay hallazgos"},
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
                    "hallazgos": [F3] + ([F9] if f9_descartado else []),
                    "hora": HORA,
                },
            },
            "revision-ds-ensayo",
        )
        if descarte_sha:
            reg.anotar(
                ident,
                {
                    "kind": "excepcion",
                    "data": {
                        "tipo": "descarte", "sha": descarte_sha, "id": "F3",
                        "motivo": "el candado del repo da sin errores nuevos",
                        "hora": HORA,
                    },
                },
                "excepcion-descarte-ensayo",
            )
        if f9_descartado:
            reg.anotar(
                ident,
                {
                    "kind": "excepcion",
                    "data": {
                        "tipo": "descarte", "sha": SHA_ENSAYO, "id": "F9",
                        "motivo": "el código que señala no existe",
                        "hora": HORA,
                    },
                },
                "excepcion-descarte-f9",
            )
        if f9_descartado or f3_resuelto:
            # El cierre del descarte real: la revisión independiente deja los
            # hallazgos resueltos con su evidencia, y dejan de contar.
            resueltos = []
            if f9_descartado:
                resueltos.append(dict(
                    F9, estado="resuelto",
                    cierre_evidencia="el código que señala no existe",
                ))
            if f3_resuelto:
                resueltos.append(dict(
                    F3, estado="resuelto",
                    cierre_evidencia="el candado del repo da sin errores nuevos",
                ))
            reg.anotar(
                ident,
                {
                    "kind": "revision",
                    "data": {
                        "fase": "", "veredicto": "aprobado", "revisor": "glm",
                        "origen": "independiente", "version": "",
                        "alcance": "completa", "cubre": [],
                        "hallazgos": resueltos, "hora": HORA,
                    },
                },
                "revision-ind-ensayo",
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
            reg, gh, e = self._con_f3_abierto(tmp, "check-ds-abierto")
            texto = self._aviso(reg, gh, e)
            self.assertNotIn("no puedo saber con certeza", texto)
            self.assertIn("F3", texto)
            self.assertIn(TITULO_F3, texto)
            self.assertIn("nadie lo ha descartado", texto)
            # La fase quedó bloqueada: el aviso no promete una devolución al
            # responsable que no va a pasar.
            self.assertNotIn("vuelve al responsable", texto)
            self.assertIn("el PR no se integra", texto)

    def test_hallazgo_descartado_y_otro_abierto_no_recomienda_integrar(self):
        # F3 sigue abierto y contando; F9 quedó descartado en el SHA vigente.
        # El aviso nombra los dos y no recomienda integrar con un bloqueante
        # sin cubrir.
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(
                tmp, "check-ds-mixto-vivo", f9_descartado=True
            )
            texto = self._aviso(reg, gh, e)
            self.assertIn("F3", texto)
            self.assertIn("nadie lo ha descartado", texto)
            self.assertIn("F9", texto)
            self.assertIn("ya no cuenta", texto)
            self.assertNotIn("antes de integrarlo", texto)
            self.assertIn("el PR no se integra", texto)

    def test_un_rojo_del_check_dice_si_el_revisor_lo_descarto(self):
        # El descarte vive en el SHA vigente: el aviso lo dice, no lo niega.
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(
                tmp, "check-ds-descartado", descarte_sha=SHA_ENSAYO
            )
            texto = self._aviso(reg, gh, e)
            self.assertNotIn("no puedo saber con certeza", texto)
            self.assertIn("F3", texto)
            self.assertIn("el revisor independiente lo descartó", texto)
            self.assertIn("cubre el SHA vigente", texto)
            self.assertNotIn("no cubre el SHA vigente", texto)

    def test_un_descarte_en_otro_sha_queda_caducado(self):
        # El descarte anterior vive en otro SHA y el hallazgo sigue abierto:
        # el aviso lo dice en llano y no recomienda integrar.
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(
                tmp, "check-ds-caducado", descarte_sha="b" * 40
            )
            texto = self._aviso(reg, gh, e)
            self.assertIn("lo descartó antes, pero el descarte no cubre el SHA vigente", texto)
            self.assertNotIn("cubre el SHA vigente. El descarte", texto)
            self.assertIn("el PR no se integra", texto)
            self.assertNotIn("vuelve al responsable", texto)

    def test_un_rojo_del_check_nombra_el_descartado_vigente(self):
        # F3 ya no cuenta (el descarte real lo dejó resuelto con evidencia):
        # el aviso lo nombra con id, título y vigencia, no dice que no hay
        # hallazgos.
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(
                tmp, "check-ds-resuelto", descarte_sha=SHA_ENSAYO,
                f3_resuelto=True,
            )
            texto = self._aviso(reg, gh, e)
            self.assertNotIn("no puedo saber con certeza", texto)
            self.assertIn("F3", texto)
            self.assertIn(TITULO_F3, texto)
            self.assertIn("el revisor independiente lo descartó", texto)
            self.assertIn("cubre el SHA vigente", texto)
            self.assertNotIn(
                "no tiene hallazgos de DeepSeek abiertos", texto
            )

    def test_checks_mixtos_conservan_el_detalle_de_deepseek(self):
        # El check de DeepSeek y otro check en rojo: el aviso conserva el
        # detalle conocido de DeepSeek (F3 y su título) junto a la explicación
        # del otro check, sin caer en el "no puedo saber" global.
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(
                tmp,
                "check-ds-mixto",
                corridas={
                    "deepseek-sin-abiertos": "failure",
                    "otro-check": "failure",
                },
                logs={
                    "deepseek-sin-abiertos": "estado=falla nota=hay hallazgos",
                    "otro-check": "fallo sin clasificar",
                },
            )
            texto = self._aviso(reg, gh, e)
            self.assertIn("F3", texto)
            self.assertIn(TITULO_F3, texto)
            self.assertIn("DeepSeek mantiene estos hallazgos", texto)
            self.assertIn("«otro-check»", texto)

    def test_con_otro_check_en_rojo_no_recomienda_integrar(self):
        # deepseek-sin-abiertos y otro-check (tubería) en rojo: mientras el
        # registro tiene un bloqueante de DeepSeek que cuenta, ninguna parte
        # del aviso recomienda integrar; el diagnóstico de la tubería se
        # conserva sin la recomendación de merge.
        with tempfile.TemporaryDirectory() as tmp:
            reg, gh, e = self._con_f3_abierto(
                tmp,
                "ds-y-timeout",
                corridas={
                    "deepseek-sin-abiertos": "failure",
                    "otro-check": "timed_out",
                },
                logs={
                    "deepseek-sin-abiertos": "estado=falla nota=hay hallazgos",
                    "otro-check": "The job has timed out",
                },
            )
            texto = self._aviso(reg, gh, e)
            self.assertIn("F3", texto)
            self.assertIn(TITULO_F3, texto)
            self.assertIn("nadie lo ha descartado", texto)
            self.assertIn("la tubería", texto.lower())
            self.assertNotIn("recomiendo mergear", texto)


if __name__ == "__main__":
    unittest.main()
