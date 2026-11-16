"""El lector aprende los hechos de entrega antes de que haya escritores."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from motor.encargos.dominio import Encargo, TransicionIlegal, reducir
from motor.encargos.registro import Evento, Registro
from motor.encargos.reductor import TABLA
from tools.plegado import Plegado, diferencias


def _hecho(kind: str, **data: object) -> dict:
    return {"kind": kind, "data": data}


def _pedido() -> Encargo:
    e = reducir(None, _hecho("entrega", id="x", clave="k", huella="h",
                             texto="pedido", hora="t0"), nuevo=True)
    return reducir(e, _hecho("fases", fases=[{"id": "F", "resultado": "r"}]),
                  nuevo=True)


class ReductorEntregaV4(unittest.TestCase):
    def test_cinco_filas_con_propiedades_y_sin_escritores(self) -> None:
        from motor.encargos.detencion import DETENCIONES

        esperadas = {
            "pr_observado": ("fase", False, True),
            "fase_cancelada": ("fase", True, False),
            "pr_otra_base": ("fase", True, False),
            "limpieza": ("encargo", False, True),
            "limpio": ("encargo", False, False),
        }
        for kind, propiedades in esperadas.items():
            with self.subTest(kind=kind):
                fila = TABLA[kind]
                self.assertEqual(
                    (fila.alcance, fila.corta_series, fila.marcador), propiedades,
                )
                self.assertIsNotNone(fila.validar)
        self.assertIn("pr_otra_base", DETENCIONES)

    def test_cada_hecho_legal_se_guarda_en_un_registro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registro = Registro(Path(tmp))
            registro.append_many("x", (
                Evento("entrega", {"id": "x", "clave": "k", "huella": "h",
                                    "texto": "pedido", "hora": "t0"}, "entrega:x"),
                Evento("fases", {"fases": [{"id": "F", "resultado": "r"}]}, "fases:x"),
                Evento("pr_observado", {"fase": "F", "estado": "abierto", "numero": 9,
                                         "head": "h", "base": "main", "merge_commit": "",
                                         "hora": "t1"}, "pr:x"),
                Evento("pr_otra_base", {"fase": "F", "numero": 9, "base": "otra",
                                         "hora": "t2"}, "base:x"),
                Evento("orden", {"accion": "continuar", "hora": "t3"}, "continuar:x"),
                Evento("fase_cancelada", {"fase": "F", "numero": 9,
                                           "causa": "pr_cerrado", "origen": "",
                                           "hora": "t4"}, "cancelar-fase:x"),
                Evento("limpieza", {"recurso": "rama", "clave": "encargo/x",
                                     "resultado": "borrado", "intento": 1,
                                     "hora": "t5"}, "limpieza:x"),
                Evento("limpio", {"hora": "t6"}, "limpio:x"),
            ))
            e = registro.fold("x")
            self.assertEqual((e.estado, e.canceladas, e.hora_final, e.limpio),
                             ("cancelado", ("F",), "t4", "t6"))
            self.assertEqual(e.prs[0].aceptada_en, "otra")
            self.assertEqual(e.limpieza[0], ("rama", "encargo/x", "borrado", 1, "t5"))

    def test_rechazo_de_escritura_por_kind(self) -> None:  # no-roja-en-base: caracteriza rechazos; los casos legales demuestran que se reconocen los kinds
        e = _pedido()
        casos = (
            ("pr_observado", {"fase": "F", "estado": "inventado"}),
            ("fase_cancelada", {"fase": "desconocida", "causa": "pr_cerrado"}),
            ("pr_otra_base", {"fase": "F", "numero": None, "base": "otra"}),
            ("limpieza", {"recurso": "desconocido", "clave": "x"}),
            ("limpio", {"hora": "t"}),
        )
        for kind, data in casos:
            with self.subTest(kind=kind):
                with self.assertRaises(TransicionIlegal):
                    reducir(e, {"kind": kind, "data": data}, nuevo=True)

    def test_aplicar_malos_datos_no_lanza_ni_con_infinity(self) -> None:  # no-roja-en-base: caracteriza la ruta histórica además de probar los cinco kinds por separado
        e = _pedido()
        for kind in ("pr_observado", "fase_cancelada", "pr_otra_base", "limpieza", "limpio"):
            for data in ({}, {"fase": []}, {"fase": float("nan"),
                                           "numero": float("inf")},
                         {"fase": "F", "estado": [], "recurso": [],
                          "intento": float("nan")},
                         {"fase": "ajena", "intento": "ajeno"}):
                with self.subTest(kind=kind, data=data):
                    resultado = reducir(e, {"kind": kind, "data": data}, nuevo=False)
                    self.assertEqual(asdict(resultado), asdict(e))

    def test_claves_nuevas_se_omiten_solo_si_estan_declaradas(self) -> None:
        base = [Plegado("x", 1, {"estado": "recibido"})]
        arbol = [Plegado("x", 1, {"estado": "recibido", "limpio": "",
                                 "hora_final": "", "prs": []})]
        resultado = diferencias("falso", base, arbol)
        self.assertEqual((resultado.diferencias, resultado.forma), ((), ()))
        arbol = [Plegado("x", 1, {"estado": "recibido", "desconocida": ""})]
        self.assertEqual(len(diferencias("falso", base, arbol).forma), 1)
        base = [Plegado("x", 1, {"estado": "recibido", "limpio": "t1"})]
        arbol = [Plegado("x", 1, {"estado": "recibido", "limpio": "t2"})]
        self.assertEqual(diferencias("falso", base, arbol).diferencias[0].campos,
                         ("limpio",))

    def test_observar_no_pierde_la_base_aceptada(self) -> None:
        from motor.encargos.entrega.tipos import PrCerrado

        e = _pedido()
        datos = {"fase": "F", "estado": "abierto", "numero": 9,
                 "head": "h", "base": "main", "merge_commit": "", "hora": "t1"}
        observado = reducir(e, {"kind": "pr_observado", "data": datos}, nuevo=True)
        observado = reducir(observado, _hecho("pr_otra_base", fase="F",
                                              numero=9, base="otra", hora="t2"), nuevo=True)
        aceptado = reducir(observado, _hecho("orden", accion="continuar", hora="t3"),
                           nuevo=True)
        cerrado = reducir(aceptado, {"kind": "pr_observado",
                                     "data": {**datos, "estado": "cerrado"}}, nuevo=True)
        self.assertIsInstance(cerrado.prs[0].pr, PrCerrado)
        self.assertEqual(cerrado.prs[0].aceptada_en, "otra")
        self.assertEqual(cerrado.otra_base_de, ("", ""))

    def test_terminado_reabierto_pierde_hora_final_y_limpio(self) -> None:
        from tests.soporte_situacion import evolucionar

        previo = _pedido()
        e = evolucionar(previo, estado="terminado",
                       cierre_forzado_fase=previo.fase_actual, cierre_forzado_huella="h",
                       limpio="t2", hora_final="t1")
        reabierto = reducir(e, _hecho("orden", accion="reabrir", fase="F",
                                      cierre="h", hora="t3"),
                           nuevo=False)
        self.assertEqual((reabierto.hora_final, reabierto.limpio), ("", ""))


if __name__ == "__main__":
    unittest.main()
