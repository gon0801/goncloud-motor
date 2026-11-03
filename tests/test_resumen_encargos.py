"""El resumen de encargos: contenido para cada estado, envio por cambio, corte y escapado."""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from motor.encargos import resumen
from motor.encargos.registro import Registro
from motor.integraciones import directores

HORA = "2026-11-01T10:00:00Z"
T0 = datetime.fromisoformat(HORA.replace("Z", "+00:00")).timestamp()
REANUDAR = "2026-11-01T11:00:00Z"


def momento(minutos: float) -> str:
    return datetime.fromtimestamp(T0 + minutos * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

TRANSPORTE = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "cuerpo=sys.stdin.read()\n"
    "if (base/'falla').exists():\n"
    " sys.stderr.write('no salio'); sys.exit(1)\n"
    "with open(base/'entregados.jsonl','a',encoding='utf-8') as f:\n"
    " f.write(json.dumps({'id':os.environ['MOTOR_AVISO_ID'],"
    "'evidencia':os.environ['MOTOR_AVISO_EVIDENCIA'],'cuerpo':cuerpo,"
    "'formato':os.environ.get('MOTOR_AVISO_FORMATO','')},"
    " ensure_ascii=False)+'\\n')\n"
    "print(json.dumps({'recibo':'ok-'+os.environ['MOTOR_AVISO_ID']}))\n"
)


class Banco:
    def __init__(self, tmp: str, intervalo: int = 60):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        envio = Path(tmp) / "envio.py"
        envio.write_text(TRANSPORTE, encoding="utf-8")
        (self.base / "canal.json").write_text(
            json.dumps(
                {"tg": {"comando": [sys.executable, "-B", str(envio)], "idempotente": True}}
            ),
            encoding="utf-8",
        )
        (self.base / "resumen-config.json").write_text(
            json.dumps({"canal": "tg", "intervalo_min": intervalo}),
            encoding="utf-8",
        )

    def encargo(self, clave: str, texto: str = "", entrega: str | None = None, repo: str = "") -> str:
        return directores.encargar(
            self.reg,
            texto or f"pedido {clave}",
            clave,
            HORA,
            "hermes",
            "tg",
            "durable",
            entrega_nivel=entrega,
            repo=repo,
        )["encargo"]

    def evento(self, encargo: str, kind: str, data: dict, ukey: str, hora: str = HORA) -> str:
        return self.reg.anotar(
            encargo, {"kind": kind, "data": {**data, "hora": hora}}, ukey
        )

    def resumenes(self) -> list[dict]:
        log = self.base / "entregados.jsonl"
        if not log.exists():
            return []
        filas = [json.loads(linea) for linea in log.read_text(encoding="utf-8").splitlines() if linea]
        return [f for f in filas if str(f.get("evidencia") or "").startswith("resumen:")]

    def estado(self) -> dict:
        path = self.base / "resumen.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def avisos(self) -> list[dict]:
        carpeta = self.base / "avisos"
        if not carpeta.is_dir():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(carpeta.glob("*.json"))
        ]


def activo(banco: Banco, clave: str) -> str:
    ident = banco.encargo(clave)
    banco.evento(ident, "fases", {"fases": [{"id": "A", "resultado": clave}]}, f"f-{clave}")
    return ident


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = Banco(self._tmp.name)
        self.addCleanup(self.b.reg.close)


class Contenido(Base):

    def test_un_registro_con_encargos_en_cada_estado_muestra_lo_pedido(self):
        recibido = self.b.encargo("recibido")

        sonnet = self.b.encargo("sonnet")
        self.b.evento(
            sonnet,
            "intento_abierto",
            {"id": "i1", "pid": 11, "inicio": "x", "rol": "responsable", "agente": "claude", "modelo": "sonnet"},
            "i-sonnet",
        )
        self.b.evento(sonnet, "actividad", {"observada": True, "hecho": "implementando"}, "a-sonnet")

        opus = self.b.encargo("opus")
        self.b.evento(
            opus,
            "intento_abierto",
            {"id": "i2", "pid": 12, "inicio": "x", "rol": "revisor", "agente": "claude", "modelo": "opus"},
            "i-opus",
        )
        self.b.evento(opus, "actividad", {"observada": True, "hecho": "revisando"}, "a-opus")

        espera = self.b.encargo("espera")
        self.b.evento(
            espera, "espera", {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR}, "e-espera"
        )

        checks = self.b.encargo("checks")
        self.b.evento(
            checks,
            "espera",
            {"motivo": "esperando checks requeridos del PR #9: bateria", "reanuda_en": REANUDAR},
            "e-checks",
        )

        necesita = self.b.encargo("necesita")
        self.b.evento(
            necesita,
            "pregunta",
            {"texto": "¿Publico el informe?", "bloquea_todo": True},
            "p-necesita",
        )

        deteniendo = self.b.encargo("deteniendo")
        self.b.evento(deteniendo, "orden", {"accion": "parar"}, "o-deteniendo")

        conciliando = self.b.encargo("conciliando")
        self.b.evento(
            conciliando,
            "efecto",
            {"id": "e1", "estado": "incierto", "destino": "telegram", "operacion": "publicar"},
            "e-conciliando",
        )

        fases = self.b.encargo("fases")
        self.b.evento(
            fases,
            "fases",
            {
                "fases": [
                    {"id": "uno", "resultado": "empezar"},
                    {"id": "freno", "resultado": "frenar"},
                    {"id": "tres", "resultado": "cerrar"},
                ]
            },
            "f-fases",
        )
        self.assertEqual(self.b.evento(fases, "comprobacion", {
            "clase": "confirmado", "evidencia": "uno listo", "fase": "uno",
        }, "c-fases"), "nuevo")

        pr = self.b.encargo("pr", entrega="pr", repo="gon0801/goncloud-motor")
        self.b.evento(pr, "fases", {"fases": [{"id": "A", "resultado": "publicar"}]}, "f-pr")
        self.b.evento(pr, "producto_listo", {"version": "v1", "manifiesto": [], "fase": "A"}, "prod-pr")
        self.b.evento(
            pr,
            "revision",
            {
                "revision_id": "r1",
                "version": "v1",
                "alcance": "completa",
                "veredicto": "aprobado",
                "origen": "independiente",
                "fase": "A",
            },
            "rev-pr",
        )
        self.b.evento(
            pr,
            "entrega_externa",
            {
                "paso": "pr_abierto",
                "numero": 12,
                "sha": "a" * 40,
                "repo": "gon0801/goncloud-motor",
                "fase": "A",
            },
            "pr-abierto",
        )
        self.b.evento(
            pr,
            "espera",
            {"motivo": "esperando revisión de DeepSeek para el SHA " + "a" * 12, "reanuda_en": REANUDAR},
            "e-pr",
        )

        terminado = activo(self.b, "terminado")
        self.assertEqual(
            self.b.evento(
                terminado,
                "comprobacion",
                {"clase": "confirmado", "evidencia": "listo", "fase": "A"},
                "c-terminado",
            ),
            "nuevo",
        )
        self.assertEqual(self.b.reg.fold(terminado).estado, "terminado")

        ahora = T0 + 16 * 60
        construido = resumen.construir(self.b.reg, ahora=ahora, host="pc-ensayo")
        html = construido.cuerpo
        self.assertIn("pc-ensayo", html)
        self.assertIn(f"{construido.activos} activos", html)
        self.assertIn("Sonnet implementando", html)
        self.assertIn("Opus revisando", html)
        self.assertIn("esperando la revisión de DeepSeek", html)
        self.assertIn("esperando checks", html)
        self.assertIn("necesita tu decisión: ¿Publico el informe?", html)
        self.assertIn("freno", html)
        self.assertIn("2 de 3", html)
        self.assertIn("goncloud-motor #12", html)
        self.assertIn("Desde: hace", html)
        self.assertIn("más de 15 minutos", html)
        self.assertIn("Necesitan tu decisión", html)
        self.assertIn(recibido[:12], html)
        self.assertNotIn(terminado[:12], html)
        for ident in (sonnet, opus, espera, checks, necesita, deteniendo, conciliando, fases, pr):
            self.assertIn(ident[:12], html)
        self.assertEqual(construido.activos, 10)
        self.assertIn(conciliando[:12], html)
        self.assertIn("conciliando", html)

    def test_el_comando_del_director_imprime_el_resumen(self):
        from motor.integraciones.cli import main

        self.b.encargo("uno")
        salida = StringIO()
        with redirect_stdout(salida):
            codigo = main(["resumen", "--base", str(self.b.base)])
        self.assertEqual(codigo, 0)
        self.assertIn("Resumen de encargos", salida.getvalue())
        self.assertIn("enc_", salida.getvalue())

    def test_una_espera_rearmada_conserva_desde_el_inicio(self):
        ident = self.b.encargo("rearma")
        self.b.evento(ident, "fases", {"fases": [{"id": "A", "resultado": "x"}]}, "fase")
        motivo = "esperando revisión de DeepSeek para el SHA " + "a" * 12
        for ronda in range(1, 41):
            hora = momento(ronda)
            self.b.evento(ident, "plazo", {}, f"plazo-{ronda}", hora=hora)
            self.b.evento(
                ident,
                "espera",
                {"motivo": motivo, "reanuda_en": momento(ronda + 1)},
                f"espera-{ronda}",
                hora=hora,
            )
        html = resumen.texto(self.b.reg, ahora=T0 + 41 * 60, host="pc")
        self.assertIn("esperando la revisión de DeepSeek", html)
        self.assertIn("Desde: hace 40 min", html)
        self.assertNotIn("la misma espera", html)


class Alertas(Base):
    def test_la_misma_espera_sostenida_es_una_alerta(self):
        ident = self.b.encargo("repite")
        motivo = "falta un entorno macOS"
        for ronda in range(1, 131):
            hora = momento(ronda)
            self.b.evento(ident, "plazo", {}, f"plazo-{ronda}", hora=hora)
            self.b.evento(
                ident,
                "espera",
                {"motivo": motivo, "reanuda_en": momento(ronda + 1)},
                f"espera-{ronda}",
                hora=hora,
            )
        html = resumen.texto(self.b.reg, ahora=T0 + 131 * 60, host="pc")
        self.assertIn("la misma espera lleva más de 2 h", html)
        html_luego = resumen.texto(self.b.reg, ahora=T0 + 240 * 60, host="pc")
        self.assertIn("la misma espera lleva más de 2 h", html_luego)

    def test_dos_no_concluyentes_seguidos_son_una_alerta(self):
        ident = self.b.encargo("no-concluye")
        self.b.evento(ident, "fases", {"fases": [{"id": "A", "resultado": "x"}]}, "fase")
        for indice in range(2):
            self.b.evento(
                ident,
                "revision",
                {
                    "fase": "A",
                    "revision_id": f"r{indice}",
                    "version": "v1",
                    "alcance": "completa",
                    "veredicto": "no_concluyente",
                    "inspeccion_faltante": "faltan datos",
                    "origen": "independiente",
                    "evidencia": "no alcanza",
                },
                f"revision-{indice}",
            )
        self.assertEqual(self.b.reg.fold(ident).no_concluyentes, 2)
        html = resumen.texto(self.b.reg, ahora=T0, host="pc")
        self.assertIn("2 revisiones no concluyentes seguidas", html)

    def test_un_fallo_de_instrumento_y_una_solicitud_tardia_se_avisan(self):
        instrumento = self.b.encargo("instrumento")
        self.b.evento(
            instrumento,
            "instrumento_fallo",
            {"fuente": "github", "causa": "no responde", "siguiente": "reintentar"},
            "fallo",
        )
        tardia = self.b.encargo("tardia")
        self.b.evento(
            tardia,
            "espera",
            {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR},
            "espera-tardia",
        )
        self.b.evento(
            tardia,
            "solicitud_tardia",
            {
                "id": "s1",
                "fase": "A",
                "intento": "i1",
                "motivo": "el encargo se detuvo antes de despachar",
            },
            "tardia-s1",
        )
        html = resumen.texto(self.b.reg, ahora=T0, host="pc")
        self.assertIn("fallo de instrumento: no responde", html)
        self.assertIn("solicitud tardía: el encargo se detuvo antes de despachar", html)

    def test_un_impedimento_resuelto_apaga_la_alerta_de_instrumento(self):
        ident = self.b.encargo("resuelto")
        self.b.evento(
            ident,
            "instrumento_fallo",
            {"fuente": "github", "causa": "no responde", "siguiente": "reintentar"},
            "fallo",
        )
        self.b.evento(
            ident,
            "impedimento",
            {
                "clase": "instrumento",
                "causa": "no responde",
                "condicion": "el instrumento vuelve a responder",
            },
            "impedimento",
        )
        self.b.evento(
            ident,
            "impedimento_resuelto",
            {"clase": "instrumento", "evidencia": "el instrumento ya responde"},
            "resuelto",
        )
        html = resumen.texto(self.b.reg, ahora=T0, host="pc")
        self.assertNotIn("fallo de instrumento", html)


class Corte(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = Banco(self._tmp.name)
        self.addCleanup(self.b.reg.close)

    def test_un_resumen_largo_se_parte_sin_pasar_de_4096(self):
        identidades = [
            self.b.encargo(f"carga{i}", texto=f"pedido {i} " + "x" * 90) for i in range(60)
        ]
        html = resumen.texto(self.b.reg, ahora=T0, host="pc")
        partes = resumen.partir(html)
        self.assertGreater(len(partes), 1)
        for parte in partes:
            self.assertLessEqual(len(parte), 4096)
        junto = "\n".join(partes)
        for ident in identidades:
            self.assertEqual(junto.count(ident[:12]), 1)


class Envio(Base):
    def test_no_se_envia_si_nada_cambio_aunque_pasen_las_horas(self):
        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "espera",
            {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR},
            "espera-uno",
        )
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.resumenes()), 1)
        self.assertIn("ultima_firma", self.b.estado())
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 1)

    def test_se_envia_si_cambio_la_fase(self):
        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "espera",
            {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR},
            "espera-uno",
        )
        self.b.evento(ident, "fases", {"fases": [{"id": "A", "resultado": "uno"}]}, "fase-uno")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.resumenes()), 1)
        self.b.evento(
            ident,
            "fases",
            {"fases": [{"id": "A", "resultado": "uno"}, {"id": "B", "resultado": "dos"}]},
            "fase-dos",
        )
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 2)
        self.assertIn("1 de 2", self.b.resumenes()[-1]["cuerpo"])

    def test_se_envia_si_aparecio_una_alerta(self):
        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "espera",
            {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR},
            "espera-uno",
        )
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.resumenes()), 1)
        self.b.evento(
            ident,
            "impedimento",
            {
                "clase": "comprobacion",
                "causa": "falta el equipo de ensayo",
                "condicion": "conseguir el equipo",
            },
            "impedimento-uno",
        )
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 2)
        self.assertIn("impedimento", self.b.resumenes()[-1]["cuerpo"].lower())

    def test_el_intervalo_configurado_manda_el_cambio_a_su_hora(self):
        (self.b.base / "resumen-config.json").write_text(
            json.dumps({"canal": "tg", "intervalo_min": 120}), encoding="utf-8"
        )
        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "espera",
            {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR},
            "espera-uno",
        )
        self.b.evento(ident, "fases", {"fases": [{"id": "A", "resultado": "uno"}]}, "fase-uno")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.resumenes()), 1)
        self.b.evento(
            ident,
            "fases",
            {"fases": [{"id": "A", "resultado": "uno"}, {"id": "B", "resultado": "dos"}]},
            "fase-dos",
        )
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 1)
        directores.drenar(self.b.reg, ahora=T0 + 120 * 60)
        self.assertEqual(len(self.b.resumenes()), 2)

    def test_sin_encargos_activos_no_se_envia(self):
        ident = self.b.encargo("terminado")
        self.b.evento(ident, "fases", {"fases": [{"id": "A", "resultado": "x"}]}, "fase")
        self.b.evento(
            ident, "comprobacion", {"clase": "confirmado", "evidencia": "listo", "fase": "A"}, "cierre"
        )
        self.assertEqual(self.b.reg.fold(ident).estado, "terminado")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(self.b.resumenes(), [])

    def test_la_primera_puesta_en_marcha_activa_el_resumen_sin_config_manual(self):
        (self.b.base / "resumen-config.json").unlink()
        self.assertTrue(resumen.asegurar_config(self.b.base))
        self.assertEqual(resumen.config_envio(self.b.base), ("tg", 60))
        self.assertFalse(resumen.asegurar_config(self.b.base))

    def test_config_envio_apaga_el_envio_con_activo_false(self):
        (self.b.base / "resumen-config.json").write_text(
            json.dumps({"canal": "tg", "intervalo_min": 30, "activo": False}),
            encoding="utf-8",
        )
        self.assertEqual(resumen.config_envio(self.b.base), ("", 30))
        self.b.encargo("uno")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(self.b.resumenes(), [])

    def test_config_envio_no_usa_un_canal_sin_transporte(self):
        (self.b.base / "resumen-config.json").write_text(
            json.dumps({"canal": "fantasma", "intervalo_min": 60}), encoding="utf-8"
        )
        self.assertEqual(resumen.config_envio(self.b.base), ("", 60))
        canales = json.loads((self.b.base / "canal.json").read_text(encoding="utf-8"))
        canales["sin_transporte"] = {"idempotente": True}
        (self.b.base / "canal.json").write_text(json.dumps(canales), encoding="utf-8")
        (self.b.base / "resumen-config.json").write_text(
            json.dumps({"canal": "sin_transporte", "intervalo_min": 60}), encoding="utf-8"
        )
        self.assertEqual(resumen.config_envio(self.b.base), ("", 60))
        self.b.encargo("uno")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(self.b.resumenes(), [])

    def test_si_se_pierde_el_estado_no_duplica_ni_se_atasca(self):
        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "espera",
            {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR},
            "espera-uno",
        )
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.resumenes()), 1)
        (self.b.base / "resumen.json").unlink()
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 1, "los avisos publicados siguen siendo la prueba")
        self.assertIn("ultima_firma", self.b.estado())
        self.b.evento(
            ident,
            "fases",
            {"fases": [{"id": "A", "resultado": "uno"}, {"id": "B", "resultado": "dos"}]},
            "fase-dos",
        )
        directores.drenar(self.b.reg, ahora=T0 + 120 * 60)
        self.assertEqual(len(self.b.resumenes()), 2, "el resumen nuevo no queda atascado")

    def test_un_resumen_viejo_atascado_no_bloquea_el_nuevo(self):
        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "espera",
            {"motivo": "falta un entorno macOS", "reanuda_en": REANUDAR},
            "espera-uno",
        )
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.resumenes()), 1)
        viejo = next((self.b.base / "avisos").glob("*.json"))
        doc = json.loads(viejo.read_text(encoding="utf-8"))
        doc["id"] = "av_viejo"
        doc["firma"] = "firma-vieja"
        doc["estado"] = "incierto"
        doc["recibo"] = ""
        doc["cuerpo"] = "resumen viejo"
        (self.b.base / "avisos" / "av_viejo.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
        self.b.evento(
            ident,
            "fases",
            {"fases": [{"id": "A", "resultado": "uno"}, {"id": "B", "resultado": "dos"}]},
            "fase-dos",
        )
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 2)
        self.assertEqual(
            json.loads((self.b.base / "avisos" / "av_viejo.json").read_text())["estado"],
            "descartado",
        )

    def test_una_firma_que_reaparece_se_vuelve_a_enviar(self):
        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "intento_abierto",
            {"id": "i1", "pid": 11, "inicio": "x", "rol": "responsable", "agente": "claude", "modelo": "sonnet"},
            "i-sonnet",
        )
        self.b.evento(ident, "actividad", {"observada": True, "hecho": "implementando"}, "a-sonnet")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(len(self.b.resumenes()), 1)
        self.b.evento(
            ident,
            "impedimento",
            {"clase": "comprobacion", "causa": "falta el equipo", "condicion": "conseguirlo"},
            "impedimento",
        )
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 2)
        self.b.evento(
            ident,
            "impedimento_resuelto",
            {"clase": "comprobacion", "evidencia": "ya hay equipo"},
            "impedimento-resuelto",
        )
        directores.drenar(self.b.reg, ahora=T0 + 120 * 60)
        self.assertEqual(len(self.b.resumenes()), 3)
        self.assertIn("Sonnet implementando", self.b.resumenes()[-1]["cuerpo"])
        self.assertNotIn("impedimento", self.b.resumenes()[-1]["cuerpo"].lower())
        directores.drenar(self.b.reg, ahora=T0 + 121 * 60)
        self.assertEqual(len(self.b.resumenes()), 3, "no se repite sin cambio")
    def test_si_cerrar_no_corrio_el_aviso_publicado_sana_sin_reenviar(self):
        from unittest import mock

        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "intento_abierto",
            {"id": "i1", "pid": 11, "inicio": "x", "rol": "responsable", "agente": "claude", "modelo": "sonnet"},
            "i-sonnet",
        )
        self.b.evento(ident, "actividad", {"observada": True, "hecho": "implementando"}, "a-sonnet")
        directores.drenar(self.b.reg, ahora=T0)
        self.b.evento(
            ident,
            "impedimento",
            {"clase": "comprobacion", "causa": "falta el equipo", "condicion": "conseguirlo"},
            "impedimento",
        )
        directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.b.evento(
            ident,
            "impedimento_resuelto",
            {"clase": "comprobacion", "evidencia": "ya hay equipo"},
            "impedimento-resuelto",
        )
        with mock.patch(
            "motor.integraciones.directores.resumen_encargos.cerrar",
            return_value=False,
        ):
            directores.drenar(self.b.reg, ahora=T0 + 120 * 60)
        self.assertEqual(len(self.b.resumenes()), 3)
        publicados = [
            doc
            for doc in self.b.avisos()
            if doc.get("tipo") == "resumen" and doc.get("estado") == "publicado"
        ]
        ultimo = max(publicados, key=lambda doc: int(doc.get("episodio") or 0))
        firma = ultimo["firma"]
        self.assertNotEqual(
            self.b.estado().get("ultima_firma"), firma, "cerrar no corrió en el envío"
        )
        directores.drenar(self.b.reg, ahora=T0 + 181 * 60)
        self.assertEqual(len(self.b.resumenes()), 3, "el aviso publicado no se reenvía")
        self.assertEqual(
            self.b.estado().get("ultima_firma"), firma, "el siguiente drenado sana el estado"
        )
        self.assertEqual(self.b.estado().get("ultimo_episodio"), int(ultimo["episodio"]))

    def test_cerrar_fallido_en_el_envio_intermedio_no_pierde_la_vuelta(self):
        from unittest import mock

        ident = self.b.encargo("uno")
        self.b.evento(
            ident,
            "intento_abierto",
            {"id": "i1", "pid": 11, "inicio": "x", "rol": "responsable", "agente": "claude", "modelo": "sonnet"},
            "i-sonnet",
        )
        self.b.evento(ident, "actividad", {"observada": True, "hecho": "implementando"}, "a-sonnet")
        directores.drenar(self.b.reg, ahora=T0)
        self.b.evento(
            ident,
            "impedimento",
            {"clase": "comprobacion", "causa": "falta el equipo", "condicion": "conseguirlo"},
            "impedimento",
        )
        with mock.patch(
            "motor.integraciones.directores.resumen_encargos.cerrar",
            return_value=False,
        ):
            directores.drenar(self.b.reg, ahora=T0 + 60 * 60)
        self.assertEqual(len(self.b.resumenes()), 2)
        self.assertIn("ultima_firma", self.b.estado())
        self.b.evento(
            ident,
            "impedimento_resuelto",
            {"clase": "comprobacion", "evidencia": "ya hay equipo"},
            "impedimento-resuelto",
        )
        directores.drenar(self.b.reg, ahora=T0 + 120 * 60)
        self.assertEqual(len(self.b.resumenes()), 3, "la firma A vuelve a salir")
        self.assertIn("Sonnet implementando", self.b.resumenes()[-1]["cuerpo"])
        directores.drenar(self.b.reg, ahora=T0 + 121 * 60)
        self.assertEqual(len(self.b.resumenes()), 3, "no se repite sin cambio")

    def test_una_espera_rearmada_no_cambia_la_firma_ni_se_reenvia(self):
        (self.b.base / "resumen-config.json").write_text(
            json.dumps({"canal": "tg", "intervalo_min": 1}), encoding="utf-8"
        )
        ident = self.b.encargo("rearma")
        self.b.evento(ident, "fases", {"fases": [{"id": "A", "resultado": "x"}]}, "fase")
        motivo = "esperando checks requeridos del PR #9: bateria"
        for ronda in range(1, 21):
            hora = momento(ronda)
            self.b.evento(ident, "plazo", {}, f"plazo-{ronda}", hora=hora)
            self.b.evento(
                ident,
                "espera",
                {"motivo": motivo, "reanuda_en": momento(ronda + 1)},
                f"espera-{ronda}",
                hora=hora,
            )
        directores.drenar(self.b.reg, ahora=T0 + 20 * 60)
        self.assertEqual(len(self.b.resumenes()), 1)
        enviado = self.b.resumenes()[-1]["cuerpo"]
        self.assertIn("Ahora: esperando checks", enviado)
        self.assertIn("Desde: hace 19 min", enviado)
        self.assertNotIn("la misma espera", enviado)
        for ronda in range(21, 41):
            hora = momento(ronda)
            self.b.evento(ident, "plazo", {}, f"plazo-{ronda}", hora=hora)
            self.b.evento(
                ident,
                "espera",
                {"motivo": motivo, "reanuda_en": momento(ronda + 1)},
                f"espera-{ronda}",
                hora=hora,
            )
        directores.drenar(self.b.reg, ahora=T0 + 40 * 60)
        self.assertEqual(
            len(self.b.resumenes()), 1, "una espera normal rearmada no es un cambio"
        )

    def test_una_espera_sostenida_alerta_una_vez_y_no_se_reenvia(self):
        (self.b.base / "resumen-config.json").write_text(
            json.dumps({"canal": "tg", "intervalo_min": 1}), encoding="utf-8"
        )
        ident = self.b.encargo("trabado")
        motivo = "esperando checks requeridos del PR #9: bateria"

        def rondas(desde: int, hasta: int) -> None:
            for ronda in range(desde, hasta):
                hora = momento(ronda)
                self.b.evento(ident, "plazo", {}, f"plazo-{ronda}", hora=hora)
                self.b.evento(
                    ident,
                    "espera",
                    {"motivo": motivo, "reanuda_en": momento(ronda + 1)},
                    f"espera-{ronda}",
                    hora=hora,
                )

        rondas(1, 11)
        directores.drenar(self.b.reg, ahora=T0 + 10 * 60)
        self.assertEqual(len(self.b.resumenes()), 1)
        self.assertNotIn("la misma espera", self.b.resumenes()[-1]["cuerpo"])
        rondas(11, 131)
        directores.drenar(self.b.reg, ahora=T0 + 131 * 60)
        self.assertEqual(len(self.b.resumenes()), 2)
        self.assertIn(
            "la misma espera lleva más de 2 h", self.b.resumenes()[-1]["cuerpo"]
        )
        rondas(131, 251)
        directores.drenar(self.b.reg, ahora=T0 + 251 * 60)
        self.assertEqual(
            len(self.b.resumenes()), 2, "la alerta aparece una vez y no se reenvía"
        )

    def test_un_envio_fallido_se_reintenta_por_el_mismo_camino(self):
        self.b.encargo("uno")
        (self.b.base / "falla").write_text("x")
        directores.drenar(self.b.reg, ahora=T0)
        self.assertEqual(self.b.resumenes(), [])
        (self.b.base / "falla").unlink()
        directores.drenar(self.b.reg, ahora=T0 + 61)
        self.assertEqual(len(self.b.resumenes()), 1)
        self.assertIn("ultima_firma", self.b.estado())


class FormatoLegible(Base):
    """El resumen va con negritas de Telegram. Su aviso se declara `html` para que el
    transporte no lo escape; sin esa marca las etiquetas llegaban a la vista."""

    def _con_modelo(self, clave: str, modelo: str, rol: str = "responsable") -> str:
        ident = self.b.encargo(clave, repo="git@github.com:gon0801/goncloud-motor.git")
        self.b.evento(
            ident,
            "intento_abierto",
            {"id": "i-" + clave, "pid": 21, "inicio": "x", "rol": rol, "agente": "opencode", "modelo": modelo},
            "i-" + clave,
        )
        self.b.evento(ident, "actividad", {"observada": True, "hecho": "trabajando"}, "a-" + clave)
        return ident

    def test_el_resumen_solo_usa_negritas(self):
        self._con_modelo("negritas", "zai-coding-plan/glm-5.3-flash")
        texto = resumen.texto(self.b.reg, ahora=T0, host="pc")
        self.assertIn("<b>Resumen de encargos</b>", texto)
        etiquetas = set(re.findall(r"</?([a-z]+)", texto))
        self.assertEqual(etiquetas, {"b"})
        self.assertEqual(texto.count("<b>"), texto.count("</b>"))

    def test_el_texto_variable_se_escapa(self):
        self.b.encargo("escapa", texto="Título <b>crudo</b> & demás")
        texto = resumen.texto(self.b.reg, ahora=T0, host="pc")
        self.assertIn("Título &lt;b&gt;crudo&lt;/b&gt; &amp; demás", texto)
        self.assertNotIn("<b>crudo</b>", texto)

    def test_el_transporte_recibe_el_resumen_marcado_como_html(self):
        # Con fases el encargo tambien emite su aviso de inicio, que debe ir como texto.
        activo(self.b, "marca")
        directores.drenar(self.b.reg, ahora=T0 + 2 * 60 * 60)
        enviados = self.b.resumenes()
        self.assertTrue(enviados)
        self.assertEqual({e["formato"] for e in enviados}, {"html"})
        self.assertIn("<b>Resumen de encargos</b>", enviados[-1]["cuerpo"])
        log = self.b.base / "entregados.jsonl"
        otros = [
            json.loads(linea) for linea in log.read_text(encoding="utf-8").splitlines()
            if linea and not json.loads(linea)["evidencia"].startswith("resumen:")
        ]
        self.assertTrue(otros, "el escenario debe enviar al menos un aviso que no es resumen")
        for aviso in otros:
            self.assertEqual(aviso["formato"], "texto", aviso["evidencia"])

    def test_el_modelo_sale_con_su_nombre_llano(self):
        for clave, modelo, rol, esperado in (
            ("glm", "zai-coding-plan/glm-5.3-flash", "responsable", "GLM implementando"),
            ("ds", "opencode-go/deepseek-v4.1-flash", "responsable", "DeepSeek implementando"),
            ("op", "anthropic/claude-opus-5-5", "revisor", "Opus revisando"),
            ("kimi", "kimi-k3.1-pro", "responsable", "Kimi implementando"),
        ):
            self._con_modelo(clave, modelo, rol)
            texto = resumen.texto(self.b.reg, ahora=T0, host="pc")
            self.assertIn(esperado, texto, modelo)
        self.assertNotIn("Zai-coding-plan", texto)
        for proveedor in ("coding-plan", "opencode-go/", "anthropic/"):
            self.assertNotIn(proveedor, texto)

    def test_el_pr_sale_con_el_nombre_corto_del_repo(self):
        self.assertEqual(resumen._repo_corto("git@github.com:gon0801/goncloud-motor.git"), "goncloud-motor")
        self.assertEqual(resumen._repo_corto("https://github.com/gon0801/goncloud-motor"), "goncloud-motor")
        self.assertEqual(resumen._repo_corto("gon0801/goncloud-motor"), "goncloud-motor")
        self.assertEqual(resumen._repo_corto(""), "")

    @unittest.skipUnless(hasattr(time, "tzset"), "cambiar la zona del proceso necesita tzset")
    def test_la_hora_es_la_misma_en_todas_las_maquinas(self):
        # 04:24 UTC del 2-nov es 21:24 del 1-nov en Vancouver (UTC-7 todo el año),
        # sin importar la zona configurada en la máquina que arma el resumen.
        epoch = datetime(2026, 11, 2, 4, 24, tzinfo=timezone.utc).timestamp()
        anterior = os.environ.get("TZ")
        try:
            for zona in ("America/Los_Angeles", "UTC", "Asia/Tokyo"):
                os.environ["TZ"] = zona
                time.tzset()
                self.assertEqual(resumen._hora_legible(epoch), "1 nov, 21:24", zona)
        finally:
            if anterior is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = anterior
            time.tzset()

    def test_la_espera_del_comprobador_sale_sin_version(self):
        motivo = "esperando la observacion del comprobador sobre la version 08d860918226 antes de publicar"
        self.assertEqual(
            resumen._espera_en_palabras(motivo),
            "esperando las comprobaciones antes de publicar",
        )
        self.assertEqual(
            resumen._espera_en_palabras("esperando el turno del agente"),
            "esperando el turno del agente",
        )

    def test_la_maquina_sale_con_un_nombre_legible(self):
        self.assertEqual(resumen._maquina("Gons-Mac-Mini-2.local"), "Mac Mini")
        self.assertEqual(resumen._maquina("Gon-PC"), "PC")
        self.assertEqual(resumen._maquina("Davids-MacBook-Pro"), "MacBook")
        self.assertEqual(resumen._maquina("pc-ensayo"), "pc-ensayo")

if __name__ == "__main__":
    unittest.main()

