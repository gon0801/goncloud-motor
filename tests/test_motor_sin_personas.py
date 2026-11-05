"""El motor no deja ningún estado esperando a una persona.

Una prueba por cada punto del pedido del 3-nov: sin progreso releva y luego
entrega con pendientes, una herramienta caída tres veces se omite, la revisión
de DeepSeek tiene tope, el revisor sin veredicto releva, la disputa la decide el
revisor, cualquier director de la máquina corrige, la cuota usa otro modelo y
una pregunta sin respuesta corta la contesta el revisor.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo
from motor.encargos import cli as motor_cli
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso, barrer, _cuerpo_pr
from motor.encargos.dominio import PausaAgente, bloqueantes_abiertos
from motor.encargos.registro import Registro
from motor.encargos.rutas import elegir
from motor.integraciones import cli as director_cli
from motor.github import FakeGitHub
from tests.test_bucle_instrumento import Banco
from tests.test_flujo_revisiones import (
    Recorrido,
    _GhConDeepSeek,
    _bloqueante,
    _entrega_pr,
)


# Estas pruebas no ejercitan la jaula: la sonda se da por buena en cualquier host.
_PARCHE_SONDA = None


def setUpModule():
    global _PARCHE_SONDA
    _PARCHE_SONDA = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    _PARCHE_SONDA.start()


def tearDownModule():
    global _PARCHE_SONDA
    if _PARCHE_SONDA is not None:
        _PARCHE_SONDA.stop()
        _PARCHE_SONDA = None


def _llamador(enc, prompt, espacio, sesion):
    return None


def _bloqueante_dict(titulo: str = "malo.txt no contiene B") -> dict:
    return _bloqueante(titulo)


class SinProgresoRelevaYEntrega(unittest.TestCase):
    def test_el_mismo_bloqueo_releva_y_luego_entrega_con_pendientes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "p1", _GhConDeepSeek)
            self.addCleanup(r.reg.close)
            vuelta.gh.textos = []
            malo = r.espacio / "malo.txt"

            def ronda(i: int):
                malo.write_text(f"intento-{i}")
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "cambios",
                        "evidencia": f"cat malo.txt -> intento-{i}",
                        "bloqueantes": [_bloqueante_dict()],
                    }
                )
                return r.incorporar()

            ronda(1)
            ronda(2)
            e = ronda(3)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.relevos, 1)
            e = ronda(4)
            self.assertTrue(e.pendientes_entrega)
            self.assertNotEqual(e.estado, "necesita_usuario")

            barrer(r.reg, ahora_iso(), time.time(), _llamador, gh=vuelta.gh)
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(e.pr_numero)
            estado = json.loads(
                (Path(tmp) / "fake" / "github.json").read_text(encoding="utf-8")
            )
            pr = next(iter(estado["prs"].values()))
            self.assertIn("Pendientes abiertos al entregar", pr["cuerpo"])
            self.assertIn("malo.txt no contiene B", pr["cuerpo"])


class HerramientaCaidaSeOmite(unittest.TestCase):
    def test_la_misma_herramienta_caida_tres_veces_queda_no_disponible(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            banco = Banco(tmp, {"actual": SondaEntorno(True)})
            self.addCleanup(banco.reg.close)
            for i in range(2):
                banco.anotar(
                    "instrumento_fallo",
                    {"clase": "comprobacion", "causa": f"falla {i}"},
                    f"fallo-{i}",
                )
            enc = banco.fold()
            manejado = ciclo._impedimento_de_instrumento(
                banco.reg, enc, "comprobacion", "falla igual", "repararla", banco.hora
            )
            # Manejado: la herramienta queda no disponible y no se gasta un
            # turno de reparación.
            self.assertTrue(manejado)
            enc = banco.fold()
            self.assertEqual(enc.impedimento, "")
            self.assertIn("comprobacion", enc.herramientas_no_disponibles)
            self.assertTrue(any("no disponible" in n for n in enc.notas_pr))

    def test_el_juicio_caido_tres_veces_tambien_se_omite(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            banco = Banco(tmp, {"actual": SondaEntorno(True)})
            self.addCleanup(banco.reg.close)
            for i in range(2):
                banco.anotar(
                    "instrumento_fallo",
                    {"clase": "juicio:esquema", "causa": f"falta veredicto {i}"},
                    f"fallo-juicio-{i}",
                )
            enc = banco.fold()
            manejado = ciclo._impedimento_de_instrumento(
                banco.reg, enc, "juicio:esquema", "falta veredicto",
                "volver a emitir el juicio", banco.hora,
            )
            self.assertTrue(manejado)
            enc = banco.fold()
            self.assertEqual(enc.impedimento, "")
            self.assertEqual(enc.impedimentos, ())
            self.assertIn("juicio:esquema", enc.herramientas_no_disponibles)


class DeepSeekTieneTope(unittest.TestCase):
    def test_sin_revision_de_deepseek_pasado_el_tope_entrega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-tope", FakeGitHub)
            self.addCleanup(r.reg.close)
            vuelta_gh = vuelta.gh
            (r.espacio / "saludo.txt").write_text("hola")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
            r.incorporar()
            gh = vuelta.gh
            barrer(r.reg, ahora_iso(), time.time(), _llamador, gh=gh)
            e = r.fold()
            self.assertEqual(e.estado, "esperando_reintento")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            barrer(r.reg, tarde, time.time(), _llamador, gh=gh)
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("DeepSeek" in n for n in e.notas_pr), e.notas_pr)
            self.assertTrue(e.pendientes_entrega, e.pendientes_entrega)

    def test_si_no_se_puede_leer_deepseek_el_tope_tambien_entrega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-tope-lectura", FakeGitHub)
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
            r.incorporar()
            gh = vuelta.gh
            barrer(r.reg, ahora_iso(), time.time(), _llamador, gh=gh)
            e = r.fold()
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            gh.comentarios_deepseek = mock.Mock(
                side_effect=RuntimeError("la API no responde")
            )
            barrer(r.reg, tarde, time.time(), _llamador, gh=gh)
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(
                any("no se pudo leer" in n for n in e.notas_pr), e.notas_pr
            )
            self.assertTrue(e.pendientes_entrega, e.pendientes_entrega)


class RevisorSinVeredictoReleva(unittest.TestCase):
    def test_el_revisor_que_no_concluye_releva_y_luego_entrega(self):
        from tests.test_freno_no_concluyente import _Freno

        caso = _Freno(methodName="runTest")
        caso.setUp()
        self.addCleanup(caso.doCleanups)
        r = caso.recorrido("p4", con_recurso=True)
        caso.herramienta_ok()
        for ruta in ("a.txt", "b.txt", "c.txt"):
            caso.vuelta(r, ruta)
        e = r.fold()
        self.assertNotEqual(e.estado, "necesita_usuario")
        self.assertEqual(e.relevos, 1)
        caso.vuelta(r, "d.txt")
        e = r.fold()
        self.assertTrue(e.pendientes_entrega or e.estado == "terminado")

    def test_sin_inspeccion_faltante_el_relevo_va_a_otro_revisor(self):
        from tests.test_freno_no_concluyente import _Freno

        caso = _Freno(methodName="runTest")
        caso.setUp()
        self.addCleanup(caso.doCleanups)
        r = caso.recorrido("f3")

        def vuelta(i: int):
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "no_concluyente",
                    "evidencia": f"no alcancé el log del job, vuelta {i}",
                }
            )
            return r.incorporar()

        vuelta(1)
        enc = vuelta(2)
        self.assertEqual(enc.rol, "revisor")
        self.assertGreaterEqual(enc.relevos, 1)
        self.assertNotEqual(enc.estado, "necesita_usuario")


class RelevoPorRol(unittest.TestCase):
    def test_un_relevo_del_responsable_no_deja_al_revisor_sin_modelo(self):
        from motor.encargos.dominio import relevos_de

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            reg = Registro(base)
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "f1", ahora_iso())["encargo"]

            elegidas = []

            def construir(agente, modelo, modo):
                def lanzar(actual, prompt, espacio, sesion):
                    elegidas.append((actual.rol, agente, modelo))
                    return [agente, modelo]

                return lanzar

            def paso(registro, hora, epoch, lanzador, **kw):
                actual = registro.fold(encargo)
                actual = replace(
                    actual,
                    rol="revisor",
                    modelos=(
                        ("responsable", "claude", "claude/opus"),
                        ("revisor", "claude", "claude/sonnet"),
                    ),
                    relevos=1,
                    relevos_por_rol=(("responsable", 1),),
                    relevo_huella_por_rol=(("responsable", "bloqueo-1"),),
                )
                self.assertEqual(relevos_de(actual, "responsable"), 1)
                self.assertEqual(relevos_de(actual, "revisor"), 0)
                comando, _ = lanzador(
                    actual, base / "pedido", base / "espacio", "s1"
                )
                self.assertEqual(comando, ["claude", "claude/sonnet"])
                return []

            args = SimpleNamespace(
                agente="claude", respaldo=[], una_vez=True, umbral=30, intervalo=1
            )
            with (
                mock.patch.object(motor_cli, "_disponible", return_value=True),
                mock.patch.object(
                    motor_cli, "_modelos_para", return_value=("claude/sonnet",)
                ),
                mock.patch.object(motor_cli, "_lanzador", side_effect=construir),
                mock.patch.object(motor_cli, "barrer", side_effect=paso),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(motor_cli._supervisor(reg, args), 0)
            self.assertEqual(elegidas, [("revisor", "claude", "claude/sonnet")])


class PreguntaVencidaNoLaContestaUnJuicioViejo(unittest.TestCase):
    def test_un_turno_vivo_no_contesta_la_pregunta_que_no_vio(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "f2", "dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("42")
            r.incorporar()
            enc = r.fold()
            r.reg.anotar(
                r.enc,
                {
                    "kind": "reserva",
                    "data": {"rol": "revisor", "generacion": enc.generacion + 1},
                },
                "f2-reserva",
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "intento_abierto",
                    "data": {
                        "id": "rev-vivo",
                        "pid": os.getpid(),
                        "inicio": plataforma.hora_inicio(os.getpid()),
                        "rol": "revisor",
                        "hora": ahora_iso(),
                    },
                },
                "f2-abierto",
            )
            enc = r.fold()
            vieja = (
                datetime.now(timezone.utc) - timedelta(minutes=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "solicitud",
                    "data": {
                        "id": "sol-viva",
                        "operacion": "remoto.leer",
                        "recurso": "ci-github",
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "generacion": enc.generacion,
                        "conductor": enc.conductor,
                        "motivo": "falta el log",
                        "parametros": [["ruta", "indice.txt"]],
                        "estado": "pregunta",
                        "pregunta": "¿autorizas leer el log?",
                        "hora": vieja,
                    },
                },
                "sol-viva",
            )
            barrer(r.reg, ahora_iso(), time.time(), _llamador)
            self.assertEqual(
                [
                    ev
                    for ev in r.reg.eventos(r.enc)
                    if ev["kind"] == "respuesta_revisor_pedida"
                ],
                [],
            )

            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "el producto no cambió",
                    "bloqueantes": [_bloqueante_dict()],
                }
            )
            r.incorporar()
            enc = r.fold()
            self.assertEqual(enc.solicitudes[0].estado, "pregunta")


class DisputaLaDecideElRevisor(unittest.TestCase):
    def test_la_disputa_no_pide_al_propietario_y_el_revisor_la_resuelve(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "p5", _GhConDeepSeek)
            self.addCleanup(r.reg.close)
            e = vuelta("uno")
            hallazgo = bloqueantes_abiertos(e, "deepseek")[0]
            (_interno := r.espacio / ".interno").mkdir(parents=True, exist_ok=True)
            (_interno / "disputa.json").write_text(
                json.dumps(
                    {
                        "hallazgo": hallazgo.id,
                        "evidencia": "el token del log es publico de prueba",
                    }
                )
            )
            e = r.incorporar()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.preguntas, ())
            self.assertTrue(
                any("publico de prueba" in n for n in e.notas_pr), e.notas_pr
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "estrategia",
                    "data": {
                        "estrategia": "revisar_evidencia",
                        "rol": "revisor",
                        "motivo": "la disputa la decide el revisor",
                        "siguiente": "resolverla con evidencia",
                        "hora": r.hora,
                    },
                },
                "p5-revisor",
            )
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "el token es un identificador publico de prueba",
                    "resueltos": [hallazgo.id],
                }
            )
            e = r.incorporar()
            self.assertTrue(
                any(
                    "descartado por el revisor" in n for n in e.notas_pr
                ),
                e.notas_pr,
            )
            self.assertIn(
                "descartado por el revisor", _cuerpo_pr(e, r.espacio)
            )


class CualquierDirectorCorrige(unittest.TestCase):
    def _correr(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as salida:
            codigo = director_cli.main(argv)
        return codigo, json.loads(salida.getvalue() or "{}")

    def test_un_director_registrado_corrige_y_queda_registrado(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            codigo, recibo = self._correr(
                [
                    "encargar", "--base", str(base), "--director", "hermes",
                    "--via", "durable", "--canal", "hermes", "--clave", "pedido",
                    "--texto", "Crea dato.txt con 42",
                ]
            )
            self.assertEqual(codigo, 0)
            encargo = recibo["encargo"]
            codigo, salida = self._correr(
                [
                    "corregir", encargo, "--base", str(base), "--clave", "corr",
                    "--texto", "usa UTF-8", "--canal", "claw",
                ]
            )
            self.assertEqual(codigo, 0)
            self.assertEqual(salida["autor"], "director:claw")
            reg = Registro(base)
            self.addCleanup(reg.close)
            self.assertEqual(reg.fold(encargo).ajustes, ("usa UTF-8",))

    def _encargar(self, base: Path) -> str:
        codigo, recibo = self._correr(
            [
                "encargar", "--base", str(base), "--director", "hermes",
                "--via", "durable", "--canal", "hermes", "--clave", "pedido",
                "--texto", "Crea dato.txt con 42",
            ]
        )
        self.assertEqual(codigo, 0)
        return recibo["encargo"]

    def test_parar_de_un_canal_no_registrado_se_rechaza(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            encargo = self._encargar(base)
            codigo, salida = self._correr(
                [
                    "parar", encargo, "--base", str(base), "--clave", "para-1",
                    "--canal", "desconocido",
                ]
            )
            self.assertEqual(codigo, 2)
            self.assertTrue(salida["rechazada"])
            reg = Registro(base)
            self.addCleanup(reg.close)
            self.assertEqual(reg.fold(encargo).estado, "recibido")
            self.assertEqual(
                [e for e in reg.eventos(encargo) if e["kind"] == "orden"], []
            )

    def test_parar_y_continuar_de_un_director_registrado_se_aceptan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            encargo = self._encargar(base)
            codigo, salida = self._correr(
                [
                    "parar", encargo, "--base", str(base), "--clave", "para-1",
                    "--canal", "claw",
                ]
            )
            self.assertEqual(codigo, 0)
            self.assertEqual(salida["estado"], "deteniendo")
            reg = Registro(base)
            self.addCleanup(reg.close)
            reg.anotar(
                encargo,
                {"kind": "arbol", "data": {"observacion": "ausente"}},
                "t-ausente",
            )
            self.assertEqual(reg.fold(encargo).estado, "detenido")
            codigo, salida = self._correr(
                [
                    "continuar", encargo, "--base", str(base),
                    "--clave", "sigue-1", "--canal", "hermes",
                ]
            )
            self.assertEqual(codigo, 0)
            self.assertEqual(reg.fold(encargo).estado, "recibido")
            autores = [
                ev["data"].get("autor")
                for ev in reg.eventos(encargo)
                if ev["kind"] == "orden"
            ]
            self.assertEqual(autores, ["director:claw", "director:hermes"])

    def test_sin_canal_la_orden_queda_como_operador_registrado(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            encargo = self._encargar(base)
            codigo, salida = self._correr(
                [
                    "parar", encargo, "--base", str(base), "--clave", "para-1",
                ]
            )
            self.assertEqual(codigo, 0)
            self.assertEqual(salida["estado"], "deteniendo")
            reg = Registro(base)
            self.addCleanup(reg.close)
            autores = [
                ev["data"].get("autor")
                for ev in reg.eventos(encargo)
                if ev["kind"] == "orden"
            ]
            self.assertEqual(autores, ["director:operador"])


class CuotaUsaOtroModelo(unittest.TestCase):
    def test_un_modelo_sin_cuota_no_tapa_a_los_demas_del_mismo_rol(self):
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            recibo = ciclo.recibir(reg, "x", "cuota", ahora_iso())
            enc = reg.fold(recibo["encargo"])
        # Se construye el encargo con la pausa por cuota de un modelo concreto.
        enc = replace(
            enc,
            agentes=("claude",),
            pausas=(
                PausaAgente(
                    "claude",
                    "2099-01-01T00:00:00Z",
                    "cuota agotada",
                    True,
                    "claude/opus",
                ),
            ),
        )
        elegido = elegir(
            enc,
            "2026-11-04T00:00:00Z",
            {"claude": ("claude/opus", "claude/sonnet")},
            rol="responsable",
        )
        self.assertEqual(elegido, ("claude", "claude/sonnet"))

    def test_la_pausa_de_un_modelo_no_borra_la_de_otro_del_mismo_agente(self):
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "cuota-dos", ahora_iso())["encargo"]
            for modelo in ("claude/opus", "claude/sonnet"):
                reg.anotar(
                    encargo,
                    {
                        "kind": "proveedor_fallo",
                        "data": {
                            "agente": "claude",
                            "modelo": modelo,
                            "hasta": "2099-01-01T00:00:00Z",
                            "causa": "cuota agotada",
                            "relevo": True,
                        },
                    },
                    f"pausa-{modelo}",
                )
            enc = reg.fold(encargo)
            self.assertEqual(
                sorted(p.modelo for p in enc.pausas),
                ["claude/opus", "claude/sonnet"],
            )
            self.assertIsNone(
                elegir(
                    replace(enc, agentes=("claude",)),
                    "2026-11-04T00:00:00Z",
                    {"claude": ("claude/opus", "claude/sonnet")},
                    rol="responsable",
                )
            )


class PreguntaSinRespuestaLaContestaElRevisor(unittest.TestCase):
    def test_una_pregunta_vencida_se_contesta_y_el_trabajo_sigue(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "p8", "dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("42")
            r.incorporar()
            enc = r.fold()
            vieja = (
                datetime.now(timezone.utc) - timedelta(minutes=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "solicitud",
                    "data": {
                        "id": "sol-1",
                        "operacion": "remoto.leer",
                        "recurso": "ci-github",
                        "fase": enc.fase_activa,
                        "intento": enc.intento or "t-1",
                        "generacion": enc.generacion,
                        "conductor": enc.conductor,
                        "motivo": "falta el log",
                        "parametros": [["ruta", "indice.txt"]],
                        "estado": "pregunta",
                        "pregunta": "¿sigo sin el log?",
                        "hora": vieja,
                    },
                },
                "sol-1",
            )
            barrer(r.reg, ahora_iso(), time.time(), _llamador)
            enc = r.fold()
            self.assertEqual(enc.rol, "revisor")
            self.assertEqual(enc.estrategia, "responder_pregunta")
            self.assertEqual(enc.solicitudes[0].estado, "pregunta")

            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "Autorizo leer el log remoto: es un log de prueba",
                }
            )
            r.incorporar()
            enc = r.fold()
            self.assertNotEqual(enc.solicitudes[0].estado, "pregunta")
            self.assertTrue(
                any(
                    "contestada por el revisor" in n for n in enc.notas_pr
                ),
                enc.notas_pr,
            )
            respuestas = [
                ev["data"]
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "respuesta"
            ]
            self.assertTrue(respuestas)
            self.assertIn("log de prueba", respuestas[-1]["texto"])
            self.assertEqual(respuestas[-1]["autor"], "revisor")




class AlCerrarLimpiaSuDisco(unittest.TestCase):
    def test_el_cierre_borra_runtime_y_los_intentos_de_ejecucion(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "limpia", ahora_iso())["encargo"]
            runtime = reg.base / "runtime" / encargo / "intento-1"
            ejecucion = reg.base / "ejecucion" / encargo / "intento-1"
            runtime.mkdir(parents=True)
            ejecucion.mkdir(parents=True)
            (runtime / "sesion.db").write_text("sesion")
            (ejecucion / "herramientas.json").write_text("{}")
            reg.anotar(
                encargo,
                {
                    "kind": "pendiente_entrega",
                    "data": {"texto": "bloqueo abierto", "hora": ahora_iso()},
                },
                "pendiente-limpia",
            )
            enc = reg.fold(encargo)
            enc = ciclo._entregar_con_pendientes(reg, enc, None, ahora_iso())
            self.assertEqual(enc.estado, "terminado")
            self.assertFalse(runtime.exists())
            self.assertFalse(ejecucion.exists())
            self.assertEqual(reg.fold(encargo).estado, "terminado")

    def test_un_cancelado_tambien_limpia_runtime_y_ejecucion(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "cancela", ahora_iso())["encargo"]
            runtime = reg.base / "runtime" / encargo / "intento-1"
            ejecucion = reg.base / "ejecucion" / encargo / "intento-1"
            runtime.mkdir(parents=True)
            ejecucion.mkdir(parents=True)
            (runtime / "tmp").mkdir()
            (ejecucion / "herramientas.json").write_text("{}")
            ciclo.ordenar(reg, encargo, "cancelar", "cancela-1", ahora_iso())
            reg.anotar(
                encargo,
                {"kind": "arbol", "data": {"observacion": "ausente"}},
                "cancela-ausente",
            )
            self.assertEqual(reg.fold(encargo).estado, "cancelado")
            barrer(reg, ahora_iso(), time.time(), _llamador)
            self.assertFalse(runtime.exists())
            self.assertFalse(ejecucion.exists())
            self.assertEqual(reg.fold(encargo).estado, "cancelado")

    def test_el_cierre_mata_el_proceso_que_siguio_vivo(self):
        import socket
        import subprocess
        import sys
        import time as reloj

        from motor import plataforma

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "proceso", ahora_iso())["encargo"]
            intento = reg.base / "ejecucion" / encargo / "intento-1"
            intento.mkdir(parents=True)
            proceso = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            self.addCleanup(lambda: proceso.poll() is None and proceso.kill())
            (intento / "proc.json").write_text(
                json.dumps(
                    {
                        "pid": proceso.pid,
                        "inicio": plataforma.hora_inicio(proceso.pid),
                        "boot_id": plataforma.boot_id(),
                        "host": socket.gethostname(),
                    }
                )
            )
            reg.anotar(
                encargo,
                {
                    "kind": "pendiente_entrega",
                    "data": {"texto": "bloqueo abierto", "hora": ahora_iso()},
                },
                "pendiente-proceso",
            )
            enc = reg.fold(encargo)
            ciclo._entregar_con_pendientes(reg, enc, None, ahora_iso())
            fin = reloj.time() + 5
            while plataforma.pid_vivo(proceso.pid) and reloj.time() < fin:
                reloj.sleep(0.05)
            self.assertFalse(plataforma.pid_vivo(proceso.pid))


class EntregarSinPuertoDejaConstatancia(unittest.TestCase):
    def test_sin_puerto_github_queda_escrito_que_falta_el_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(
                reg,
                "x",
                "sin-puerto",
                ahora_iso(),
                entrega_nivel="pr",
                repo="gon0801/goncloud-motor",
                rama="encargo/falta-pr",
            )["encargo"]
            reg.anotar(
                encargo,
                {
                    "kind": "pendiente_entrega",
                    "data": {"texto": "bloqueo abierto", "hora": ahora_iso()},
                },
                "pendiente-sin-puerto",
            )
            enc = reg.fold(encargo)
            enc = ciclo._entregar_con_pendientes(reg, enc, None, ahora_iso())
            self.assertEqual(enc.estado, "terminado")
            self.assertTrue(
                any(
                    "sin puerto GitHub" in nota and "PR" in nota
                    for nota in enc.notas_pr
                ),
                enc.notas_pr,
            )
            self.assertTrue(
                any("No se pudo publicar el PR" in x for x in enc.evidencia),
                enc.evidencia,
            )


class EncargoCerradoSigueMirandoAutoridad(unittest.TestCase):
    def test_un_terminado_reintenta_el_resultado_por_la_autoridad(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "autoridad-resultado", ahora_iso())[
                "encargo"
            ]
            reg.anotar(
                encargo,
                {
                    "kind": "cierre_forzado",
                    "data": {
                        "evidencia": "entregado con pendientes abiertos",
                        "hora": ahora_iso(),
                    },
                },
                "cierre-autoridad",
            )
            self.assertEqual(reg.fold(encargo).estado, "terminado")
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "recibido"}},
                ),
                mock.patch.object(
                    ciclo, "_publicar_resultado", return_value=True
                ) as publicado,
            ):
                barrer(reg, ahora_iso(), time.time(), _llamador)
            publicado.assert_called()

    def test_un_terminado_resuelto_no_consulta_la_autoridad_cada_vuelta(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "autoridad-una-vez", ahora_iso())[
                "encargo"
            ]
            reg.anotar(
                encargo,
                {
                    "kind": "cierre_forzado",
                    "data": {"evidencia": "entregado con pendientes", "hora": ahora_iso()},
                },
                "cierre-una-vez",
            )
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "terminado"}},
                ) as operar,
            ):
                for _ in range(3):
                    barrer(reg, ahora_iso(), time.time(), _llamador)
            self.assertEqual(operar.call_count, 1)

    def test_un_cancelado_reintenta_la_orden_canonica(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "autoridad-orden", ahora_iso())[
                "encargo"
            ]
            ciclo.ordenar(reg, encargo, "cancelar", "cancela-1", ahora_iso())
            reg.anotar(
                encargo,
                {"kind": "arbol", "data": {"observacion": "ausente"}},
                "cancela-ausente",
            )
            self.assertEqual(reg.fold(encargo).estado, "cancelado")
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "cancelado"}},
                ),
                mock.patch.object(
                    ciclo, "_enviar_orden", return_value=True
                ) as enviar,
            ):
                barrer(reg, ahora_iso(), time.time(), _llamador)
            claves = [llamada.args[3] for llamada in enviar.call_args_list]
            self.assertTrue(
                any(str(clave).endswith("-canonico") for clave in claves), claves
            )

    def test_un_cierre_nuevo_tras_reabrir_el_pr_vuelve_a_conciliarse(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(
                reg,
                "x",
                "reabre-pr",
                ahora_iso(),
                entrega_nivel="pr",
                repo="gon0801/goncloud-motor",
                rama="encargo/reabre",
            )["encargo"]
            reg.anotar(
                encargo,
                {
                    "kind": "cierre_forzado",
                    "data": {"evidencia": "primer cierre", "hora": ahora_iso()},
                },
                "cierre-1",
            )
            ciclo._marcar_autoridad_cerrada(reg, reg.fold(encargo), ahora_iso())
            self.assertTrue(
                ciclo._autoridad_cerrada_resuelta(reg, reg.fold(encargo))
            )

            fase = reg.fold(encargo).fase_activa
            reg.anotar(
                encargo,
                {
                    "kind": "ci_pr",
                    "data": {
                        "fase": fase,
                        "sha": "sha-reabre",
                        "estado": "rojo",
                        "reabrir": True,
                        "fallas": ["test_del_producto"],
                        "causa": "fallo del producto",
                        "hora": ahora_iso(),
                    },
                },
                "ci-pr-rojo-reabre",
            )
            self.assertEqual(reg.fold(encargo).estado, "recibido")
            reg.anotar(
                encargo,
                {
                    "kind": "cierre_forzado",
                    "data": {
                        "evidencia": "segundo cierre corregido",
                        "hora": ahora_iso(),
                    },
                },
                "cierre-2",
            )
            self.assertEqual(reg.fold(encargo).estado, "terminado")
            self.assertFalse(
                ciclo._autoridad_cerrada_resuelta(reg, reg.fold(encargo))
            )

            # La autoridad ya quedó en terminado por el primer cierre: aun así
            # hay que publicar la evidencia del cierre nuevo.
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "terminado"}},
                ) as operar,
                mock.patch.object(
                    ciclo, "_publicar_resultado", return_value=True
                ) as publicado,
            ):
                barrer(reg, ahora_iso(), time.time(), _llamador)
                # La marca recién sellada detiene los barridos siguientes:
                # sin ella, cada vuelta volvería a publicar el resultado.
                barrer(reg, ahora_iso(), time.time(), _llamador)
            operar.assert_called()
            self.assertEqual(publicado.call_count, 1)
            self.assertTrue(
                ciclo._autoridad_cerrada_resuelta(reg, reg.fold(encargo))
            )

    def test_una_marca_vieja_sin_estado_no_impide_sellar_el_cierre(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "marca-vieja", ahora_iso())[
                "encargo"
            ]
            reg.anotar(
                encargo,
                {
                    "kind": "cierre_forzado",
                    "data": {"evidencia": "entregado con pendientes", "hora": ahora_iso()},
                },
                "cierre-marca-vieja",
            )
            # Marca de la versión anterior: mismo id, sin estado.
            reg.anotar(
                encargo,
                {"kind": "autoridad_cerrada", "data": {"hora": ahora_iso()}},
                f"autoridad-cerrada:{encargo}",
            )
            self.assertFalse(
                ciclo._autoridad_cerrada_resuelta(reg, reg.fold(encargo))
            )
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "terminado"}},
                ) as operar,
            ):
                for _ in range(2):
                    barrer(reg, ahora_iso(), time.time(), _llamador)
            self.assertEqual(operar.call_count, 1)
            self.assertTrue(
                ciclo._autoridad_cerrada_resuelta(reg, reg.fold(encargo))
            )

    def test_un_detenido_no_sella_el_cierre_y_luego_publica_el_resultado(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "detenido-luego-cierra", ahora_iso())[
                "encargo"
            ]
            ciclo.ordenar(reg, encargo, "parar", "parar-1", ahora_iso())
            reg.anotar(
                encargo,
                {"kind": "arbol", "data": {"observacion": "ausente"}},
                "parar-ausente",
            )
            self.assertEqual(reg.fold(encargo).estado, "detenido")
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "detenido"}},
                ),
                mock.patch.object(
                    ciclo, "_enviar_orden", return_value=True
                ) as enviar,
            ):
                for _ in range(2):
                    barrer(reg, ahora_iso(), time.time(), _llamador)
            self.assertEqual(enviar.call_count, 1)
            self.assertFalse(
                ciclo._autoridad_cerrada_resuelta(reg, reg.fold(encargo))
            )

            ciclo.ordenar(reg, encargo, "continuar", "sigue-1", ahora_iso())
            reg.anotar(
                encargo,
                {
                    "kind": "cierre_forzado",
                    "data": {
                        "evidencia": "entregado con pendientes abiertos",
                        "hora": ahora_iso(),
                    },
                },
                "cierre-despues-detenido",
            )
            self.assertEqual(reg.fold(encargo).estado, "terminado")
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "recibido"}},
                ),
                mock.patch.object(
                    ciclo, "_publicar_resultado", return_value=True
                ) as publicado,
            ):
                barrer(reg, ahora_iso(), time.time(), _llamador)
            publicado.assert_called()

    def test_un_cancelado_resuelto_no_reenvia_la_orden_cada_vuelta(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            encargo = ciclo.recibir(reg, "x", "orden-una-vez", ahora_iso())[
                "encargo"
            ]
            ciclo.ordenar(reg, encargo, "cancelar", "cancela-2", ahora_iso())
            reg.anotar(
                encargo,
                {"kind": "arbol", "data": {"observacion": "ausente"}},
                "cancela-ausente-2",
            )
            with (
                mock.patch("motor.encargos.transporte.cargar", return_value={}),
                mock.patch(
                    "motor.encargos.transporte.operar",
                    return_value={"alcanzada": True, "salida": {"estado": "cancelado"}},
                ),
                mock.patch.object(
                    ciclo, "_enviar_orden", return_value=True
                ) as enviar,
            ):
                for _ in range(3):
                    barrer(reg, ahora_iso(), time.time(), _llamador)
            self.assertEqual(enviar.call_count, 1)


class AvisosInformanSinPedirNada(unittest.TestCase):
    def test_el_cierre_cuenta_los_pendientes_como_informacion(self):
        from dataclasses import replace

        from motor.integraciones.directores import _cuerpo_de_cierre

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            recibo = ciclo.recibir(reg, "x", "aviso", ahora_iso())
            enc = reg.fold(recibo["encargo"])
        enc = replace(
            enc,
            relevos=1,
            pendientes_entrega=("un bloqueo abierto",),
            herramientas_no_disponibles=("comprobacion",),
        )
        texto = _cuerpo_de_cierre(enc)
        self.assertIn("no hace falta que hagas nada", texto)
        self.assertIn("herramienta no estaba disponible", texto)
        self.assertIn("cambió de modelo", texto)
        self.assertNotIn("Necesito tu decisión", texto)

if __name__ == "__main__":
    unittest.main()
