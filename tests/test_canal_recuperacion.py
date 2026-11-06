"""Un canal caído se recupera solo con el sondeo y un aviso incierto vencido no se queda huérfano."""

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
    "if (base/'envio_caido').exists():\n"
    " sys.stderr.write('el envio no salio'); sys.exit(1)\n"
    "print(json.dumps({'recibo':'env-'+ident}))\n"
)

CONCILIACION = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "ident=sys.argv[-1]\n"
    "sys.stdin.read()\n"
    "with open(base/'check.log','a') as f: f.write(ident+'\\n')\n"
    "modo=(base/'check_modo').read_text().strip() if (base/'check_modo').exists() else 'ausente'\n"
    "if modo=='roto':\n"
    " sys.stderr.write('la revision no responde'); sys.exit(1)\n"
    "if modo=='sin_veredicto':\n"
    " print(json.dumps({'estado':'desconocido'})); sys.exit(0)\n"
    "if modo=='hallado':\n"
    " print(json.dumps({'recibo':'conc-'+ident})); sys.exit(0)\n"
    "print(json.dumps({'ausente':True}))\n"
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
        cfg = {"comando": [sys.executable, "-B", str(envio)], "idempotente": True}
        check = Path(self._tmp.name) / "check.py"
        check.write_text(CONCILIACION, encoding="utf-8")
        cfg["conciliacion"] = [sys.executable, "-B", str(check)]
        (self.base / "canal.json").write_text(
            json.dumps({"hermes": cfg}), encoding="utf-8"
        )
        self.check_modo("ausente")

    def check_modo(self, valor: str) -> None:
        (self.base / "check_modo").write_text(valor)

    def lineas(self, nombre: str) -> list[str]:
        log = self.base / nombre
        return log.read_text().split() if log.exists() else []

    def sondeos(self) -> list[str]:
        return [x for x in self.lineas("check.log") if x.startswith("sondeo")]

    def estado(self) -> dict:
        return json.loads((self.base / "canales" / "hermes.json").read_text())

    def caido(self) -> None:
        canales.registrar_pasada(
            self.base, T0 - 1000, {"hermes": "no puedo leer el bot"}, set()
        )

    def encargo(self) -> str:
        ident = directores.encargar(
            self.reg, "pedido", "uno", HORA, "hermes", "hermes", "durable"
        )["encargo"]
        self.reg.anotar(ident, {"kind": "fases", "data": {
            "fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": HORA,
        }}, "fase")
        return ident

    def relevo(self, ident: str, huella: str) -> None:
        self.reg.anotar(ident, {"kind": "sin_progreso", "data": {
            "huella": huella, "escalar": True, "diagnostico": huella, "hora": HORA,
        }}, f"rel-{huella}")

    def detener(self, ident: str) -> None:
        self.reg.anotar(ident, {"kind": "orden", "data": {
            "accion": "parar", "hora": HORA,
        }}, "orden-parar")
        self.reg.anotar(ident, {"kind": "arbol", "data": {
            "observacion": "ausente", "hora": HORA,
        }}, "arbol-ausente")
        self.assertEqual(self.reg.fold(ident).estado, "detenido")

    def cancelar(self, ident: str) -> None:
        self.reg.anotar(ident, {"kind": "orden", "data": {
            "accion": "cancelar", "hora": HORA,
        }}, "orden-cancelar")
        self.reg.anotar(ident, {"kind": "arbol", "data": {
            "observacion": "ausente", "hora": HORA,
        }}, "arbol-cancelar")
        self.assertEqual(self.reg.fold(ident).estado, "cancelado")

    def encargo_directo(self) -> str:
        return directores.encargar(
            self.reg, "pedido", "directo", HORA, "hermes", "hermes", "directa"
        )["encargo"]

    def terminar(self, ident: str) -> None:
        self.reg.anotar(ident, {"kind": "resultado_directo", "data": {
            "evidencia": "listo", "hora": HORA,
        }}, "fin")
        self.assertEqual(self.reg.fold(ident).estado, "terminado")

    def escribir_aviso(self, doc: dict) -> None:
        (self.base / "avisos").mkdir(parents=True, exist_ok=True)
        (self.base / "avisos" / f"{doc['id']}.json").write_text(json.dumps(doc))

    def aviso(self, ident: str) -> dict:
        activo = self.base / "avisos" / f"{ident}.json"
        archivo = self.base / "avisos" / "archivo" / f"{ident}.json"
        return json.loads((activo if activo.exists() else archivo).read_text())

    def aviso_de_relevo(self) -> dict:
        for path in (self.base / "avisos").glob("*.json"):
            doc = json.loads(path.read_text())
            if doc.get("tipo") == "relevo":
                return doc
        self.fail("no hay aviso de relevo")


class RecuperacionPorSondeo(Banco):
    def test_un_canal_caido_sin_avisos_pendientes_se_recupera_con_el_sondeo(self):
        for respuesta in ("ausente", "hallado"):
            with self.subTest(respuesta=respuesta):
                self.caido()
                self.check_modo(respuesta)
                directores.drenar(self.reg, ahora=T0)
                estado = self.estado()
                self.assertNotIn("fallando_desde", estado, "el sondeo probó que el canal volvió")
                self.assertEqual(estado.get("ultimo_exito"), directores._iso(T0))
                self.assertEqual(self.sondeos(), ["sondeo-hermes"], "un solo sondeo")
                self.assertEqual(self.lineas("envios.log"), [], "el sondeo no manda mensajes")
                (self.base / "canales" / "hermes.json").unlink()
                for log in ("check.log", "envios.log"):
                    ruta = self.base / log
                    if ruta.exists():
                        ruta.unlink()

    def test_con_el_transporte_roto_sigue_caido_y_no_se_sondea_mas_de_una_vez_por_intervalo(self):
        self.caido()
        self.check_modo("roto")
        directores.drenar(self.reg, ahora=T0)
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])
        caidos = [c["canal"] for c in canales.canales_caidos(self.base, ahora=T0 + 1000)]
        self.assertEqual(caidos, ["hermes"], "sin respuesta el canal sigue caído")

        directores.drenar(self.reg, ahora=T0 + 30)
        self.assertEqual(len(self.sondeos()), 1, "antes de la espera no se sondea")

        directores.drenar(self.reg, ahora=T0 + 61)
        self.assertEqual(len(self.sondeos()), 2, "pasada la espera vuelve a sondearse")

    def test_sin_conciliacion_el_canal_se_deja_como_esta(self):
        cfg = json.loads((self.base / "canal.json").read_text())
        del cfg["hermes"]["conciliacion"]
        (self.base / "canal.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.caido()
        directores.drenar(self.reg, ahora=T0)
        self.assertIn("fallando_desde", self.estado())
        self.assertEqual(self.sondeos(), [])

    def test_un_sondeo_sin_veredicto_no_recupera_el_canal(self):
        self.caido()
        self.check_modo("sin_veredicto")
        directores.drenar(self.reg, ahora=T0)
        self.assertIn("fallando_desde", self.estado())
        self.assertEqual(len(self.sondeos()), 1)
        directores.drenar(self.reg, ahora=T0 + 30)
        self.assertEqual(len(self.sondeos()), 1, "el sondeo sin veredicto también espera su intervalo")

    def test_con_un_aviso_pendiente_no_se_sondea(self):
        self.encargo()
        (self.base / "envio_caido").write_text("x")
        directores.drenar(self.reg, ahora=T0)
        self.assertEqual(self.sondeos(), [], "el aviso pendiente ya prueba el transporte")
        directores.drenar(self.reg, ahora=T0 + 30)
        self.assertEqual(self.sondeos(), [], "aún no toca reintentar el aviso ni sondear")

    def test_un_resumen_incierto_con_el_envio_apagado_no_bloquea_el_sondeo(self):
        self.escribir_aviso({
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
        })
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        self.assertEqual(self.sondeos(), ["sondeo-hermes"])
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 1000), [])


class AvisoInciertoHuerfano(Banco):
    def test_un_aviso_vencido_no_bloquea_el_sondeo_del_canal(self):
        ident = self.encargo()
        directores.drenar(self.reg, ahora=T0)
        self.relevo(ident, "h1")
        (self.base / "envio_caido").write_text("x")
        directores.drenar(self.reg, ahora=T0 + 1)
        viejo = self.aviso_de_relevo()

        self.relevo(ident, "h2")
        doc = self.aviso(viejo["id"])
        doc["primer_intento"] = T0 - 7 * 3600
        doc["proximo_intento"] = directores._iso(T0 + 100)
        (self.base / "avisos" / f"{viejo['id']}.json").write_text(json.dumps(doc))
        (self.base / "envio_caido").unlink()

        directores.drenar(self.reg, ahora=T0 + 10)
        self.assertEqual(self.aviso(viejo["id"])["estado"], "descartado")
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 1000), [])
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])

    def test_un_aviso_incierto_vencido_que_ya_no_genera_el_estado_se_reintenta(self):
        ident = self.encargo()
        directores.drenar(self.reg, ahora=T0)
        self.relevo(ident, "h1")
        (self.base / "envio_caido").write_text("x")
        directores.drenar(self.reg, ahora=T0 + 1)
        viejo = self.aviso_de_relevo()
        self.assertEqual(viejo["estado"], "incierto")
        self.assertTrue((self.base / "canal" / "hermes" / viejo["id"]).is_file())

        self.relevo(ident, "h2")
        (self.base / "envio_caido").unlink()
        directores.drenar(self.reg, ahora=T0 + 100)

        guardado = self.aviso(viejo["id"])
        self.assertEqual(guardado["estado"], "publicado")
        self.assertTrue(guardado.get("recibo"))
        self.assertIn(viejo["id"], self.lineas("check.log"))
        self.assertIn(viejo["id"], self.lineas("envios.log"))

    def test_un_aviso_incierto_de_un_encargo_detenido_no_bloquea_el_sondeo(self):
        ident = self.encargo()
        directores.drenar(self.reg, ahora=T0)
        self.relevo(ident, "h1")
        (self.base / "envio_caido").write_text("x")
        directores.drenar(self.reg, ahora=T0 + 1)
        viejo = self.aviso_de_relevo()
        self.assertEqual(viejo["estado"], "incierto")
        self.detener(ident)

        (self.base / "envio_caido").unlink()
        directores.drenar(self.reg, ahora=T0 + 100)

        self.assertEqual(
            self.aviso(viejo["id"])["estado"], "incierto",
            "un encargo detenido no reabre avisos de avance",
        )
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 1000), [])

    def test_el_cierre_incierto_de_un_cancelado_se_descarta_y_no_bloquea_el_sondeo(self):
        ident = self.encargo_directo()
        self.cancelar(ident)
        cierre = directores._aviso_de(self.reg.fold(ident))
        cierre["estado"] = "incierto"
        cierre["fallos"] = 1
        cierre["proximo_intento"] = directores._iso(T0 + 600)
        self.escribir_aviso(cierre)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        guardado = self.aviso(cierre["id"])
        self.assertEqual(guardado["estado"], "descartado")
        self.assertEqual(guardado["motivo"], directores.MOTIVO_ENCARGO_CERRADO)
        self.assertTrue(
            (self.base / "avisos" / "archivo" / f"{cierre['id']}.json").is_file()
        )
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 1000), [])

    def test_el_cierre_incierto_de_un_terminado_se_descarta_y_no_bloquea_el_sondeo(self):
        ident = self.encargo_directo()
        self.terminar(ident)
        cierre = directores._aviso_de(self.reg.fold(ident))
        cierre["estado"] = "incierto"
        cierre["fallos"] = 1
        cierre["proximo_intento"] = directores._iso(T0 + 600)
        self.escribir_aviso(cierre)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        guardado = self.aviso(cierre["id"])
        self.assertEqual(guardado["estado"], "descartado")
        self.assertEqual(guardado["motivo"], directores.MOTIVO_ENCARGO_CERRADO)
        self.assertTrue(
            (self.base / "avisos" / "archivo" / f"{cierre['id']}.json").is_file()
        )
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 1000), [])

    def test_un_aviso_incierto_huerfano_de_un_encargo_terminado_no_bloquea_el_sondeo(self):
        ident = self.encargo_directo()
        self.terminar(ident)
        cierre = directores._aviso_de(self.reg.fold(ident))
        cierre["estado"] = "publicado"
        cierre["recibo"] = "ok"
        self.escribir_aviso(cierre)
        huerfano = {
            "id": "av_ci_rojo_huerfano",
            "encargo": ident,
            "canal": "hermes",
            "evidencia": "avance:ci_rojo:sha-viejo",
            "estado": "incierto",
            "tipo": "ci_rojo",
            "recibo": "",
            "fallos": 1,
            "proximo_intento": directores._iso(T0 - 10),
        }
        self.escribir_aviso(huerfano)
        self.caido()

        directores.drenar(self.reg, ahora=T0)

        guardado = self.aviso(huerfano["id"])
        self.assertEqual(guardado["estado"], "descartado")
        self.assertEqual(guardado["motivo"], directores.MOTIVO_ENCARGO_CERRADO)
        self.assertTrue(
            (self.base / "avisos" / "archivo" / f"{huerfano['id']}.json").is_file(),
            "el huerfano de un terminado se archiva",
        )
        self.assertEqual(self.sondeos(), ["sondeo-hermes"])
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 1000), [])


if __name__ == "__main__":
    unittest.main()
