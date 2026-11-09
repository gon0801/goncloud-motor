"""Las decisiones de David en una sola tabla, en sus límites.

La tabla vive en `motor/encargos/politica.py`; `AGENTS.md` se genera desde
ella. Los topes que ya funcionaban se prueban como caracterizaciones. La
lectura de la sesión de un modelo, el relevo autorizado y las restricciones
estructuradas son el arreglo: sus pruebas fallan en la base en la aserción que
nombra el defecto, no por una importación ni por una firma.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest import mock

from motor.encargos import agentes, ciclo, cli, suscripciones
from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import Hallazgo, bloqueantes_abiertos
from tests.dobles import github_con_hallazgos, github_por_numero
from tests.test_flujo_revisiones import _entrega_pr


RAIZ = Path(__file__).resolve().parents[1]
OPUS = "anthropic/claude-opus-5-5"
ASTRA = "openai/gpt-6-astra"


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


def _revisor(enc):
    return replace(
        enc,
        rol="revisor",
        agentes=("claude",),
        modelos_solicitados=(("revisor", "claude", OPUS),),
    )


def _lanzar(r, enc):
    """El lanzador real de producción para el rol revisor."""
    antes = len(r.reg.eventos(r.enc))
    resultado = cli.lanzar_de_ruta(r.reg, ())(enc, Path("/dev/null"), r.espacio, "s-1")
    motivos = [
        ev["data"]["motivo"]
        for ev in r.reg.eventos(r.enc)[antes:]
        if ev["kind"] == "espera"
    ]
    return resultado, (motivos[-1] if motivos else "")


def _security(modo: str):
    """Un `security` falso: renovada, falta o ilegible.

    Los demás subprocess (git del banco) siguen su camino real.
    """
    real = subprocess.run
    aplicacion = (
        "security: SecKeychainItemCopyContent: User interaction is not allowed."
    )
    ausente = (
        "security: SecKeychainSearchCopyNext: The specified item could not be "
        "found in the keychain."
    )

    def correr(argv, **kw):
        if not argv or Path(str(argv[0])).name != "security":
            return real(argv, **kw)
        if "-w" in argv:
            if modo in ("renovada", "falta", "ilegible"):
                return mock.Mock(returncode=44, stdout="", stderr=aplicacion)
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        if modo == "renovada":
            return mock.Mock(returncode=0, stdout="", stderr="")
        if modo == "falta":
            return mock.Mock(returncode=44, stdout="", stderr=ausente)
        return mock.Mock(returncode=44, stdout="", stderr=aplicacion)

    return correr


def _entorno(tmp: Path) -> dict:
    return {
        "HOME": str(tmp),
        "CLAUDE_CONFIG_DIR": str(tmp / "claude"),
        "CODEX_HOME": str(tmp / "codex"),
    }


def _servicio_del_cli(env: dict) -> str:
    """El ítem del Llavero que busca el CLI 2.1.319, medido con `security` falso.

    Con `CLAUDE_SECURESTORAGE_CONFIG_DIR` definido y vacío usa el ítem sin
    sufijo; con ese valor no vacío, o con solo `CLAUDE_CONFIG_DIR`, añade
    `-<sha256 del directorio>` que el login del usuario no creó.
    """
    config = str(env.get("CLAUDE_CONFIG_DIR") or "")
    if "CLAUDE_SECURESTORAGE_CONFIG_DIR" in env:
        sufijo = ""
        if env["CLAUDE_SECURESTORAGE_CONFIG_DIR"]:
            sufijo = "-" + hashlib.sha256(
                env["CLAUDE_SECURESTORAGE_CONFIG_DIR"].encode()
            ).hexdigest()[:8]
    else:
        sufijo = (
            ""
            if not config
            else "-" + hashlib.sha256(config.encode()).hexdigest()[:8]
        )
    return "Claude Code-credentials" + sufijo


class _SesionFalsa(unittest.TestCase):
    """Un host con Claude y Codex, sin leer el Llavero real."""

    def host(self, modo: str):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "codex").mkdir()
        (tmp / "codex" / "auth.json").write_text(
            json.dumps({"auth_mode": "chatgpt"}), encoding="utf-8"
        )
        parches = [
            mock.patch.dict("os.environ", _entorno(tmp), clear=True),
            mock.patch.object(agentes, "resolver_binario", return_value="/usr/bin/true"),
            mock.patch.object(suscripciones.sys, "platform", "darwin"),
            mock.patch.object(
                suscripciones.subprocess, "run", side_effect=_security(modo)
            ),
        ]
        for parche in parches:
            parche.start()
            self.addCleanup(parche.stop)
        return tmp


class TablaUnica(unittest.TestCase):
    def test_la_tabla_de_agents_se_genera_y_el_check_la_protege(self):
        corrida = subprocess.run(
            [sys.executable, str(RAIZ / "tools" / "calidad.py"), "politica"],
            cwd=RAIZ,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            corrida.returncode,
            0,
            "la tabla de AGENTS.md no se genera desde una sola fuente: "
            + (corrida.stderr or corrida.stdout).strip(),
        )

    def test_los_topes_no_viven_en_ciclo(self):
        for nombre in (
            "_TOPE_DEEPSEEK_MIN",
            "_SONDEO_CERRADOS_TOPE_POR_PASADA",
            "_SONDEO_DEEPSEEK_VENTANA_S",
            "_SONDEO_DEEPSEEK_GRACIA_S",
            "_SONDEO_DEEPSEEK_RAPIDO_S",
            "_RELECTURA_DEEPSEEK_CERRADO_S",
        ):
            self.assertFalse(
                hasattr(ciclo, nombre),
                f"el tope suelto {nombre} sigue en ciclo.py; se lee de la tabla",
            )

    def _con_titulo(self, tmp: str, clave: str, titulo: str):
        vuelta, r = _entrega_pr(
            self, tmp, clave, partial(github_con_hallazgos, titulos=(titulo,))
        )
        return vuelta, r

    def test_un_hallazgo_que_pide_cambiar_la_regla_cita_la_fila(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._con_titulo(
                tmp, "politica-cambio", "bajar el tope de DeepSeek de 30 a 15 minutos"
            )
            e = vuelta("uno")
        self.assertEqual(_ids(e), [])
        razones = [x[2] for x in e.fuera_de_alcance]
        self.assertTrue(
            any("regla del propietario" in razon for razon in razones), razones
        )

    def test_un_hallazgo_que_dice_que_el_codigo_incumple_sigue_siendo_normal(self):  # no-roja-en-base: caracterización: un incumplimiento real ya bloqueaba en la base
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._con_titulo(
                tmp,
                "politica-incumple",
                "el sondeo de cerrados hace 10 consultas por pasada; debería "
                "ser 5 como dice la tabla, incumpliendo la regla",
            )
            e = vuelta("uno")
        self.assertNotEqual(_ids(e), [])

    def test_un_numero_que_no_es_una_propuesta_no_saca_el_hallazgo(self):  # no-roja-en-base: caracterización: una mención numérica ya bloqueaba en la base
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._con_titulo(
                tmp,
                "politica-redaccion",
                "cambiar la redacción del tope de DeepSeek de 30 min en la "
                "línea 42 del README",
            )
            e = vuelta("uno")
        self.assertNotEqual(_ids(e), [])

    def test_llevar_el_codigo_al_valor_de_la_tabla_no_saca_el_hallazgo(self):  # no-roja-en-base: caracterización: la base ya lo dejaba bloquear
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._con_titulo(
                tmp,
                "politica-al-valor",
                "el tope de DeepSeek en ciclo.py es 60; cambiar de 60 a 30 "
                "minutos como fija la tabla",
            )
            e = vuelta("uno")
        self.assertNotEqual(_ids(e), [])

    def test_continuar_no_vuelve_a_juzgar_la_politica(self):  # no-roja-en-base: caracterización: la base ya aplicaba el evento guardado como se aceptó
        from motor.encargos.dominio import con_fase, reducir

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "politica-continuar", github_por_numero
            )
            vuelta("uno")
            e = r.fold()
        h = Hallazgo(
            id="ds_x",
            gravedad="bloqueante",
            criterio="comportamiento",
            titulo="bajar el tope de DeepSeek de 30 a 15 minutos",
            origen="deepseek",
        )
        e = con_fase(e, replace(e.fase_actual, hallazgos=(h,)))
        e = replace(
            e, estado="detenido", proc=None, reserva=False, intentos_nuevos=True
        )
        evento = {"kind": "orden", "data": {"accion": "continuar", "hora": e.hora}}
        escrito = reducir(e, evento, nuevo=True)
        plegado = reducir(e, evento, nuevo=False)
        self.assertEqual(_ids(escrito), ["ds_x"])
        self.assertEqual(_ids(plegado), ["ds_x"])


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


class LecturaDeLaSesion(_SesionFalsa):
    def test_tras_renovar_la_sesion_el_motor_lanza_claude(self):
        self.host("renovada")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, r = _entrega_pr(
                self, tmp, "politica-renueva", github_por_numero
            )
            resultado, motivo = _lanzar(r, _revisor(r.fold()))
        self.assertIsNotNone(
            resultado,
            "después de renovar la sesión el motor no lanza a Claude: " + motivo,
        )
        self.assertEqual(resultado[1]["agente"], "claude")

    def test_tras_renovar_la_sesion_el_lanzamiento_no_lee_el_secreto(self):
        self.host("renovada")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env: dict[str, str] = {}
            try:
                suscripciones.preparar(
                    "claude", Path(tmp) / "rt", ["claude", "--model", "x"], env
                )
            except suscripciones.CuentaNoDisponible as exc:
                self.fail(
                    "después de renovar la sesión, el lanzamiento sigue leyendo "
                    f"el secreto del Llavero: {exc}"
                )
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)

    def test_el_cli_busca_el_item_del_llavero_sin_sufijo(self):
        from motor.encargos import suscripciones

        with tempfile.TemporaryDirectory() as tmp:
            llamadas = []
            valido = json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat-x",
                        "scopes": ["user:inference"],
                        "expiresAt": (time.time() + 3600) * 1000,
                    }
                }
            )

            def correr(argv, **kw):
                del kw
                llamadas.append(list(argv))
                if "-w" in argv:
                    return mock.Mock(returncode=0, stdout=valido, stderr="")
                return mock.Mock(returncode=0, stdout="", stderr="")

            env: dict[str, str] = {}
            with (
                mock.patch.object(Path, "home", return_value=Path(tmp)),
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch.object(
                    suscripciones.subprocess, "run", side_effect=correr
                ),
                mock.patch.object(suscripciones.sys, "platform", "darwin"),
            ):
                suscripciones.preparar(
                    "claude", Path(tmp) / "rt", ["claude", "--model", "x"], env
                )
                suscripciones.disponible("claude")
        self.assertEqual(
            env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR"),
            "",
            "con CLAUDE_CONFIG_DIR propio el CLI busca "
            "«Claude Code-credentials-<8 hex>», que el login no creó",
        )
        self.assertEqual(_servicio_del_cli(env), "Claude Code-credentials")
        servicios = [a[a.index("-s") + 1] for a in llamadas if "-s" in a]
        self.assertIn("Claude Code-credentials", servicios)

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("claude"),
        "necesita el CLI de Claude en macOS",
    )
    def test_el_cli_lanzado_busca_el_item_sin_sufijo(self):
        from motor.encargos import suscripciones

        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bin_ = tmp / "bin"
            bin_.mkdir()
            log = tmp / "security.log"
            log.write_text("", encoding="utf-8")
            sh = bin_ / "security"
            sh.write_text(
                '#!/bin/sh\necho "$@" >> "$PROBE_LOG"\nexit 1\n', encoding="utf-8"
            )
            sh.chmod(0o755)
            env: dict[str, str] = {
                "HOME": str(tmp),
                "PATH": f"{bin_}:{os.environ.get('PATH', '')}",
                "PROBE_LOG": str(log),
            }
            valido = json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat-x",
                        "scopes": ["user:inference"],
                        "expiresAt": (time.time() + 3600) * 1000,
                    }
                }
            )

            def correr(argv, **kw):
                del kw
                if "-w" in argv:
                    return mock.Mock(returncode=0, stdout=valido, stderr="")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(Path, "home", return_value=tmp),
                mock.patch.object(
                    suscripciones.subprocess, "run", side_effect=correr
                ),
                mock.patch.object(suscripciones.sys, "platform", "darwin"),
            ):
                suscripciones.preparar(
                    "claude", tmp / "rt", ["claude", "--model", "x"], env
                )
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            try:
                real_run(
                    ["claude", "--print", "--model", "claude-sonnet-5-5", "di hola"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=25,
                )
            except subprocess.TimeoutExpired:
                pass
            texto = log.read_text(encoding="utf-8")
        servicios = []
        for linea in texto.splitlines():
            if "find-generic-password" in linea and "-s " in linea:
                servicios.append(linea.split("-s ", 1)[1].strip())
        self.assertIn(
            "Claude Code-credentials", servicios, f"el CLI buscó {servicios}"
        )
        self.assertFalse(
            any(s.startswith("Claude Code-credentials-") for s in servicios),
            servicios,
        )

    def test_una_sesion_ilegible_relega_a_astra(self):
        self.host("ilegible")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, r = _entrega_pr(
                self, tmp, "politica-relevo", github_por_numero
            )
            resultado, motivo = _lanzar(r, _revisor(r.fold()))
        self.assertIsNotNone(
            resultado,
            "una sesión ilegible de Opus no esperó a una persona ni relevó: "
            + motivo,
        )
        self.assertEqual(resultado[1]["agente"], "codex")
        self.assertEqual(resultado[1]["modelo"], ASTRA)
        self.assertNotIn("no tiene runtime", motivo)

    def test_una_sesion_que_falta_sigue_diciendo_que_falta(self):
        self.host("falta")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, r = _entrega_pr(
                self, tmp, "politica-falta", github_por_numero
            )
            resultado, motivo = _lanzar(r, _revisor(r.fold()))
        self.assertIsNone(resultado)
        self.assertIn("no hay sesión de Claude en este host", motivo)
        self.assertNotIn("no tiene runtime", motivo)


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

    def _firma(self):
        from motor.encargos.comprobacion import (
            hallazgo_fuera_de_alcance_por_restriccion,
        )

        return hallazgo_fuera_de_alcance_por_restriccion

    def test_una_prohibicion_saca_el_archivo_que_cubre(self):
        fn = self._firma()
        self.assertIn(
            "prohibiciones",
            inspect.signature(fn).parameters,
            "la restricción estructurada no se compara por ruta: falta --prohibir",
        )
        h = Hallazgo(
            id="F1",
            gravedad="bloqueante",
            criterio="seguridad",
            titulo="token en el log",
            ancla="motor/integraciones/cli.py:176",
        )
        razon = fn((), h, prohibiciones=("motor/integraciones/**",))
        self.assertIn("prohíbe tocar motor/integraciones/**", razon)

    def test_una_lista_blanca_saca_lo_que_no_cubre(self):
        fn = self._firma()
        self.assertIn(
            "permisos",
            inspect.signature(fn).parameters,
            "la lista blanca estructurada no se compara por ruta: falta --permitir",
        )
        h = Hallazgo(
            id="F2",
            gravedad="bloqueante",
            criterio="seguridad",
            titulo="token en el log",
            ancla="motor/integraciones/cli.py:176",
        )
        razon = fn((), h, permisos=("docs/**",))
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
