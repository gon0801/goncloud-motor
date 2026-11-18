"""5.4: las tres reglas de falta de avance y su aviso.

El reductor ya contaba los turnos y validaba el hecho (5.0); aquí se prueba el
escritor del barrido: el turno vivo, la racha de turnos y la racha de esperas.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo, reporte
from motor.encargos.registro import Registro
from motor.encargos.situacion import Detenido, FaltaDeAvance

H0 = "2026-11-18T10:00:00Z"
H59 = "2026-11-18T10:59:00Z"
H61 = "2026-11-18T11:01:00Z"
H6 = "2026-11-18T16:01:00Z"


def _recibir(reg: Registro, clave: str = "pedido-1") -> str:
    return ciclo.recibir(reg, "Crea dato.txt con 42", clave, H0)["encargo"]


def _eventos(reg: Registro, enc: str, kind: str) -> list[dict]:
    return [e for e in reg.eventos(enc) if e["kind"] == kind]


def _fase(reg: Registro, enc: str, hora: str = H0) -> None:
    reg.anotar(
        enc,
        {"kind": "fases", "data": {
            "fases": [{"id": "f", "resultado": "dato.txt con 42"}], "hora": hora,
        }},
        f"fases:{enc}",
    )


def _turno(reg: Registro, enc: str, i: int, hora: str) -> None:
    reg.anotar(
        enc,
        {"kind": "intento_abierto", "data": {
            "id": str(i), "pid": 1000 + i, "inicio": hora, "hora": hora,
        }},
        f"intento-abierto:{enc}:{i}",
    )
    reg.anotar(
        enc,
        {"kind": "intento_cerrado", "data": {"hora": hora}},
        f"intento-cerrado:{enc}:{i}",
    )


class Banco(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.reg = Registro(Path(self._tmp.name) / "base")
        self.addCleanup(self.reg.close)
        self.enc = _recibir(self.reg)

    def con_v5(self):
        return mock.patch("motor.encargos.politica.ESPERAS_V5", True)


class TurnoVivo(Banco):
    def test_una_reserva_sin_intento_se_detiene_a_los_sesenta_minutos(self):
        for arbol in ("incierto", "ausente"):
            with self.subTest(arbol=arbol):
                self.setUp()
                self.reg.anotar(
                    self.enc, {"kind": "reserva", "data": {"hora": H0}},
                    f"reserva:{self.enc}",
                )
                with self.con_v5():
                    antes = ciclo._falta_de_avance_de_turno_vivo(
                        self.reg, self.reg.fold(self.enc), arbol, None, H59
                    )
                    self.assertNotIn(antes.estado, ("deteniendo", "detenido"))
                    despues = ciclo._falta_de_avance_de_turno_vivo(
                        self.reg, self.reg.fold(self.enc), arbol, None, H61
                    )
                self.assertIn(despues.estado, ("deteniendo", "detenido"))
                self.assertEqual(despues.falta_de_avance_de, ("turno_vivo", H0, 0))

    def test_un_turno_vivo_con_herramienta_en_true_y_en_none(self):
        for herramienta in (True, None):
            with self.subTest(herramienta=herramienta):
                self.setUp()
                self.reg.anotar(
                    self.enc,
                    {"kind": "intento_abierto", "data": {
                        "id": "i", "pid": 7, "inicio": H0, "hora": H0,
                    }},
                    f"intento:{self.enc}",
                )
                with self.con_v5():
                    antes = ciclo._falta_de_avance_de_turno_vivo(
                        self.reg, self.reg.fold(self.enc), "vivo", herramienta, H59
                    )
                    self.assertNotIn(antes.estado, ("deteniendo", "detenido"))
                    despues = ciclo._falta_de_avance_de_turno_vivo(
                        self.reg, self.reg.fold(self.enc), "vivo", herramienta, H61
                    )
                self.assertIn(despues.estado, ("deteniendo", "detenido"))
                self.assertEqual(despues.falta_de_avance_de, ("turno_vivo", H0, 1))

    def test_una_actividad_reinicia_el_reloj(self):
        self.reg.anotar(
            self.enc,
            {"kind": "intento_abierto", "data": {
                "id": "i", "pid": 7, "inicio": H0, "hora": H0,
            }},
            f"intento:{self.enc}",
        )
        self.reg.anotar(
            self.enc,
            {"kind": "actividad", "data": {
                "observada": True, "hecho": "sigue compilando",
                "hora": "2026-11-18T10:30:00Z",
            }},
            f"actividad:{self.enc}",
        )
        with self.con_v5():
            a_los_31 = ciclo._falta_de_avance_de_turno_vivo(
                self.reg, self.reg.fold(self.enc), "vivo", True, H61
            )
            self.assertNotIn(a_los_31.estado, ("deteniendo", "detenido"))
            a_los_61 = ciclo._falta_de_avance_de_turno_vivo(
                self.reg, self.reg.fold(self.enc), "vivo", True,
                "2026-11-18T11:31:00Z",
            )
        self.assertIn(a_los_61.estado, ("deteniendo", "detenido"))

    def test_una_conciliacion_de_consulta_no_reinicia_el_reloj(self):
        _fase(self.reg, self.enc)
        self.reg.anotar(
            self.enc,
            {"kind": "intento_abierto", "data": {
                "id": "i", "pid": 7, "inicio": H0, "hora": H0,
            }},
            f"intento:{self.enc}",
        )
        for minuto in (2, 4, 6):
            self.reg.anotar(
                self.enc,
                {"kind": "conciliacion_fase", "data": {
                    "fase": "f", "paso": "consulta",
                    "hora": f"2026-11-18T10:0{minuto}:00Z",
                }},
                f"conciliacion:{self.enc}:{minuto}",
            )
        with self.con_v5():
            enc = ciclo._falta_de_avance_de_turno_vivo(
                self.reg, self.reg.fold(self.enc), "vivo", True, H61
            )
        self.assertIn(enc.estado, ("deteniendo", "detenido"))


class RachaDeTurnos(Banco):
    def test_ocho_turnos_detienen_y_siete_no(self):
        for turnos, detiene in ((7, False), (8, True)):
            with self.subTest(turnos=turnos):
                self.setUp()
                for i in range(turnos):
                    _turno(self.reg, self.enc, i, H0)
                with self.con_v5(), mock.patch(
                    "motor.encargos.ciclo._paso_entrega",
                    return_value=self.reg.fold(self.enc),
                ):
                    ciclo.barrer(self.reg, H61, 1.0, None, gh=None, umbral_s=10**9)
                enc = self.reg.fold(self.enc)
                if detiene:
                    self.assertIn(enc.estado, ("deteniendo", "detenido"))
                    self.assertEqual(enc.falta_de_avance_de[0], "turnos")
                else:
                    self.assertNotIn(enc.estado, ("deteniendo", "detenido"))
                    self.assertEqual(_eventos(self.reg, self.enc, "falta_de_avance"), [])

    def test_sin_avance_el_aviso_trae_la_hora_del_primer_evento(self):
        for i in range(8):
            _turno(self.reg, self.enc, i, H0)
        with self.con_v5(), mock.patch(
            "motor.encargos.ciclo._paso_entrega",
            return_value=self.reg.fold(self.enc),
        ):
            ciclo.barrer(self.reg, H61, 1.0, None, gh=None, umbral_s=10**9)
        enc = self.reg.fold(self.enc)
        self.assertEqual(enc.falta_de_avance_de, ("turnos", H0, 8))
        texto = reporte._detencion(enc)
        self.assertIn("8 turnos sin avance", texto)
        self.assertIn("03:00", texto)

    def test_el_bucle_de_producto_repetido_se_detiene_al_octavo(self):
        _fase(self.reg, self.enc)
        for i in range(8):
            self.reg.anotar(
                self.enc,
                {"kind": "producto_listo", "data": {
                    "fase": "f", "version": "v", "sha_git": "sha", "hora": H0,
                }},
                f"producto:{self.enc}:{i}",
            )
            self.reg.anotar(
                self.enc,
                {"kind": "instrumento_fallo", "data": {
                    "fuente": "agente", "causa": "misma", "hora": H0,
                }},
                f"instrumento:{self.enc}:{i}",
            )
            _turno(self.reg, self.enc, i, H0)
        with self.con_v5(), mock.patch(
            "motor.encargos.ciclo._paso_entrega",
            return_value=self.reg.fold(self.enc),
        ):
            ciclo.barrer(self.reg, H61, 1.0, None, gh=None, umbral_s=10**9)
        enc = self.reg.fold(self.enc)
        self.assertIn(enc.estado, ("deteniendo", "detenido"))
        self.assertEqual(enc.falta_de_avance_de[0], "turnos")


class RachaDeEsperas(Banco):
    def _espera(self, clase: str, hora: str, abierta: str, tope: str) -> None:
        self.reg.anotar(
            self.enc,
            {"kind": "espera", "data": {
                "motivo": "pendiente", "reanuda_en": H6, "hora": hora,
                "clase": clase, "abierta": abierta, "tope": tope,
            }},
            f"espera:{self.enc}:{clase}:{abierta}",
        )

    def test_dos_esperas_alternadas_se_detienen_a_las_seis_horas(self):
        self._espera("empuje_transitorio", H0, H0, H6)
        self.reg.anotar(
            self.enc, {"kind": "plazo", "data": {"hora": "2026-11-18T10:02:00Z"}},
            f"plazo:{self.enc}:1",
        )
        self._espera("github_pasajero", "2026-11-18T10:02:00Z",
                     "2026-11-18T10:02:00Z", H6)
        self.reg.anotar(
            self.enc, {"kind": "plazo", "data": {"hora": "2026-11-18T10:04:00Z"}},
            f"plazo:{self.enc}:2",
        )
        self._espera("empuje_transitorio", "2026-11-18T15:59:00Z",
                     "2026-11-18T15:59:00Z", "2026-11-18T16:59:00Z")
        with self.con_v5():
            enc = ciclo._falta_de_avance_de_esperas(
                self.reg, self.reg.fold(self.enc), H6
            )
        self.assertIn(enc.estado, ("deteniendo", "detenido"))
        self.assertEqual(enc.falta_de_avance_de, ("esperas", H0, 0))

    def test_un_avance_en_medio_reinicia_la_racha(self):
        self._espera("empuje_transitorio", H0, H0, H6)
        self.reg.anotar(
            self.enc, {"kind": "revision", "data": {"hora": "2026-11-18T12:00:00Z"}},
            f"revision:{self.enc}",
        )
        self._espera("github_pasajero", "2026-11-18T12:00:00Z",
                     "2026-11-18T12:00:00Z", "2026-11-18T18:00:00Z")
        with self.con_v5():
            enc = ciclo._falta_de_avance_de_esperas(
                self.reg, self.reg.fold(self.enc), H6
            )
        self.assertNotIn(enc.estado, ("deteniendo", "detenido"))

    def test_la_orden_de_espera_vacia_la_racha(self):
        self._espera("empuje_transitorio", H0, H0, H6)
        self.reg.anotar(
            self.enc,
            {"kind": "espera", "data": {
                "motivo": "orden", "reanuda_en": "2026-11-19T00:00:00Z",
                "hora": "2026-11-18T10:05:00Z", "clase": "orden_espera",
                "abierta": "2026-11-18T10:05:00Z", "tope": "1 intentos",
            }},
            f"espera:{self.enc}:orden",
        )
        self.reg.anotar(
            self.enc, {"kind": "plazo", "data": {"hora": "2026-11-19T00:00:00Z"}},
            f"plazo:{self.enc}:orden",
        )
        self._espera("proveedor", "2026-11-19T00:00:00Z",
                     "2026-11-19T00:00:00Z", "2026-11-19T06:00:00Z")
        with self.con_v5():
            enc = ciclo._falta_de_avance_de_esperas(
                self.reg, self.reg.fold(self.enc), "2026-11-19T00:01:00Z"
            )
        self.assertNotIn(enc.estado, ("deteniendo", "detenido"))

    def test_el_tope_de_la_clase_gana_en_la_misma_vuelta(self):
        self._espera("empuje_transitorio", H0, H0, H6)
        with self.con_v5():
            enc = ciclo._falta_de_avance_de_esperas(
                self.reg, self.reg.fold(self.enc), H6
            )
        # El tope de la clase venció a la misma hora: este camino no escribe.
        self.assertNotIn(enc.estado, ("deteniendo", "detenido"))
        self.assertEqual(_eventos(self.reg, self.enc, "falta_de_avance"), [])


class Aviso(unittest.TestCase):
    def test_los_tres_textos(self):
        casos = (
            (FaltaDeAvance("turnos", H0, 8), "8 turnos sin avance desde las 03:00"),
            (FaltaDeAvance("turno_vivo", H0, 2), "60 min sin ningún hecho nuevo"),
            (FaltaDeAvance("esperas", H0, 0),
             "360 min esperando sin avance, entre varias esperas, desde las 03:00"),
        )
        for causa, texto in casos:
            with self.subTest(regla=causa.regla):
                enc = type("E", (), {"situacion": Detenido(causa)})()
                self.assertIn(texto, reporte._detencion(enc))


class Apagado(Banco):
    def test_sin_el_interruptor_nada_de_esto_pasa(self):
        for i in range(8):
            _turno(self.reg, self.enc, i, H0)
        with mock.patch("motor.encargos.politica.ESPERAS_V5", False):
            ciclo.barrer(self.reg, H61, 1.0, None, gh=None, umbral_s=10**9)
        self.assertEqual(_eventos(self.reg, self.enc, "falta_de_avance"), [])
        self.assertNotIn(
            self.reg.fold(self.enc).estado, ("deteniendo", "detenido")
        )


if __name__ == "__main__":
    unittest.main()
