"""El sondeo de un canal caído solo lo detiene la lista cerrada de avisos vigentes.

Cada uno de los diez arreglos de avisos del 5 y 6 de noviembre se reconstruye
aquí con eventos mínimos: lo que no es un aviso vigente de un encargo activo
pendiente de entrega no bloquea el sondeo. El último caso es el que la lista
abierta todavía dejaba pasar: un `pendiente` huérfano de un encargo activo.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from motor.encargos.registro import Registro
from motor.integraciones import canales, directores

HORA = "2026-11-01T10:00:00Z"
T0 = 1_000_000.0

ENVIO = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "ident=os.environ['MOTOR_AVISO_ID']\n"
    "sys.stdin.read()\n"
    "with open(base/'envios.log','a') as f: f.write(ident+'\\n')\n"
    "print(json.dumps({'recibo':'env-'+ident}))\n"
)

CONCILIACION = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "ident=sys.argv[-1]\n"
    "sys.stdin.read()\n"
    "with open(base/'check.log','a') as f: f.write(ident+'\\n')\n"
    "print(json.dumps({'ausente':True}))\n"
)

SIN_RECIBO = (
    "import sys\n"
    "sys.stdin.read()\n"
    "print('{}')\n"
)


class Banco(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)
        envio = Path(self._tmp.name) / "envio.py"
        envio.write_text(ENVIO, encoding="utf-8")
        check = Path(self._tmp.name) / "check.py"
        check.write_text(CONCILIACION, encoding="utf-8")
        (self.base / "canal.json").write_text(
            json.dumps(
                {
                    "hermes": {
                        "comando": [sys.executable, "-B", str(envio)],
                        "conciliacion": [sys.executable, "-B", str(check)],
                        "idempotente": True,
                    }
                }
            ),
            encoding="utf-8",
        )

    def encargo(self, clave: str = "uno", via: str = "durable") -> str:
        return directores.encargar(
            self.reg, "pedido", clave, HORA, "hermes", "hermes", via
        )["encargo"]

    def terminar(self, ident: str) -> None:
        self.reg.anotar(
            ident,
            {"kind": "resultado_directo", "data": {"evidencia": "listo", "hora": HORA}},
            "fin",
        )
        self.assertEqual(self.reg.fold(ident).estado, "terminado")

    def cancelar(self, ident: str) -> None:
        self.reg.anotar(
            ident,
            {"kind": "orden", "data": {"accion": "cancelar", "hora": HORA}},
            "orden-cancelar",
        )
        self.reg.anotar(
            ident,
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": HORA}},
            "arbol-cancelar",
        )
        self.assertEqual(self.reg.fold(ident).estado, "cancelado")

    def aviso(self, doc: dict) -> dict:
        (self.base / "avisos").mkdir(parents=True, exist_ok=True)
        (self.base / "avisos" / f"{doc['id']}.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )
        return doc

    def cuerpo(self, ident: str, texto: str) -> None:
        carpeta = self.base / "canal" / "hermes"
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / ident).write_text(texto, encoding="utf-8")

    def sin_recibo(self) -> None:
        envio = Path(self._tmp.name) / "envio_sin_recibo.py"
        envio.write_text(SIN_RECIBO, encoding="utf-8")
        ruta = self.base / "canal.json"
        config = json.loads(ruta.read_text(encoding="utf-8"))
        config["hermes"]["comando"] = [sys.executable, "-B", str(envio)]
        ruta.write_text(json.dumps(config), encoding="utf-8")

    def caido(self) -> None:
        canales.registrar_pasada(
            self.base, T0 - 1000, {"hermes": "no puedo leer el bot"}, set()
        )

    def lineas(self, nombre: str) -> list[str]:
        log = self.base / nombre
        return log.read_text().split() if log.exists() else []

    def sondeos(self) -> list[str]:
        return [x for x in self.lineas("check.log") if x.startswith("sondeo")]

    def guardado(self, ident: str) -> dict:
        activo = self.base / "avisos" / f"{ident}.json"
        archivo = self.base / "avisos" / "archivo" / f"{ident}.json"
        return json.loads((activo if activo.exists() else archivo).read_text())

    def incierto(self, ident: str, encargo: str, **extra) -> dict:
        doc = {
            "id": ident,
            "encargo": encargo,
            "canal": "hermes",
            "evidencia": "avance:ci_rojo:" + ident,
            "estado": "incierto",
            "fallos": 1,
            "proximo_intento": directores._iso(T0 - 10),
        }
        doc.update(extra)
        return self.aviso(doc)


class CasosDeLosDiezArreglos(Banco):
    def test_ed3df5c_un_aviso_vencido_no_bloquea_el_sondeo(self):  # no-roja-en-base: caracterización del descarte por antigüedad (ed3df5c)
        ident = self.encargo()
        self.cuerpo("av_vencido", "cuerpo viejo")
        self.incierto(
            "av_vencido",
            ident,
            primer_intento=T0 - 7 * 3600,
            proximo_intento=directores._iso(T0 + 100),
        )
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.guardado("av_vencido")["estado"], "descartado")
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_4d8c5a4_un_aviso_incierto_de_un_encargo_detenido_no_bloquea_el_sondeo(self):  # no-roja-en-base: caracterización del encargo detenido (4d8c5a4)
        ident = self.encargo()
        self.reg.anotar(
            ident,
            {"kind": "orden", "data": {"accion": "parar", "hora": HORA}},
            "orden-parar",
        )
        self.reg.anotar(
            ident,
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": HORA}},
            "arbol-ausente",
        )
        self.assertEqual(self.reg.fold(ident).estado, "detenido")
        self.incierto("av_detenido", ident)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_ece4f57_un_ci_rojo_huerfano_de_un_terminado_no_bloquea_el_sondeo(self):  # no-roja-en-base: caracterización del ci_rojo huérfano (ece4f57)
        ident = self.encargo(via="directa")
        self.terminar(ident)
        self.incierto("av_ci_rojo_huerfano", ident, tipo="ci_rojo")
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.guardado("av_ci_rojo_huerfano")["estado"], "descartado")
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_4201d8d_un_resumen_apagado_no_bloquea_el_sondeo(self):  # no-roja-en-base: caracterización del resumen apagado (4201d8d)
        (self.base / "resumen-config.json").write_text(
            json.dumps({"canal": "hermes", "activo": False}), encoding="utf-8"
        )
        self.aviso(
            {
                "id": "av_resumen_apagado",
                "encargo": "",
                "canal": "hermes",
                "evidencia": "resumen:firma:1:1",
                "estado": "incierto",
                "tipo": "resumen",
                "firma": "firma",
                "episodio": 1,
                "parte": 1,
                "partes": 1,
                "cuerpo": "resumen",
                "fallos": 1,
                "proximo_intento": directores._iso(T0 - 10),
            }
        )
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_4350cc3_el_cierre_incierto_de_un_terminado_no_bloquea_el_sondeo(self):  # no-roja-en-base: caracterización del cierre de un terminado (4350cc3)
        ident = self.encargo(via="directa")
        self.terminar(ident)
        cierre = directores._aviso_de(self.reg.fold(ident))
        cierre.update(
            estado="incierto", fallos=1, proximo_intento=directores._iso(T0 + 600)
        )
        self.aviso(cierre)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.guardado(cierre["id"])["estado"], "descartado")
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_513d36e_el_cierre_incierto_de_un_cancelado_no_bloquea_el_sondeo(self):  # no-roja-en-base: caracterización del cierre de un cancelado (513d36e)
        ident = self.encargo(via="directa")
        self.cancelar(ident)
        cierre = directores._aviso_de(self.reg.fold(ident))
        cierre.update(
            estado="incierto", fallos=1, proximo_intento=directores._iso(T0 + 600)
        )
        self.aviso(cierre)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.guardado(cierre["id"])["estado"], "descartado")
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_ced5309_el_cierre_incierto_de_un_cerrado_se_archiva_y_no_bloquea(self):  # no-roja-en-base: caracterización del archivo del cierre (ced5309)
        ident = self.encargo(via="directa")
        self.terminar(ident)
        cierre = directores._aviso_de(self.reg.fold(ident))
        cierre.update(estado="incierto", fallos=1)
        self.aviso(cierre)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.guardado(cierre["id"])["motivo"], directores.MOTIVO_ENCARGO_CERRADO)
        self.assertTrue((self.base / "avisos" / "archivo" / f"{cierre['id']}.json").is_file())
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_0da393f_el_resumen_no_descarta_un_aviso_ajeno(self):  # no-roja-en-base: caracterización del filtro de resumen (0da393f)
        ident = self.encargo()
        aviso = self.incierto(
            "av_inicio", ident, evidencia="avance:inicio:inicio", tipo="inicio"
        )
        self.aviso(
            {
                "id": "av_resumen_viejo",
                "encargo": "",
                "canal": "hermes",
                "evidencia": "resumen:otra:1:1",
                "estado": "incierto",
                "tipo": "resumen",
                "firma": "otra",
                "episodio": 1,
                "parte": 1,
                "partes": 1,
                "cuerpo": "resumen",
            }
        )
        (self.base / "resumen-config.json").write_text(
            json.dumps({"canal": "hermes", "intervalo_min": 60}), encoding="utf-8"
        )

        directores.drenar(self.reg, ahora=T0)

        self.assertNotEqual(
            self.guardado(aviso["id"]).get("motivo"), "hay un resumen más nuevo"
        )
        self.assertEqual(self.sondeos(), [])

    def test_d5fc306_el_aviso_archivado_no_se_vuelve_a_leer_ni_a_bloquear(self):  # no-roja-en-base: caracterización del archivo terminal (d5fc306)
        self.aviso(
            {
                "id": "av_publicado",
                "encargo": "enc_que_ya_no_existe",
                "canal": "hermes",
                "evidencia": "avance:inicio:inicio",
                "estado": "publicado",
                "recibo": "r",
                "alcance": "externo",
            }
        )
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertFalse((self.base / "avisos" / "av_publicado.json").exists())
        self.assertTrue((self.base / "avisos" / "archivo" / "av_publicado.json").is_file())
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_1d79367_sin_avisos_retomables_el_sondeo_recupera_el_canal(self):  # no-roja-en-base: caracterización de la recuperación por sondeo (1d79367)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.sondeos(), ["sondeo-hermes"])
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 1000), [])


class ListaCerrada(Banco):
    def test_un_pendiente_huerfano_de_un_encargo_activo_no_bloquea_el_sondeo(self):
        ident = self.encargo()
        self.aviso(
            {
                "id": "av_pendiente_huerfano",
                "encargo": ident,
                "canal": "hermes",
                "evidencia": "avance:relevo:viejo",
                "estado": "pendiente",
            }
        )
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(
            self.sondeos(),
            ["sondeo-hermes"],
            "un pendiente que el estado vigente ya no genera no lo visita nadie",
        )

    def test_un_pendiente_vigente_de_un_encargo_activo_si_bloquea_el_sondeo(self):  # no-roja-en-base: caracterización de la rama positiva del predicado
        ident = self.encargo()
        self.reg.anotar(
            ident,
            {
                "kind": "fases",
                "data": {"fases": [{"id": "A", "resultado": "fase"}], "hora": HORA},
            },
            "fase",
        )
        self.caido()

        directores.drenar(self.reg, cortar="antes", ahora=T0)

        inicio = directores.id_aviso(ident, "avance:inicio:inicio")
        self.assertEqual(self.guardado(inicio)["estado"], "pendiente")
        self.assertEqual(
            self.sondeos(),
            [],
            "un pendiente que el estado vigente todavía genera sí detiene el sondeo",
        )
        self.assertEqual(
            [c["canal"] for c in canales.canales_caidos(self.base, ahora=T0 + 1000)],
            ["hermes"],
        )

    def test_el_cierre_incierto_de_la_misma_pasada_no_bloquea_el_sondeo(self):
        ident = self.encargo(via="directa")
        self.terminar(ident)
        self.sin_recibo()
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        cierre = directores._aviso_de(self.reg.fold(ident))
        self.assertEqual(
            self.guardado(cierre["id"])["estado"],
            "incierto",
            "el envío del cierre vuelve sin recibo en esta misma pasada",
        )
        self.assertEqual(
            self.sondeos(),
            ["sondeo-hermes"],
            "el cierre de un encargo cerrado no es de un encargo activo: no detiene el sondeo",
        )
        self.assertEqual(
            canales.canales_caidos(self.base, ahora=T0 + 1000),
            [],
            "el sondeo recupera el canal en la misma pasada",
        )

    def test_un_aviso_vigente_de_un_encargo_activo_se_entrega_una_sola_vez(self):  # no-roja-en-base: caracterización de la entrega idempotente
        ident = self.encargo()
        self.reg.anotar(
            ident,
            {
                "kind": "fases",
                "data": {"fases": [{"id": "A", "resultado": "fase"}], "hora": HORA},
            },
            "fase",
        )
        directores.drenar(self.reg, ahora=T0)
        primera = list(self.lineas("envios.log"))
        directores.drenar(self.reg, ahora=T0 + 1)

        self.assertEqual(len(primera), 1, "el aviso vigente del encargo activo sale una vez")
        self.assertEqual(self.lineas("envios.log"), primera, "no se reenvía el mismo hecho")
        self.assertIn(
            directores.id_aviso(ident, "avance:inicio:inicio"), primera
        )


if __name__ == "__main__":
    unittest.main()
