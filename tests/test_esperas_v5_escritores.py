"""5.1: los escritores de `espera` dicen su clase y la tabla pone intervalo y tope.

Cubren lo que el contrato pide como evidencia de la unidad: cada llamada lleva su
clase, un `espera` guardado antes de la 5.1 sigue cotejando `igual`, dos clases
distintas sobre la misma ukey dan `ClaveOcupada`, la orden `motor espera` exige
hora UTC con el interruptor encendido, y con el interruptor apagado la tabla da
los mismos intervalos que la base.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo, tabla_esperas
from motor.encargos import cli as motor_cli
from motor.encargos.registro import ClaveOcupada, Registro

H0 = "2026-11-18T10:00:00Z"
H1 = "2026-11-18T10:05:00Z"


def _recibir(reg: Registro, clave: str = "pedido-1") -> str:
    return ciclo.recibir(reg, "Crea dato.txt con 42", clave, H0)["encargo"]


class EscritoresDeLaClase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)
        self.enc = _recibir(self.reg)

    def _espera(self, id_evento: int = 0) -> dict:
        guardadas = [e for e in self.reg.eventos(self.enc) if e["kind"] == "espera"]
        return guardadas[id_evento]["data"]

    def test_cada_espera_guarda_su_clase_y_su_apertura(self):
        ciclo.marcar_espera(
            self.reg, self.enc, "sesion_local", "la sesión local falló", H1
        )
        enc = self.reg.fold(self.enc)
        data = self._espera()
        self.assertEqual(data["clase"], "sesion_local")
        self.assertEqual(data["abierta"], H1)
        self.assertEqual(data["reanuda_en"], "2026-11-18T10:35:00Z")
        self.assertEqual(data["tope"], "2026-11-18T11:05:00Z")
        self.assertEqual(enc.espera_clase, "sesion_local")

    def test_la_misma_clase_conserva_apertura_y_otra_la_estrena(self):
        ciclo.marcar_espera(self.reg, self.enc, "sesion_local", "falló", H0)
        self.reg.anotar(
            self.enc, {"kind": "plazo", "data": {"hora": H1}}, f"plazo:{self.enc}:1"
        )
        ciclo.marcar_espera(self.reg, self.enc, "sesion_local", "falló otra vez", H1)
        self.assertEqual(self._espera(1)["abierta"], H0)
        self.reg.anotar(
            self.enc, {"kind": "plazo", "data": {"hora": H1}}, f"plazo:{self.enc}:2"
        )
        ciclo.marcar_espera(self.reg, self.enc, "repo", "el repo no quedó listo", H1)
        self.assertEqual(self._espera(2)["abierta"], H1)

    def test_la_orden_esperada_lleva_su_reanudacion(self):
        ciclo.marcar_espera(
            self.reg, self.enc, "orden_espera", "espera ordenada", H0,
            reanuda_en="2026-11-18T18:00:00Z",
        )
        data = self._espera()
        self.assertEqual(data["clase"], "orden_espera")
        self.assertEqual(data["reanuda_en"], "2026-11-18T18:00:00Z")
        self.assertEqual(data["tope"], "1 intentos")

    def test_con_el_interruptor_encendido_las_horas_quedan_en_utc(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            ciclo.marcar_espera(
                self.reg, self.enc, "proveedor", "cuota agotada",
                "2026-11-18T09:00:00+00:00",
                reanuda_en="2026-11-18T02:30:00-07:00",
            )
        data = self._espera()
        self.assertEqual(data["abierta"], "2026-11-18T09:00:00Z")
        self.assertEqual(data["reanuda_en"], "2026-11-18T09:30:00Z")
        self.assertEqual(data["tope"], "2026-11-18T15:00:00Z")

    def test_es_hora_utc_lee_los_desplazamientos(self):
        self.assertEqual(
            tabla_esperas.hora_canonica("2026-11-18T02:30:00-07:00"),
            "2026-11-18T09:30:00Z",
        )
        self.assertTrue(tabla_esperas.es_hora_utc("2026-11-18T09:30:00+00:00"))
        self.assertFalse(tabla_esperas.es_hora_utc("nunca"))
        self.assertFalse(tabla_esperas.es_hora_utc(""))

    def test_el_intervalo_no_conserva_el_desplazamiento(self):
        # Un intervalo sobre una base con desplazamiento sigue en UTC: sumar 30 s
        # a las 02:30-07:00 da 09:30:30Z, no 02:30:30Z.
        self.assertEqual(
            tabla_esperas.reanuda_en("despliegue_en_curso", "2026-11-18T02:30:00-07:00"),
            "2026-11-18T09:30:30Z",
        )

    def test_una_orden_escrita_a_mano_exige_la_hora_canonica(self):
        from motor.encargos.registro import EventoIlegal

        datos = {
            "motivo": "espera ordenada", "reanuda_en": "20261118T100000Z",
            "hora": H0, "clase": "orden_espera", "abierta": H0, "tope": "1 intentos",
        }
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            with self.assertRaises(EventoIlegal):
                self.reg.anotar(
                    self.enc, {"kind": "espera", "data": datos}, f"mano:{self.enc}"
                )

    def test_un_espera_guardada_antes_coteja_igual_con_los_campos_nuevos(self):
        guardada = {"motivo": "GitHub caído", "reanuda_en": H1, "hora": H0}
        self.reg.anotar(
            self.enc, {"kind": "espera", "data": guardada}, f"vieja:{self.enc}"
        )
        self.reg.anotar(
            self.enc,
            {"kind": "espera", "data": {**guardada, "clase": "github_pasajero",
                                        "abierta": H0, "tope": "2026-11-18T16:00:00Z"}},
            f"vieja:{self.enc}",
        )
        self.assertEqual(len([e for e in self.reg.eventos(self.enc)
                              if e["kind"] == "espera"]), 1)

    def test_dos_escrituras_nuevas_con_clase_distinta_chocan(self):
        datos = {
            "motivo": "GitHub caído", "reanuda_en": H1, "hora": H0,
            "clase": "github_pasajero", "abierta": H0, "tope": "2026-11-18T16:00:00Z",
        }
        self.reg.anotar(
            self.enc, {"kind": "espera", "data": datos}, f"repetida:{self.enc}"
        )
        with self.assertRaises(ClaveOcupada):
            self.reg.anotar(
                self.enc,
                {"kind": "espera",
                 "data": {**datos, "clase": "empuje_transitorio"}},
                f"repetida:{self.enc}",
            )


class OrdenEsperada(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)
        self.enc = _recibir(self.reg)

    def _correr(self, reanuda_en: str) -> tuple[int, str]:
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida), self.assertRaises(SystemExit) as alza:
            motor_cli.main([
                "espera", self.enc, "--base", str(self.base),
                "--motivo", "espera ordenada", "--reanuda-en", reanuda_en,
            ])
        return alza.exception.code, salida.getvalue()

    def test_con_el_interruptor_encendido_exige_hora_utc(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", True):
            codigo, _ = self._correr("nunca")
        self.assertNotEqual(codigo, 0)

    def test_con_el_interruptor_apagado_acepta_lo_de_hoy(self):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            codigo = motor_cli.main([
                "espera", self.enc, "--base", str(self.base),
                "--motivo", "espera ordenada", "--reanuda-en", "nunca",
            ])
        self.assertEqual(codigo, 0)
        datos = json.loads(salida.getvalue())
        self.assertEqual(datos["estado"], "esperando_reintento")

    def test_apagado_conserva_el_argumento_y_su_identidad(self):
        hora = "2026-11-18T02:30:00-07:00"
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()):
                motor_cli.main([
                    "espera", self.enc, "--base", str(self.base),
                    "--motivo", "espera ordenada", "--reanuda-en", hora,
                ])
        esperas = [e for e in self.reg.eventos(self.enc) if e["kind"] == "espera"]
        self.assertEqual(len(esperas), 1)
        self.assertEqual(esperas[0]["data"]["reanuda_en"], hora)


class TablaApagada(unittest.TestCase):
    """Con el interruptor apagado, `hoy` da los intervalos de f30430f."""

    def test_cada_clase_espera_lo_mismo_que_la_base(self):
        base = H0
        casos = {
            "revision_deepseek": "2026-11-18T10:01:00Z",
            "lectura_deepseek_fallida": "2026-11-18T10:02:00Z",
            "checks_merge": "2026-11-18T10:02:00Z",
            "despliegue_en_curso": "2026-11-18T10:00:30Z",
            "despliegue_sin_receta": "2026-11-18T10:05:00Z",
            "autoridad": "2026-11-18T10:00:30Z",
            "conductor_ocupado": "2026-11-18T10:00:30Z",
            "observacion_comprobador": "2026-11-18T10:00:30Z",
            "sin_puerto_github": "2026-11-18T10:05:00Z",
            "cabeza_desviada": "2026-11-18T10:01:00Z",
            "base_movida": "2026-11-18T10:02:00Z",
            "acceso_kimi": H0,
            "sesion_local": "2026-11-18T10:30:00Z",
            "repo": "2026-11-18T10:05:00Z",
            "empuje": "2026-11-18T10:02:00Z",
            "empuje_transitorio": "2026-11-18T10:02:00Z",
            "github_pasajero": "2026-11-18T10:02:00Z",
            "preparacion_lanzamiento": "2026-11-18T10:05:00Z",
            "aprobado_sin_novedad": "2026-11-18T10:05:00Z",
        }
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            for clase, esperado in casos.items():
                with self.subTest(clase=clase):
                    self.assertEqual(
                        tabla_esperas.reanuda_en(clase, base), esperado
                    )

    def test_la_escalera_gasta_un_paso_por_intento(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            self.assertEqual(
                tabla_esperas.reanuda_en("destino_ocupado", H0, 1),
                "2026-11-18T10:15:00Z",
            )
            self.assertEqual(
                tabla_esperas.reanuda_en("destino_ocupado", H0, 2),
                "2026-11-18T10:30:00Z",
            )
            self.assertEqual(
                tabla_esperas.reanuda_en("destino_ocupado", H0, 9),
                "2026-11-18T14:00:00Z",
            )

    def test_el_tope_es_la_hora_de_vencimiento_o_los_intentos(self):
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            self.assertEqual(
                tabla_esperas.tope_de("checks_merge", H0), "2026-11-18T11:00:00Z"
            )
            self.assertEqual(tabla_esperas.tope_de("empuje", H0), "2 intentos")
