"""Las salidas del paso del revisor antes de su juicio se guardan enteras o nada,
y un intento ya incorporado no se decide otra vez."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import tests.test_flujo_revisiones as flujo
from motor.encargos import ciclo
from motor.encargos import registro as modulo_registro
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.necesidades import responder
from tests.arnes_corte import cortar_en_cada_commit, filas
from tests.test_cerrar_con_juicio import (
    EPOCH,
    HORA,
    _barrer,
    _cambios_que_cierran_un_bloqueante,
    _incorporar,
    _reservar_turno,
    _turno_del_revisor_terminado,
    _vuelta,
)

APROBADO = {"veredicto": "aprobado", "evidencia": "lei dato.txt: 42"}
PEDIDO = {"id": "e1", "operacion": "publicar", "clave": "k1", "cuerpo": "hola"}
NO_CONCLUYENTE = {
    "veredicto": "no_concluyente",
    "evidencia": "falta acceso a la fuente externa",
    "inspeccion_faltante": "consultar metricas de cuenta",
}


def setUpModule():
    flujo.sonda_sin_aislamiento_del_host()


def _pedidos(contenido) -> Callable[[flujo.Recorrido], None]:
    def escribir(r: flujo.Recorrido) -> None:
        (flujo._interno(r) / "pedido-efecto.json").write_text(json.dumps(contenido))

    return escribir


def _sin_pedidos(r: flujo.Recorrido) -> None:
    _pedidos([1])(r)
    (r.d / "outcome.json").write_text("{}")
    r.reg.anotar(
        r.enc,
        {
            "kind": "instrumento_no_disponible",
            "data": {"clase": "pedido-efecto.json", "causa": "falló tres veces", "hora": HORA},
        },
        "t-sin-pedidos",
    )


def _juicio_del_revisor(
    raiz: Path, juicio: dict, antes: Callable[[flujo.Recorrido], None] | None = None
) -> flujo.Recorrido:
    """El responsable entrega dato.txt y el revisor deja `juicio`. `antes` corre
    entre las dos cosas."""
    r = flujo.Recorrido(str(raiz), "salida", "Crea dato.txt con 42")
    (r.espacio / "dato.txt").write_text("42")
    (flujo._interno(r) / "evidencia.txt").write_text("dato.txt contiene 42")
    r.incorporar()
    if antes is not None:
        antes(r)
    r.juicio(juicio)
    _reservar_turno(r)
    return r


def _sin_referencia(raiz: Path) -> flujo.Recorrido:
    r = _juicio_del_revisor(raiz, APROBADO)
    (r.d / "revision.json").unlink()
    return r


def _producto_cambiado(raiz: Path) -> flujo.Recorrido:
    r = _juicio_del_revisor(raiz, APROBADO)
    (r.espacio / "dato.txt").write_text("43")
    return r


def _inspeccion_sin_capacidad(raiz: Path) -> flujo.Recorrido:
    return _juicio_del_revisor(raiz, NO_CONCLUYENTE)


def _inspeccion_ya_respondida(raiz: Path) -> flujo.Recorrido:
    """El propietario respondió la inspección ligada y el revisor la vuelve a pedir."""
    r = _inspeccion_sin_capacidad(raiz)
    _incorporar(r)(r.reg)
    responder(
        r.reg, r.enc, r.fold().fase_actual.inspeccion_solicitud, "si",
        "propietario", "salida", HORA,
    )
    r.juicio(NO_CONCLUYENTE)
    _reservar_turno(r)
    return r


def _interrumpido_con_sesion(raiz: Path) -> flujo.Recorrido:
    """El dueño paró el turno del revisor después de que dejara su outcome.json."""
    r = _juicio_del_revisor(raiz, APROBADO)
    _turno_del_revisor_terminado(r)
    (r.d / "outcome.json").write_text(json.dumps({"sesion_runtime": "ses-1"}))
    r.reg.anotar(r.enc, {"kind": "orden", "data": {"accion": "parar", "hora": HORA}}, "t-parar")
    return r


# (nombre, escena, reintento, lo que cierra el lote del paso)
SALIDAS: tuple[tuple[str, Callable[[Path], flujo.Recorrido], Callable | None, str], ...] = (
    (
        "efectos sin enumerar",
        lambda raiz: _juicio_del_revisor(raiz, APROBADO, _pedidos([PEDIDO])), None, "",
    ),
    ("pedido de efecto ilegible", lambda raiz: _juicio_del_revisor(raiz, APROBADO, _pedidos([1])), None, ""),
    (
        "pedido de efecto omitido",
        lambda raiz: _juicio_del_revisor(raiz, APROBADO, _sin_pedidos), _barrer, "incorporado",
    ),
    ("juicio ilegible", lambda raiz: _juicio_del_revisor(raiz, {"veredicto": "quizas"}), None, ""),
    ("sin referencia", _sin_referencia, None, ""),
    ("producto cambiado durante la revisión", _producto_cambiado, None, ""),
    (
        "instrumento del revisor",
        lambda raiz: _juicio_del_revisor(raiz, {**APROBADO, "instrumento": "el comando no corrió"}),
        None, "",
    ),
    ("inspección ya respondida", _inspeccion_ya_respondida, None, ""),
    ("inspección sin capacidad concedida", _inspeccion_sin_capacidad, None, ""),
    ("intento interrumpido", _interrumpido_con_sesion, None, ""),
)


class SalidasTempranas(unittest.TestCase):
    def test_un_apagon_en_cualquier_commit_deja_cada_salida_entera(self):
        for nombre, escena_de, reintento, cierre in SALIDAS:
            with self.subTest(salida=nombre), tempfile.TemporaryDirectory() as tmp:
                escena = Path(tmp) / "escena"
                escena.mkdir()
                r = escena_de(escena)
                r.reg.close()

                cortar_en_cada_commit(
                    self, escena, r.base, r.enc, _incorporar(r),
                    reintento=reintento, cierra_el_lote=cierre,
                )

    def test_la_inspeccion_que_pide_el_juicio_queda_ligada_aunque_se_corte(self):
        with tempfile.TemporaryDirectory() as tmp:
            escena = Path(tmp) / "escena"
            escena.mkdir()
            r = _inspeccion_sin_capacidad(escena)
            r.reg.close()

            cortar_en_cada_commit(self, escena, r.base, r.enc, _incorporar(r))

            registro = modulo_registro.Registro(r.base)
            self.addCleanup(registro.close)
            enc = registro.fold(r.enc)
            self.assertTrue(enc.fase_actual.inspeccion_solicitud)
            self.assertEqual(
                [s.estado for s in enc.solicitudes if s.id == enc.fase_actual.inspeccion_solicitud],
                ["pregunta"],
            )


class IntentoYaIncorporado(unittest.TestCase):
    def _escena(self, tmp: str) -> tuple[flujo.Recorrido, int]:
        r = _cambios_que_cierran_un_bloqueante(Path(tmp))
        _turno_del_revisor_terminado(r)
        return r, len(filas(r.base))

    def test_el_barrido_cierra_el_intento_sin_decidir_otra_vez_el_juicio(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, previas = self._escena(tmp)
            _incorporar(r)(r.reg)
            juicio = filas(r.base)[previas:]
            r.reg.close()

            _vuelta(r.base)

            vuelta = filas(r.base)[previas + len(juicio):]
            self.assertEqual(
                [kind for kind, _ukey, _data in vuelta if kind in ("sin_progreso", "estrategia")], []
            )
            self.assertIn("intento_cerrado", [kind for kind, _ukey, _data in vuelta])

    def test_una_marca_ocupada_por_otro_hecho_no_salta_el_encargo_en_cada_vuelta(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, _previas = self._escena(tmp)
            enc = r.fold()
            r.reg.anotar(
                r.enc,
                {"kind": "procedimiento", "data": {"nota": "otro hecho", "hora": HORA}},
                f"incorporado:{enc.id}:{enc.generacion}",
            )
            previas = len(filas(r.base))
            r.reg.close()
            errores = io.StringIO()

            with mock.patch.object(modulo_registro, "_CONTEO", modulo_registro._Conteo()), \
                    redirect_stderr(errores):
                _vuelta(r.base)
            primera = filas(r.base)[previas:]
            _vuelta(r.base)

            self.assertIn("intento_cerrado", [kind for kind, _ukey, _data in primera])
            self.assertEqual(
                [
                    kind for kind, _ukey, _data in filas(r.base)[previas + len(primera):]
                    if kind in ("revision", "sin_progreso", "estrategia")
                ],
                [],
            )
            self.assertIn("rechazo=ajena kind=incorporado", errores.getvalue())

    def test_un_intento_interrumpido_sin_resultado_lanza_el_turno_siguiente_en_la_misma_vuelta(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _juicio_del_revisor(Path(tmp), APROBADO)
            _turno_del_revisor_terminado(r)
            r.reg.anotar(r.enc, {"kind": "orden", "data": {"accion": "parar", "hora": HORA}}, "t-parar")
            r.reg.close()
            _vuelta(r.base)
            registro = modulo_registro.Registro(r.base)
            self.addCleanup(registro.close)
            registro.anotar(
                r.enc, {"kind": "orden", "data": {"accion": "continuar", "hora": HORA}}, "t-continuar"
            )
            enc = registro.fold(r.enc)
            self.assertTrue(enc.intento_interrumpido)
            self.assertFalse(enc.intento)
            lanzados: list[str] = []

            with mock.patch(
                "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
            ), mock.patch(
                "motor.flota.runtime.exigir_poteto",
                return_value=(Path(registro.base) / "poteto.md", "digest"),
            ), mock.patch("motor.encargos.ciclo.observar", return_value="ausente"):
                ciclo.barrer(registro, HORA, EPOCH, lambda e, *_args: lanzados.append(e.rol))

            self.assertIn(enc.generacion, registro.fold(r.enc).incorporado)
            self.assertEqual(lanzados, ["revisor"])


if __name__ == "__main__":
    unittest.main()
