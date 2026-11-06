"""Los avisos terminales salen de la carpeta activa sin romper la idempotencia."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.registro import Registro
from motor.integraciones import directores

HORA = "2026-11-01T10:00:00Z"
T0 = 1_000_000.0

TRANSPORTE = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "sys.stdin.read()\n"
    "with open(base/'llamadas.log','a') as f: f.write(os.environ['MOTOR_AVISO_ID']+'\\n')\n"
    "if (base/'falla').exists():\n"
    " sys.stderr.write('el canal no respondio'); sys.exit(1)\n"
    "print(json.dumps({'recibo':'ok-'+os.environ['MOTOR_AVISO_ID']}))\n"
)


class Banco:
    def __init__(self, tmp: str, falla: bool = False):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        script = Path(tmp) / "transporte.py"
        script.write_text(TRANSPORTE, encoding="utf-8")
        comando = [sys.executable, "-B", str(script)]
        (self.base / "canal.json").write_text(
            json.dumps(
                {
                    "tg": {"comando": comando, "idempotente": True},
                    "wa": {"comando": comando, "idempotente": True},
                }
            ),
            encoding="utf-8",
        )
        (self.base / "resumen-config.json").write_text(
            json.dumps({"canal": "tg", "intervalo_min": 60}), encoding="utf-8"
        )
        if falla:
            (self.base / "falla").write_text("x")

    def encargo(self, clave: str, canal: str = "tg", via: str = "durable") -> str:
        ident = directores.encargar(
            self.reg, f"pedido {clave}", clave, HORA, "hermes", canal, via
        )["encargo"]
        self.reg.anotar(
            ident,
            {
                "kind": "fases",
                "data": {"fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": HORA},
            },
            f"fase-{clave}",
        )
        return ident

    def terminar(self, ident: str) -> None:
        self.reg.anotar(
            ident,
            {"kind": "resultado_directo", "data": {"evidencia": "listo", "hora": HORA}},
            f"fin-{ident}",
        )

    def llamadas(self) -> list[str]:
        log = self.base / "llamadas.log"
        return log.read_text().split() if log.exists() else []

    def avisos(self) -> list[dict]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for carpeta in (self.base / "avisos", self.base / "avisos" / "archivo")
            for path in sorted(carpeta.glob("*.json"))
        ]


class Base(unittest.TestCase):
    falla = False

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = Banco(self._tmp.name, self.falla)
        self.addCleanup(self.b.reg.close)


class IdempotenciaDelArchivo(Base):
    def test_un_aviso_publicado_y_archivado_no_se_vuelve_a_crear_ni_a_mandar(self):
        ident = self.b.encargo("uno")
        directores.drenar(self.b.reg, ahora=T0)
        aviso = directores.id_aviso(ident, "avance:inicio:inicio")
        self.assertTrue(
            (self.b.base / "avisos" / "archivo" / f"{aviso}.json").is_file(),
            "el aviso publicado sale de la carpeta activa",
        )
        self.assertFalse((self.b.base / "avisos" / f"{aviso}.json").exists())
        enviados = self.b.llamadas()
        directores.drenar(self.b.reg, ahora=T0 + 1)
        self.assertEqual(self.b.llamadas(), enviados, "el mismo hecho no se manda otra vez")
        self.assertFalse((self.b.base / "avisos" / f"{aviso}.json").exists())

    def test_el_resumen_archivado_de_la_misma_firma_y_episodio_no_se_reenvia(self):
        self.b.encargo("uno")
        directores.drenar(self.b.reg, ahora=T0)
        resumenes = [
            doc for doc in self.b.avisos() if doc.get("tipo") == "resumen"
        ]
        self.assertEqual(len(resumenes), 1)
        self.assertEqual(resumenes[0]["estado"], "publicado")
        self.assertTrue(
            (self.b.base / "avisos" / "archivo" / f"{resumenes[0]['id']}.json").is_file()
        )
        enviados = self.b.llamadas()
        directores.drenar(self.b.reg, ahora=T0 + 61 * 60)
        self.assertEqual(self.b.llamadas(), enviados, "el resumen archivado no se reenvia")


class InciertosSinDestino(Base):
    falla = True

    def test_el_incierto_de_un_encargo_cerrado_se_descarta_y_archiva_y_el_activo_no(self):
        (self.b.base / "resumen-config.json").unlink()
        cerrado = self.b.encargo("cerrado", canal="tg", via="directa")
        activo = self.b.encargo("activo", canal="wa")
        inicio_cerrado = directores.id_aviso(cerrado, "avance:inicio:inicio")
        inicio_activo = directores.id_aviso(activo, "avance:inicio:inicio")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertTrue((self.b.base / "avisos" / f"{inicio_cerrado}.json").is_file())
        self.assertTrue((self.b.base / "avisos" / f"{inicio_activo}.json").is_file())

        self.b.terminar(cerrado)
        directores.drenar(self.b.reg, ahora=T0 + 1)
        cerrado_doc = json.loads(
            (self.b.base / "avisos" / "archivo" / f"{inicio_cerrado}.json").read_text()
        )
        self.assertEqual(cerrado_doc["estado"], "descartado")
        self.assertEqual(cerrado_doc["motivo"], directores.MOTIVO_ENCARGO_CERRADO)
        self.assertFalse((self.b.base / "avisos" / f"{inicio_cerrado}.json").exists())

        activo_doc = json.loads(
            (self.b.base / "avisos" / f"{inicio_activo}.json").read_text()
        )
        self.assertEqual(activo_doc["estado"], "incierto")
        self.assertFalse(
            (self.b.base / "avisos" / "archivo" / f"{inicio_activo}.json").is_file()
        )


class Migracion(Base):
    def _terminal(self, ident: str, estado: str) -> None:
        (self.b.base / "avisos").mkdir(parents=True, exist_ok=True)
        (self.b.base / "avisos" / f"{ident}.json").write_text(
            json.dumps(
                {
                    "id": ident,
                    "encargo": "enc_viejo",
                    "canal": "tg",
                    "evidencia": "avance:inicio:inicio",
                    "estado": estado,
                    "recibo": "r" if estado == "publicado" else "",
                    "alcance": "externo" if estado == "publicado" else "",
                }
            ),
            encoding="utf-8",
        )

    def test_la_migracion_archiva_los_terminales_y_es_idempotente(self):
        self._terminal("av_pub", "publicado")
        self._terminal("av_des", "descartado")
        (self.b.base / "avisos" / "av_incierto.json").write_text(
            json.dumps(
                {
                    "id": "av_incierto",
                    "encargo": "enc_que_ya_no_existe",
                    "canal": "tg",
                    "evidencia": "avance:inicio:inicio",
                    "estado": "incierto",
                }
            ),
            encoding="utf-8",
        )
        (self.b.base / "avisos" / "archivo").mkdir(parents=True)
        (self.b.base / "avisos" / "av_pub.json").replace(
            self.b.base / "avisos" / "archivo" / "av_pub.json"
        )
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(list((self.b.base / "avisos").glob("*.json")), [])
        archivo = sorted(
            path.name
            for path in (self.b.base / "avisos" / "archivo").glob("*.json")
            if path.name != "resumenes.json"
        )
        self.assertEqual(archivo, ["av_des.json", "av_incierto.json", "av_pub.json"])
        descartado = json.loads(
            (self.b.base / "avisos" / "archivo" / "av_incierto.json").read_text()
        )
        self.assertEqual(descartado["estado"], "descartado")
        self.assertEqual(descartado["motivo"], directores.MOTIVO_ENCARGO_CERRADO)

        directores.drenar(self.b.reg, ahora=T0 + 1)
        self.assertEqual(list((self.b.base / "avisos").glob("*.json")), [])
        self.assertEqual(
            sorted(
                path.name
                for path in (self.b.base / "avisos" / "archivo").glob("*.json")
                if path.name != "resumenes.json"
            ),
            ["av_des.json", "av_incierto.json", "av_pub.json"],
        )


class UnaSolaLectura(Base):
    def test_una_pasada_lee_la_carpeta_de_avisos_una_sola_vez(self):
        (self.b.base / "avisos").mkdir(parents=True)
        for numero in range(12):
            (self.b.base / "avisos" / f"av_{numero:016x}.json").write_text(
                json.dumps(
                    {
                        "id": f"av_{numero:016x}",
                        "encargo": "",
                        "canal": "tg",
                        "evidencia": "x",
                        "estado": "pendiente",
                    }
                ),
                encoding="utf-8",
            )
        leidos: list[Path] = []
        original = directores._leer

        def contar(path):
            leidos.append(Path(path))
            return original(path)

        with mock.patch("motor.integraciones.directores._leer", side_effect=contar):
            directores.drenar(self.b.reg, ahora=T0)
        activos = [
            path for path in leidos if path.parent == (self.b.base / "avisos").resolve()
        ]
        self.assertEqual(len(activos), 12, "cada aviso activo se lee una vez")


if __name__ == "__main__":
    unittest.main()
