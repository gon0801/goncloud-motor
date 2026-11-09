"""Las decisiones de David en una sola tabla, en sus límites.

La tabla vive en `motor/encargos/politica.py`; `AGENTS.md` se genera desde
ella. Los topes que ya funcionaban se prueban como caracterizaciones. La
lectura de la sesión de un modelo y las restricciones estructuradas son el
arreglo: sus pruebas fallan en la base en la aserción que nombra el defecto.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import Hallazgo, bloqueantes_abiertos
from tests.dobles import github_por_numero
from tests.test_flujo_revisiones import _entrega_pr


def _politica():
    try:
        from motor.encargos import politica
    except ImportError as exc:  # noqa: BLE001
        raise AssertionError(
            "falta motor/encargos/politica.py: las decisiones no tienen una "
            "sola fuente"
        ) from exc
    return politica


def _lectura():
    try:
        from motor.encargos import lectura
    except ImportError as exc:  # noqa: BLE001
        raise AssertionError(
            "falta motor/encargos/lectura.py: la lectura no está separada de "
            "la decisión"
        ) from exc
    return lectura


def _ids(e) -> list[str]:
    return [h.id for h in bloqueantes_abiertos(e, "deepseek")]


def _terminado(caso, tmp: str, clave: str):
    vuelta, r = _entrega_pr(
        caso, tmp, clave, github_por_numero, nivel="integrado"
    )
    e = vuelta("uno")
    tarde = ahora_iso(ciclo._epoch_de_hora(e.hora) + 31 * 60)
    e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
    caso.assertEqual(e.estado, "terminado")
    vuelta.gh.llamadas.clear()
    return vuelta, r, e


class TablaUnica(unittest.TestCase):
    def test_agents_se_genera_desde_politica(self):
        politica = _politica()
        raiz = Path(__file__).resolve().parents[1]
        publicado = politica.tabla_de_agents(
            (raiz / "AGENTS.md").read_text(encoding="utf-8")
        )
        self.assertEqual(publicado, politica.tabla_markdown())

    def test_los_topes_no_viven_en_ciclo(self):
        politica = _politica()
        self.assertEqual(politica.TOPE_DEEPSEEK_MIN, 30)
        self.assertFalse(hasattr(ciclo, "_TOPE_DEEPSEEK_MIN"))
        self.assertFalse(hasattr(ciclo, "_SONDEO_CERRADOS_TOPE_POR_PASADA"))
        self.assertFalse(hasattr(ciclo, "_SONDEO_DEEPSEEK_VENTANA_S"))

    def test_un_hallazgo_que_pide_cambiar_la_regla_cita_la_fila(self):
        politica = _politica()
        h = Hallazgo(
            id="F1",
            gravedad="bloqueante",
            criterio="operativo",
            titulo="bajar el tope de DeepSeek de 30 a 15 minutos",
        )
        razon = politica.hallazgo_contra_politica(h)
        self.assertIn("regla del propietario", razon)
        self.assertIn("DeepSeek", razon)

    def test_un_hallazgo_que_dice_que_el_codigo_incumple_sigue_siendo_normal(self):
        politica = _politica()
        h = Hallazgo(
            id="F2",
            gravedad="bloqueante",
            criterio="comportamiento",
            titulo=(
                "el código no respeta el tope de DeepSeek de 30 min: hoy "
                "espera 60"
            ),
        )
        self.assertEqual(politica.hallazgo_contra_politica(h), "")


class LimitesDeLaPolitica(unittest.TestCase):
    def test_deepseek_a_los_29_espera_y_a_los_31_omite(self):  # no-roja-en-base: caracterización del tope de 30 min
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "politica-ds", partial(github_por_numero, defecto="")
            )
            inicio = time.time()
            e = vuelta("uno")
            self.assertEqual(e.estado, "esperando_reintento")
            e = ciclo._paso_entrega(
                r.reg, r.fold(), vuelta.gh, ahora_iso(inicio + 29 * 60)
            )
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertIn("esperando revisión", e.motivo)
            e = ciclo._paso_entrega(
                r.reg, r.fold(), vuelta.gh, ahora_iso(inicio + 31 * 60)
            )
            self.assertEqual(e.deepseek_huella, "sin-revision")
            self.assertTrue(
                any("tras 31 min" in n for n in e.notas_pr), e.notas_pr
            )

    def test_a_los_6_dias_sondea_y_a_los_8_no(self):  # no-roja-en-base: caracterización de la ventana de 7 días
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = _terminado(self, tmp, "politica-cerrados")
            vuelta.gh.textos[e.pr_numero] = None
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            seis = 6 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + seis + 61
            )
            self.assertEqual(len(vuelta.gh.llamadas), 2)
            ocho = 8 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + ocho + 61
            )
            self.assertEqual(len(vuelta.gh.llamadas), 2)

    def test_cinco_consultas_entran_y_la_sexta_espera_la_otra_pasada(self):  # no-roja-en-base: caracterización del presupuesto de 5 por pasada
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = _terminado(self, tmp, "politica-presupuesto")
            vuelta.gh.textos = {}
            base = e.fases[0]
            seis_fases = tuple(
                replace(
                    base,
                    id=f"f{i}",
                    pr_numero=100 + i,
                    pr_sha=f"{i + 1:040d}",
                    ultimo_paso_entrega="pr_con_pendientes",
                )
                for i in range(6)
            )
            sintetico = replace(e, fases=seis_fases)
            epoch = ciclo._epoch_de_hora(e.hora)
            with mock.patch.object(r.reg, "fold", return_value=sintetico):
                presupuesto = [5]
                ciclo._revisar_deepseek_del_cerrado(
                    r.reg, sintetico, vuelta.gh, e.hora, epoch, presupuesto
                )
                self.assertEqual(len(vuelta.gh.llamadas), 5)
                self.assertEqual(presupuesto, [0])
            marca = r.fold().deepseek_cerrado
            actualizado = replace(sintetico, deepseek_cerrado=marca)
            with mock.patch.object(r.reg, "fold", return_value=actualizado):
                ciclo._revisar_deepseek_del_cerrado(
                    r.reg, actualizado, vuelta.gh, e.hora, epoch + 1, [5]
                )
            self.assertEqual(len(vuelta.gh.llamadas), 6)

    def test_un_medium_en_alcance_bloquea_y_uno_prohibido_no(self):  # no-roja-en-base: caracterización de Medium en y fuera de alcance
        from tests.dobles import github_con_hallazgo

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "politica-medium", github_con_hallazgo
            )
            e = vuelta("uno")
            self.assertEqual(_ids(e), ["F1"])
            self.assertEqual(e.estrategia, "investigar")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "politica-medium-fuera",
                github_con_hallazgo,
                restricciones=(
                    "No tocar motor/integraciones/cli.py ni sus pruebas.",
                ),
            )
            e = vuelta("uno")
            self.assertEqual(_ids(e), [])
            self.assertEqual(e.estado, "terminado")


class LecturaDeLaSesion(unittest.TestCase):
    _ERROR_MACOS = (
        "security: SecKeychainSearchCopyNext: The specified item could not be "
        "found in the keychain."
    )

    def _seguridad(self, falla_con_w: bool):
        def correr(argv, **kw):
            del kw
            if falla_con_w and "-w" in argv:
                return mock.Mock(returncode=44, stdout="", stderr=self._ERROR_MACOS)
            if not falla_con_w and "-w" not in argv:
                return mock.Mock(returncode=44, stdout="", stderr=self._ERROR_MACOS)
            return mock.Mock(returncode=0, stdout="", stderr="")

        return correr

    def test_una_sesion_renovada_sigue_disponible_sin_leer_el_secreto(self):
        lectura = _lectura()
        from motor.encargos import suscripciones

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(Path, "home", return_value=Path(tmp)),
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch.object(
                    suscripciones.subprocess, "run",
                    side_effect=self._seguridad(falla_con_w=True),
                ),
                mock.patch.object(suscripciones.sys, "platform", "darwin"),
            ):
                sesion = suscripciones.disponible("claude")
        self.assertIsInstance(sesion, lectura.Leido)

    def test_una_sesion_que_falta_sigue_diciendo_que_falta(self):
        lectura = _lectura()
        from motor.encargos import suscripciones

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(Path, "home", return_value=Path(tmp)),
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch.object(
                    suscripciones.subprocess, "run",
                    side_effect=self._seguridad(falla_con_w=False),
                ),
                mock.patch.object(suscripciones.sys, "platform", "darwin"),
            ):
                sesion = suscripciones.disponible("claude")
        self.assertIsInstance(sesion, lectura.Pendiente)

    def test_la_espera_nombra_la_causa_real_y_no_el_texto_viejo(self):
        lectura = _lectura()
        from motor.encargos import cli

        motivo = cli._motivo_de_sesion(
            "claude", lectura.Error("la lectura del Llavero fue revocada")
        )
        self.assertIn("no pude leer la sesión de Claude del Llavero", motivo)
        self.assertIn("revocada", motivo)
        self.assertNotIn("no tiene runtime", motivo)
        falta = cli._motivo_de_sesion("claude", lectura.Pendiente(""))
        self.assertEqual(falta, "no hay sesión de Claude en este host")

    def test_opus_ilegible_relega_a_astra(self):
        lectura = _lectura()
        from motor.encargos import cli

        autorizados = {("claude"): ("anthropic/claude-opus-5-5",)}
        lecturas = {"claude": lectura.Error("revocado")}
        with mock.patch.object(
            cli, "leer_disponible", return_value=lectura.Leido(True)
        ):
            relevo = cli._relevo_para_sesion(
                "revisor",
                ("claude", "anthropic/claude-opus-5-5"),
                autorizados,
                lecturas,
                None,
            )
        self.assertEqual(relevo, ("codex", "openai/gpt-6-astra"))

    def test_un_modelo_que_de_verdad_falta_no_se_releva(self):
        lectura = _lectura()
        from motor.encargos import cli

        autorizados = {("claude"): ("anthropic/claude-opus-5-5",)}
        lecturas = {"claude": lectura.Pendiente("")}
        with mock.patch.object(
            cli, "leer_disponible", return_value=lectura.Leido(True)
        ):
            relevo = cli._relevo_para_sesion(
                "revisor",
                ("claude", "anthropic/claude-opus-5-5"),
                autorizados,
                lecturas,
                None,
            )
        self.assertIsNone(relevo)


class RestriccionesEstructuradas(unittest.TestCase):
    def test_encargar_acepta_permitir_y_prohibir(self):
        from motor import __main__ as motor_cli
        from motor.encargos.registro import Registro
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            with (
                contextlib.redirect_stdout(io.StringIO()) as salida,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                codigo = motor_cli.main(
                    [
                        "encargar",
                        "--base",
                        str(base),
                        "--clave",
                        "glob",
                        "--texto",
                        "acota el cambio",
                        "--permitir",
                        "motor/encargos/**",
                        "--prohibir",
                        "docs/**",
                    ]
                )
            self.assertEqual(codigo, 0)
            ident = json.loads(salida.getvalue())["encargo"]
            reg = Registro(base)
            try:
                enc = reg.fold(ident)
            finally:
                reg.close()
            self.assertEqual(enc.permisos, ("motor/encargos/**",))
            self.assertEqual(enc.prohibiciones, ("docs/**",))

    def test_una_prohibicion_saca_el_archivo_que_cubre(self):
        from motor.encargos.comprobacion import (
            hallazgo_fuera_de_alcance_por_restriccion,
        )

        h = Hallazgo(
            id="F1",
            gravedad="bloqueante",
            criterio="seguridad",
            titulo="token en el log",
            ancla="motor/integraciones/cli.py:176",
        )
        try:
            razon = hallazgo_fuera_de_alcance_por_restriccion(
                (), h, prohibiciones=("motor/integraciones/**",)
            )
        except TypeError:
            self.fail(
                "la restricción estructurada no se compara por ruta: falta "
                "--prohibir/--permitir"
            )
        self.assertIn("prohíbe tocar motor/integraciones/**", razon)

    def test_una_lista_blanca_saca_lo_que_no_cubre(self):
        from motor.encargos.comprobacion import (
            hallazgo_fuera_de_alcance_por_restriccion,
        )

        h = Hallazgo(
            id="F2",
            gravedad="bloqueante",
            criterio="seguridad",
            titulo="token en el log",
            ancla="motor/integraciones/cli.py:176",
        )
        try:
            razon = hallazgo_fuera_de_alcance_por_restriccion(
                (), h, permisos=("docs/**",)
            )
        except TypeError:
            self.fail(
                "la lista blanca estructurada no se compara por ruta: falta "
                "--permitir"
            )
        self.assertIn("limita el cambio a docs/**", razon)


class CaracterizacionesDeLoQueYaFunciona(unittest.TestCase):
    def test_ci_cancelado_es_tuberia_y_se_relanza_una_vez(self):  # no-roja-en-base: caracterización del CI cancelado
        self.assertEqual(
            ciclo._clasificar_fallo_ci("bateria", "cancelled", ""), "tuberia"
        )
        self.assertEqual(
            ciclo._causa_tuberia_en_llano(
                [{"nombre": "bateria", "conclusion": "cancelled"}], ""
            ),
            "la corrida se canceló",
        )

    def test_el_timeout_estructurado_del_sticky_es_ausencia(self):  # no-roja-en-base: caracterización del marcador ai-review:completion
        sha = "a" * 40
        sticky = (
            "DeepSeek review\n"
            'ai-review:completion={"status":"timeout","retry":false}\n'
            "la revisión excedió el tiempo límite de 900 s; no se reintenta"
        )
        self.assertEqual(
            ciclo._deepseek_con_contenido([{"texto": sticky}], sha), []
        )
        # El respaldo por frase se conserva para stickies viejos sin marcador.
        viejo = (
            "DeepSeek review\n"
            "la revisión excedió el tiempo límite de 900 s; no se reintenta"
        )
        self.assertEqual(
            ciclo._deepseek_con_contenido([{"texto": viejo}], sha), []
        )


if __name__ == "__main__":
    unittest.main()
