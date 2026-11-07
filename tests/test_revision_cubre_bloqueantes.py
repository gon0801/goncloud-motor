"""Una revisión concluyente manda sobre los bloqueantes independientes que cubre.

Si una revisión concluyente de la versión vigente (completa o corrección) no
vuelve a reportar abierto un bloqueante independiente incluido en su alcance,
ese bloqueante queda resuelto con esa revisión como evidencia. Un
no_concluyente, una inspección pendiente o un instrumento fallido no cierran
nada por omisión. La omisión del revisor independiente nunca cierra un hallazgo
de DeepSeek.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import _sin_avance, ahora_iso, recibir
from motor.encargos.comprobacion import firma_producto, validar_juicio
from motor.encargos.dominio import (
    AlcanceRevision,
    Hallazgo,
    _bloques_comunes,
    alcance_de,
    bloqueantes_abiertos,
    reducir,
    vista,
)
from motor.encargos.registro import Registro
from tests.entorno import HAY_AISLAMIENTO, SIN_AISLAMIENTO


def _alcance(hallazgos: tuple[Hallazgo, ...], clase: str = "correccion") -> AlcanceRevision:
    return AlcanceRevision(
        clase=clase,
        version="v2",
        base_version="v1",
        abiertos=hallazgos,
        tocadas=("malo.txt",),
        conservadas=(),
        manifiesto=(("malo.txt", "h2"),),
    )


def _abierto(hid: str, titulo: str = "") -> Hallazgo:
    return Hallazgo(
        id=hid,
        gravedad="bloqueante",
        criterio="comportamiento",
        titulo=titulo or hid,
        reproduccion="cat malo.txt",
        ancla="malo.txt:1",
    )


def _abierto_con(hid: str, titulo: str) -> Hallazgo:
    return Hallazgo(
        id=hid,
        gravedad="bloqueante",
        criterio="comportamiento",
        titulo=titulo,
        reproduccion="cat malo.txt",
        ancla="malo.txt:1",
    )


def _juicio(datos: dict, alcance: AlcanceRevision, externos=()):
    return validar_juicio(
        {"version": "v2", **datos},
        alcance,
        externos=externos,
    )


class LaRevisionConcluyenteCierraLoCubierto(unittest.TestCase):
    def test_los_que_ya_no_reporta_quedan_resueltos_con_la_revision_como_evidencia(self):
        alcance = _alcance((_abierto("h_a"), _abierto("h_b")))
        valido = _juicio(
            {"veredicto": "aprobado", "evidencia": "cat malo.txt sale limpio"},
            alcance,
        )
        self.assertEqual(valido.veredicto, "aprobado")
        resueltos = {h.id: h for h in valido.hallazgos if h.estado == "resuelto"}
        self.assertEqual(set(resueltos), {"h_a", "h_b"})
        self.assertTrue(all(not h.cierre_evidencia for h in resueltos.values()))
        self.assertNotIn("h_a", valido.resueltos)
        self.assertNotIn("h_b", valido.resueltos)

    def test_un_bloqueante_redeclarado_sigue_abierto_y_los_omitidos_se_cerran(self):
        alcance = _alcance((_abierto("h_a"), _abierto("h_b"), _abierto("h_c")))
        valido = _juicio(
            {
                "veredicto": "cambios",
                "evidencia": "h_c sigue roto",
                "bloqueantes": [
                    {
                        "id": "h_c",
                        "criterio": "comportamiento",
                        "titulo": "h_c",
                        "reproduccion": "cat malo.txt",
                        "ancla": "malo.txt:1",
                    }
                ],
            },
            alcance,
        )
        self.assertEqual(valido.veredicto, "cambios")
        estado = {h.id: h.estado for h in valido.hallazgos}
        self.assertEqual(estado.get("h_a"), "resuelto")
        self.assertEqual(estado.get("h_b"), "resuelto")
        self.assertEqual(estado.get("h_c"), "abierto")

    def test_no_concluyente_no_cierra_nada_por_omision(self):
        alcance = _alcance((_abierto("h_a"), _abierto("h_b")))
        valido = _juicio(
            {
                "veredicto": "no_concluyente",
                "evidencia": "no pude ejecutar el producto",
            },
            alcance,
        )
        self.assertEqual(valido.veredicto, "no_concluyente")
        self.assertTrue(
            all(h.estado == "abierto" for h in valido.hallazgos if h.id in {"h_a", "h_b"})
        )

    def test_un_instrumento_fallido_no_cierra_nada_por_omision(self):
        alcance = _alcance((_abierto("h_a"), _abierto("h_b")))
        valido = _juicio(
            {
                "veredicto": "cambios",
                "evidencia": "el comando de comprobacion no corre",
                "instrumento": {"causa": "SyntaxError en el comando declarado"},
            },
            alcance,
        )
        self.assertEqual(valido.veredicto, "cambios")
        self.assertTrue(
            all(h.estado == "abierto" for h in valido.hallazgos if h.id in {"h_a", "h_b"})
        )

    def test_cambios_sin_reporte_y_sin_evidencia_no_concluye_y_no_cierra(self):
        alcance = _alcance((_abierto("h_a"),))
        valido = _juicio({"veredicto": "cambios"}, alcance)
        self.assertEqual(valido.veredicto, "no_concluyente")
        self.assertNotIn(
            "h_a", {h.id for h in valido.hallazgos if h.estado == "resuelto"}
        )

    def test_una_omision_del_revisor_no_cierra_un_hallazgo_de_deepseek(self):
        alcance = _alcance((_abierto("h_ind"),))
        deepseek = _abierto("h_ds")
        valido = _juicio(
            {"veredicto": "aprobado", "evidencia": "todo bien"},
            alcance,
            externos=(deepseek,),
        )
        cerrados = {h.id for h in valido.hallazgos if h.estado == "resuelto"}
        self.assertIn("h_ind", cerrados)
        self.assertNotIn("h_ds", cerrados)
        self.assertNotIn("h_ds", {h.id for h in valido.hallazgos})

    def test_la_aprobacion_sin_evidencia_sigue_siendo_un_defecto(self):
        alcance = _alcance((_abierto("h_a"),))
        with self.assertRaises(Exception) as ctx:
            _juicio({"veredicto": "aprobado"}, alcance)
        self.assertIn("evidencia", str(ctx.exception))

    def test_los_resueltos_nombrados_siguen_cerrando_como_hoy(self):
        alcance = _alcance((_abierto("h_a"), _abierto("h_b")))
        valido = _juicio(
            {
                "veredicto": "aprobado",
                "evidencia": "cat malo.txt sale limpio",
                "resueltos": ["h_a"],
            },
            alcance,
        )
        self.assertEqual(valido.resueltos, ("h_a",))
        self.assertEqual(valido.veredicto, "aprobado")


class Recorrido:
    """Harness sin LLM: la prueba hace de agente entre incorporaciones."""

    def __init__(self, tmp: str, clave: str, texto: str, **kw):
        from motor.encargos.ciclo import _incorporar

        self._incorporar = _incorporar
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        self.hora = ahora_iso()
        self.enc = recibir(self.reg, texto, clave, self.hora, **kw)["encargo"]
        self.espacio = self.base / "espacio" / self.enc
        self.espacio.mkdir(parents=True, exist_ok=True)
        self.d = self.base / "ejecucion" / self.enc
        self.d.mkdir(parents=True, exist_ok=True)
        self.resultado = self.d / "resultado.json"

    def fold(self):
        return self.reg.fold(self.enc)

    def incorporar(self):
        e = self.fold()
        n = e.generacion + 1
        self.reg.anotar(
            self.enc,
            {"kind": "reserva", "data": {"rol": e.rol or "responsable", "generacion": n}},
            f"t-reserva:{n}",
        )
        self.reg.anotar(
            self.enc, {"kind": "reserva_cerrada", "data": {}}, f"t-cerrada:{n}"
        )
        self.reg.anotar(
            self.enc,
            {
                "kind": "estrategia",
                "data": {
                    "rol": e.rol or "responsable",
                    "estrategia": e.estrategia,
                    "motivo": e.motivo,
                    "siguiente": e.siguiente,
                },
            },
            f"t-rol:{n}",
        )
        return self._incorporar(self.reg, self.fold(), self.espacio, self.hora, self.d)

    def juicio(self, datos: dict) -> None:
        (self.d / "revision.json").write_text(
            json.dumps(
                {
                    "observaciones": str(self.d / "observaciones.json"),
                    "resultado": str(self.resultado),
                    "version": firma_producto(self.espacio),
                }
            )
        )
        datos.setdefault("version", firma_producto(self.espacio))
        self.resultado.write_text(json.dumps(datos))

    def eventos(self, kind: str) -> list[dict]:
        return [e for e in self.reg.eventos(self.enc) if e["kind"] == kind]


def _bloqueante(hid: str, titulo: str, reproduccion: str = "cat malo.txt") -> dict:
    return {
        "id": hid,
        "criterio": "comportamiento",
        "titulo": titulo,
        "reproduccion": reproduccion,
        "ancla": "malo.txt:1",
    }


class LaRegresionDeLasRevisiones12231Y12324(unittest.TestCase):
    """Caso enc_fb7358452cdfe01b reconstruido con juicios mínimos.

    Nueve bloqueantes abiertos en alcance corrección: la primera revisión
    reporta uno abierto y la segunda cero (el revisor escribe que el último
    quedó resuelto). Antes del arreglo el estado conservaba los nueve y la
    escalera escalaba a relevo y a entregar con pendientes.
    """

    def test_los_omitidos_se_cerran_y_el_encargo_llega_al_cierre_sin_escalera(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "cubierto-1", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            ids = [f"h_{i}" for i in range(9)]
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [
                        _bloqueante(h, f"bloqueante {h}") for h in ids
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual(len(bloqueantes_abiertos(e)), 9)
            # Revisión 12231: alcance corrección que cubre los 9, reporta 1.
            (r.espacio / "malo.txt").write_text("Y")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "ocho quedaron resueltos; este sigue",
                    "bloqueantes": [_bloqueante(ids[8], "bloqueante h_8")],
                }
            )
            e = r.incorporar()
            abiertos = bloqueantes_abiertos(e)
            self.assertEqual([h.id for h in abiertos], [ids[8]])
            escalar = [
                ev for ev in r.eventos("sin_progreso") if ev["data"].get("escalar")
            ]
            self.assertEqual(escalar, [])
            # Revisión 12324: misma versión, reporta 0 y dice que quedó resuelto.
            (r.espacio / "malo.txt").write_text("B")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "cat malo.txt -> B; el último bloqueante quedó resuelto",
                }
            )
            e = r.incorporar()
            self.assertEqual(len(bloqueantes_abiertos(e)), 0)
            self.assertEqual(
                [
                    ev
                    for ev in r.eventos("sin_progreso")
                    if ev["data"].get("escalar")
                ],
                [],
            )
            self.assertEqual(r.eventos("cierre_forzado"), [])
            self.assertEqual(e.estado, "terminado")
            revision = r.eventos("revision")[-1]
            resueltos_en_evento = {
                h["id"] for h in revision["data"]["hallazgos"]
                if h.get("estado") == "resuelto"
            }
            self.assertIn(ids[8], resueltos_en_evento)


class ElCierrePorOmisionNoBorraUnaRedeclaracionConOtroId(unittest.TestCase):
    """Caso F1 (DeepSeek).

    El id del bloqueante declarado puede diferir del id del hallazgo abierto
    que redeclara: el revisor lo nombra distinto o el título deriva. El cierre
    por omisión solo cierra lo que la revisión ya no reporta; una
    redeclaración con otro id y el mismo contenido no es una omisión.
    """

    def _juicio_con_bloqueante(
        self, alcance: AlcanceRevision, bloqueante: dict
    ) -> object:
        return _juicio(
            {
                "veredicto": "cambios",
                "evidencia": "sigue roto",
                "bloqueantes": [bloqueante],
            },
            alcance,
        )

    def test_mismo_ancla_y_titulo_con_otro_id_deja_el_hallazgo_abierto(self):
        alcance = _alcance((_abierto_con("h_a", "falla el envio"),))
        valido = self._juicio_con_bloqueante(
            alcance,
            {
                "id": "F1",
                "criterio": "comportamiento",
                "titulo": "falla el envio",
                "reproduccion": "cat malo.txt",
                "ancla": "malo.txt:1",
            },
        )
        self.assertEqual(valido.veredicto, "cambios")
        estado = {h.id: (h.gravedad, h.estado) for h in valido.hallazgos}
        self.assertEqual(estado.get("h_a"), ("bloqueante", "abierto"))
        self.assertNotIn("F1", estado)

    def test_en_ruta_conservada_la_redeclaracion_con_otro_id_no_se_degrada(self):
        alcance = AlcanceRevision(
            clase="correccion",
            version="v2",
            base_version="v1",
            abiertos=(_abierto_con("h_a", "falla el envio"),),
            tocadas=("malo.txt",),
            conservadas=("malo.txt",),
            manifiesto=(("malo.txt", "h2"),),
        )
        valido = self._juicio_con_bloqueante(
            alcance,
            {
                "id": "F1",
                "criterio": "comportamiento",
                "titulo": "falla el envio",
                "reproduccion": "cat malo.txt",
                "ancla": "malo.txt:1",
            },
        )
        estado = {h.id: (h.gravedad, h.estado) for h in valido.hallazgos}
        self.assertEqual(estado.get("h_a"), ("bloqueante", "abierto"))
        self.assertNotIn("F1", estado)

    def test_otro_id_con_otro_contenido_si_queda_cerrado_por_omision(self):
        alcance = _alcance((_abierto_con("h_a", "falla el envio"),))
        valido = self._juicio_con_bloqueante(
            alcance,
            {
                "id": "F9",
                "criterio": "comportamiento",
                "titulo": "falta la migracion",
                "reproduccion": "python migra.py",
                "ancla": "migra.py:3",
            },
        )
        self.assertEqual(valido.veredicto, "cambios")
        estado = {h.id: h.estado for h in valido.hallazgos}
        self.assertEqual(estado.get("h_a"), "resuelto")
        self.assertEqual(estado.get("F9"), "abierto")

    def test_en_el_estado_la_redeclaracion_con_otro_id_mantiene_el_bloqueante(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "cubierto-3", "malo.txt con un bloqueante")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [_bloqueante("h_0", "bloqueante h_0")],
                }
            )
            e = r.incorporar()
            self.assertEqual({h.id for h in bloqueantes_abiertos(e)}, {"h_0"})
            # La revisión de la corrección redeclara el mismo defecto con
            # otro id: el estado conserva h_0 abierto y no crea h_0b.
            (r.espacio / "malo.txt").write_text("Y")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "sigue roto, con otro nombre",
                    "bloqueantes": [_bloqueante("h_0b", "bloqueante h_0")],
                }
            )
            e = r.incorporar()
            self.assertEqual({h.id for h in bloqueantes_abiertos(e)}, {"h_0"})
            h_0 = [h for h in e.fase_actual.hallazgos if h.id == "h_0"][-1]
            self.assertEqual(h_0.estado, "abierto")
            self.assertNotIn(
                "h_0b", {h.id for h in e.fase_actual.hallazgos}
            )


class ElCierrePorOmisionNoEsUnCierreDelPropietario(unittest.TestCase):
    """Caso h_54e00c90.

    El cierre por omisión lleva su evidencia en la revisión misma (el evento
    `revision` con su revision_id), no en cierre_evidencia: esa marca es la
    del propietario y además bloquea la reapertura en _fusionar. Por eso una
    redeclaración posterior con la misma reproducción reabre el hallazgo y el
    prompt no lo presenta como algo que el propietario declaró cerrado.
    """

    def test_la_redeclaracion_con_la_misma_reproduccion_reabre_sin_atribuir_al_propietario(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "cubierto-2", "malo.txt con dos bloqueantes")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [
                        _bloqueante("h_0", "bloqueante h_0"),
                        _bloqueante("h_1", "bloqueante h_1", "cat otro.txt"),
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual(
                {h.id for h in bloqueantes_abiertos(e)}, {"h_0", "h_1"}
            )
            # La revisión de la corrección solo redeclara h_1: h_0 queda
            # resuelto por omisión, sin marca de cierre del propietario.
            (r.espacio / "malo.txt").write_text("Y")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "h_1 sigue",
                    "bloqueantes": [
                        _bloqueante("h_1", "bloqueante h_1", "cat otro.txt")
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual([h.id for h in bloqueantes_abiertos(e)], ["h_1"])
            h_0 = [h for h in e.fase_actual.hallazgos if h.id == "h_0"][-1]
            self.assertEqual(h_0.estado, "resuelto")
            self.assertEqual(h_0.cierre_evidencia, "")
            self.assertNotIn(
                "El propietario declaró cerrados",
                "\n".join(_bloques_comunes(e)),
            )
            # La revisión siguiente vuelve a reportar h_0 con su reproducción
            # original: reabre y vuelve al responsable.
            (r.espacio / "malo.txt").write_text("Z")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "h_0 y h_1 siguen",
                    "bloqueantes": [
                        _bloqueante("h_0", "bloqueante h_0"),
                        _bloqueante("h_1", "bloqueante h_1", "cat otro.txt"),
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual(
                {h.id for h in bloqueantes_abiertos(e)}, {"h_0", "h_1"}
            )
            self.assertNotIn(
                "El propietario declaró cerrados",
                "\n".join(_bloques_comunes(e)),
            )


if __name__ == "__main__":
    unittest.main()
