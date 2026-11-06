"""El fold incremental del registro: mismos estados, sin releer la historia."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.dominio import reducir
from motor.encargos.registro import Registro

HORA = "2026-11-05T10:00:00Z"


def desde_cero(reg: Registro, encargo: str, hasta: int | None = None):
    estado = None
    for ev in reg.eventos(encargo):
        if hasta is not None and ev["id"] > hasta:
            break
        estado = reducir(estado, ev)
    return estado


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)

    def crear(self, encargo: str = "enc_1") -> str:
        self.reg.anotar(
            encargo,
            {
                "kind": "entrega",
                "data": {
                    "id": encargo,
                    "clave": encargo,
                    "huella": "h",
                    "texto": "pedido",
                    "hora": HORA,
                },
            },
            f"entrega:{encargo}",
        )
        return encargo

    def nota(self, encargo: str, nota: str) -> str:
        return self.reg.anotar(
            encargo,
            {"kind": "procedimiento", "data": {"nota": nota, "hora": HORA}},
            f"nota:{encargo}:{nota}",
        )


class Incremental(Base):
    def test_incremental_y_desde_cero_son_iguales_tras_varios_anotar(self):
        encargo = self.crear()
        self.nota(encargo, "n0")
        self.assertEqual(self.reg.fold(encargo), desde_cero(self.reg, encargo))
        for i in range(1, 6):
            self.nota(encargo, f"n{i}")
            self.assertEqual(self.reg.fold(encargo), desde_cero(self.reg, encargo))
            self.assertEqual(self.reg.fold(encargo).procedimiento, tuple(
                f"n{j}" for j in range(i + 1)
            ))
        otro = self.crear("enc_2")
        self.nota(otro, "otra")
        self.assertEqual(self.reg.fold(encargo), desde_cero(self.reg, encargo))

    def test_una_segunda_instancia_ve_lo_escrito_por_otra_conexion(self):
        encargo = self.crear()
        self.reg.fold(encargo)
        otro = Registro(self.base)
        self.addCleanup(otro.close)
        otro.anotar(
            encargo,
            {"kind": "ajuste", "data": {"texto": "corrige", "hora": HORA}},
            "ajuste-externo",
        )
        actual = self.reg.fold(encargo)
        self.assertEqual(actual.ajustes, ("corrige",))
        self.assertEqual(actual, desde_cero(self.reg, encargo))

    def test_un_insert_directo_en_la_misma_conexion_se_ve(self):
        encargo = self.crear()
        self.reg.fold(encargo)
        self.reg.db.execute(
            "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
            (HORA, encargo, "procedimiento", '{"nota": "directa", "hora": "x"}', "directa"),
        )
        self.assertEqual(self.reg.fold(encargo).procedimiento, ("directa",))

    def test_ultimos_da_maximo_y_conteo_por_encargo(self):
        self.crear("a")
        self.nota("a", "n0")
        self.nota("a", "n1")
        self.crear("b")
        esperado = {
            ident: (self.reg.ultimo_evento(ident), len(self.reg.eventos(ident)))
            for ident in self.reg.ids()
        }
        self.assertEqual(self.reg.ultimos(), esperado)

    def test_sin_eventos_nuevos_no_vuelve_a_leer_los_eventos(self):
        encargo = self.crear()
        self.nota(encargo, "n0")
        self.reg.fold(encargo)
        anterior = self.reg.ultimo_evento(encargo)
        with mock.patch.object(self.reg, "_rango", wraps=self.reg._rango) as leidos:
            self.assertEqual(self.reg.fold(encargo).procedimiento, ("n0",))
            self.assertEqual(leidos.call_count, 0)
            self.nota(encargo, "n1")
            nuevo = self.reg.ultimo_evento(encargo)
            self.assertEqual(self.reg.fold(encargo).procedimiento, ("n0", "n1"))
            leidos.assert_called_once_with(encargo, anterior, nuevo)

    def test_el_plegado_completo_cachea_el_token_de_su_estado(self):
        encargo = self.crear()
        self.nota(encargo, "n0")
        with mock.patch.object(self.reg, "_ultimo_y_conteo", return_value=(1, 1)):
            self.assertEqual(self.reg.fold(encargo).procedimiento, ("n0",))
        self.assertEqual(self.reg.fold(encargo).procedimiento, ("n0",))
        self.assertEqual(self.reg.fold(encargo), desde_cero(self.reg, encargo))

    def test_con_un_evento_nuevo_lee_solo_el_rango_nuevo(self):
        encargo = self.crear()
        self.nota(encargo, "n0")
        self.reg.fold(encargo)
        anterior = self.reg.ultimo_evento(encargo)
        self.nota(encargo, "n1")
        nuevo = self.reg.ultimo_evento(encargo)
        with mock.patch.object(self.reg, "_rango", wraps=self.reg._rango) as leidos:
            self.assertEqual(self.reg.fold(encargo).procedimiento, ("n0", "n1"))
        leidos.assert_called_once_with(encargo, anterior, nuevo)

    def test_si_baja_el_maximo_o_el_conteo_no_cuadra_se_reconstruye(self):
        encargo = self.crear()
        self.nota(encargo, "n0")
        self.nota(encargo, "n1")
        self.reg.fold(encargo)
        db = sqlite3.connect(self.base / "registro.sqlite")
        self.addCleanup(db.close)
        db.execute("DROP TRIGGER events_no_delete")
        db.execute(
            "DELETE FROM events WHERE encargo = ? AND kind = 'procedimiento' "
            "AND data LIKE '%n1%'",
            (encargo,),
        )
        db.commit()
        actual = self.reg.fold(encargo)
        self.assertEqual(actual.procedimiento, ("n0",))
        self.assertEqual(actual, desde_cero(self.reg, encargo))


class Hasta(Base):
    def test_hasta_no_devuelve_eventos_posteriores(self):
        encargo = self.crear()
        self.nota(encargo, "n0")
        corte = self.reg.ultimo_evento(encargo)
        self.nota(encargo, "n1")
        estado = self.reg.fold(encargo, hasta=corte)
        self.assertEqual(estado.procedimiento, ("n0",))
        self.assertEqual(estado, desde_cero(self.reg, encargo, hasta=corte))
        self.nota(encargo, "n2")
        self.assertEqual(self.reg.fold(encargo, hasta=corte).procedimiento, ("n0",))
        self.assertEqual(self.reg.fold(encargo).procedimiento, ("n0", "n1", "n2"))

    def test_hasta_antes_del_primer_evento_es_none(self):
        encargo = self.crear()
        self.assertIsNone(self.reg.fold(encargo, hasta=0))
