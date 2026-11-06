"""La caché de plegados sobrevive a las pasadas del hilo de avisos."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.ciclo import recibir
from motor.encargos.registro import Registro
from motor.integraciones import directores

HORA = "2026-11-06T10:00:00Z"

TRANSPORTE = (
    "import json,sys\n"
    "sys.stdin.read()\n"
    "print(json.dumps({'recibo': 'ok'}))\n"
)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name)
        self.base = self.raiz / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)

    def encargo(self, clave: str, canal: str = "tg") -> str:
        ident = recibir(
            self.reg, f"pedido {clave}", clave, HORA, "hermes",
            canal=canal, via="directa",
        )["encargo"]
        self.reg.anotar(
            ident,
            {"kind": "fases", "data": {
                "fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": HORA,
            }},
            f"fase-{clave}",
        )
        return ident

    def transporte(self) -> None:
        guion = self.raiz / "transporte.py"
        guion.write_text(TRANSPORTE, encoding="utf-8")
        (self.base / "canal.json").write_text(
            json.dumps({
                "tg": {
                    "comando": [sys.executable, "-B", str(guion)],
                    "idempotente": True,
                }
            }),
            encoding="utf-8",
        )

    def dos_pasadas(self, contar: mock.Mock) -> int:
        self.transporte()
        self.encargo("uno")
        self.encargo("dos")
        drenador = directores.DrenadoAparte(self.base, tope_s=10.0)
        self.assertTrue(drenador.lanzar())
        drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")
        primera = contar.call_count
        self.assertGreater(primera, 0, "la primera pasada pliega los encargos")
        contar.reset_mock()
        self.assertTrue(drenador.lanzar(), "terminada la pasada se puede lanzar otra")
        drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")
        return primera


class CacheEntrePasadas(Base):
    def test_la_segunda_pasada_sin_eventos_nuevos_no_vuelve_a_plegar(self):
        with mock.patch.object(
            Registro, "_plegar", autospec=True, wraps=Registro._plegar
        ) as plegar:
            self.dos_pasadas(plegar)
            self.assertEqual(
                plegar.call_count, 0,
                "la segunda pasada reutiliza los plegados de la primera",
            )


class EventoDeOtraInstancia(Base):
    def test_lo_escrito_por_otra_instancia_entre_pasadas_aparece_en_la_siguiente(self):
        self.transporte()
        ident = self.encargo("uno")
        drenador = directores.DrenadoAparte(self.base, tope_s=10.0)
        self.assertTrue(drenador.lanzar())
        drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")

        otro = Registro(self.base)
        self.addCleanup(otro.close)
        otro.anotar(
            ident,
            {"kind": "procedimiento", "data": {"nota": "nueva", "hora": HORA}},
            "nota-externa",
        )

        vistos: dict[str, object] = {}
        original = Registro.fold

        def espia(self, encargo, hasta=None):
            estado = original(self, encargo, hasta)
            if encargo == ident and hasta is None:
                vistos["estado"] = estado
            return estado

        with mock.patch.object(Registro, "fold", espia):
            self.assertTrue(drenador.lanzar())
            drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")
        self.assertEqual(vistos["estado"].procedimiento, ("nueva",))


class FoldHastaCacheado(Base):
    def test_hasta_cacheado_entre_instancias_no_devuelve_eventos_posteriores(self):
        ident = self.encargo("uno")
        corte = self.reg.ultimo_evento(ident)
        antes = self.reg.fold(ident, hasta=corte)

        self.reg.anotar(
            ident,
            {"kind": "ajuste", "data": {"texto": "posterior", "hora": HORA}},
            "ajuste-posterior",
        )

        otro = Registro(self.base)
        self.addCleanup(otro.close)
        with mock.patch.object(
            Registro, "_plegar", autospec=True, wraps=Registro._plegar
        ) as plegar:
            despues = otro.fold(ident, hasta=corte)
            self.assertEqual(
                plegar.call_count, 0,
                "la caché de (encargo, hasta) se comparte entre instancias",
            )
        self.assertEqual(despues, antes)
        self.assertEqual(despues.ajustes, ())


if __name__ == "__main__":
    unittest.main()
