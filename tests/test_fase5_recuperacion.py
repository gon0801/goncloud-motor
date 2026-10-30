import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from motor.encargos import ciclo, dominio
from motor.encargos.comprobacion import hallazgos_deepseek, version_producto
from motor.encargos.registro import Registro
from motor.github import GhGitHub, PrView, veredicto
from motor.integraciones import directores


HORA = "2026-10-29T10:00:00Z"


class RecuperacionFase5(unittest.TestCase):
    def test_transporte_utf8_envia_y_concilia_acentos_y_emoji(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            reg = Registro(raiz / "base")
            recibo = "recibo-operación-🚀"
            sink = raiz / "transporte.py"
            sink.write_text(
                "import json,sys\nfrom pathlib import Path\n"
                f"respuesta={{'recibo':{recibo!r}}}\n"
                "if sys.argv[2]=='texto':\n"
                " datos=sys.stdin.read()\n"
                " Path(sys.argv[1]).write_text(datos,encoding='utf-8')\n"
                " print(json.dumps(respuesta,ensure_ascii=False))\n"
                "else:\n"
                " datos=sys.stdin.buffer.read()\n datos.decode('utf-8')\n"
                " Path(sys.argv[1]).write_bytes(datos)\n"
                " sys.stdout.buffer.write(json.dumps(respuesta,ensure_ascii=False).encode('utf-8'))\n",
                encoding="utf-8",
            )
            aviso = {"id": "av_unicode", "encargo": "enc_temporal", "canal": "local"}
            try:
                with patch.dict(os.environ):
                    os.environ.pop("PYTHONIOENCODING", None)
                    for modo in ("buffer", "texto"):
                        for n, cuerpo in enumerate(("Operación completada. Próxima revisión.", "Revisión completada 🚀")):
                            envio = raiz / f"envio-{modo}-{n}.txt"
                            comando = [sys.executable, "-B", "-X", "utf8=0", str(sink), str(envio), modo]
                            with self.subTest(operacion="enviar", cuerpo=cuerpo, io=modo):
                                self.assertEqual(
                                    directores._ejecutar_transporte(reg, comando, aviso, cuerpo),
                                    (recibo, ""),
                                )
                                self.assertEqual(envio.read_bytes(), cuerpo.encode("utf-8"))
                            conciliacion = raiz / f"conciliacion-{modo}-{n}.txt"
                            comando[-2] = str(conciliacion)
                            with self.subTest(operacion="conciliar", cuerpo=cuerpo, io=modo):
                                self.assertEqual(
                                    directores._conciliar_transporte(reg, comando, aviso, cuerpo),
                                    ("hallado", recibo),
                                )
                                self.assertEqual(conciliacion.read_bytes(), cuerpo.encode("utf-8"))
            finally:
                reg.close()

    def test_deepseek_schema3_concilia_F1_resuelto_ya_persistido(self):
        sha = "aa335ac97b3a85a6fdade67fbcc5ada58c869d2f"
        contenido = {
            "schema": 3,
            "findings": [
                {"id": "F1", "title": "La evidencia y el script referenciados no existen",
                 "severity": "High", "status": {"kind": "resolved", "at_sha": "7d9787d790096b3a88d85c87a6c07d04c5fb0d39"},
                 "primary_anchor": {"kind": "legacy", "path": "reporte.md", "line": 40}},
                {"id": "F2", "title": "Siguiente paso inconsistente con la tabla consultada",
                 "severity": "Low", "status": {"kind": "open"},
                 "primary_anchor": {"kind": "legacy", "path": "reporte.md", "line": 46}},
            ],
        }
        body = (
            "<!-- ai-review:sticky -->\n"
            f"<!-- ai-review:sha={sha} -->\n"
            f"<!-- ai-review:completion={sha}:complete -->\n"
            f"<!-- ai-review:findings={json.dumps(contenido)} -->"
        )
        filas = [{"body": body, "user": {"login": "github-actions[bot]"}}]
        gh = GhGitHub()
        gh._gh = lambda *args: json.dumps(filas) if "/issues/" in args[1] else "[]"
        comentarios = gh.comentarios_deepseek("gon0801/motor-sandbox", 28, sha)
        hallazgos = hallazgos_deepseek(comentarios, sha)
        self.assertEqual([h.id for h in hallazgos], ["F2"])
        self.assertEqual(hallazgos[0].ancla, "reporte.md:46")
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                ident = ciclo.recibir(
                    reg, "publicar reporte", "deepseek-resuelto", HORA,
                    entrega_nivel="pr", repo="gon0801/motor-sandbox", base_rama="main",
                )["encargo"]
                espacio = ciclo._espacio(reg.base, ident)
                (espacio / "reporte.md").write_text("producto aprobado\n")
                producto = version_producto(espacio)
                eventos = [
                    ("producto_listo", {"version": producto.firma, "manifiesto": producto.manifiesto}),
                    ("revision", {"version": producto.firma, "veredicto": "aprobado",
                                  "evidencia": "producto comprobado", "origen": "independiente"}),
                    ("entrega_externa", {"paso": "commit", "sha": sha}),
                    ("entrega_externa", {"paso": "pr_listo", "numero": 28, "sha": sha}),
                    ("revision", {"revision_id": "rev_ds_" + sha[:12], "version": producto.firma,
                                  "veredicto": "cambios", "origen": "deepseek", "hallazgos": [
                                      {"id": "F1", "titulo": contenido["findings"][0]["title"],
                                       "gravedad": "bloqueante", "criterio": "comportamiento",
                                       "estado": "abierto", "origen": "deepseek"}]}),
                    ("entrega_externa", {"paso": "deepseek_leido", "sha": sha}),
                ]
                for n, (kind, data) in enumerate(eventos):
                    clave = f"anterior:{n}"
                    if kind == "revision" and data.get("origen") == "deepseek":
                        clave = f"revision-ds:{ident}:{reg.fold(ident).fase_activa}:{sha}"
                    elif kind == "entrega_externa" and data.get("paso") == "deepseek_leido":
                        clave = f"entrega:{ident}:{reg.fold(ident).fase_activa}:deepseek_leido:{sha}"
                    reg.anotar(ident, {"kind": kind, "data": {**data, "hora": HORA}}, clave)
                self.assertEqual([h.id for h in dominio.bloqueantes_abiertos(reg.fold(ident))], ["F1"])
                ciclo.ordenar(reg, ident, "parar", "parada-deepseek", HORA)
                reg.anotar(ident, {"kind": "arbol", "data": {
                    "observacion": "ausente", "hora": HORA,
                }}, "arbol-deepseek")
                continuada = ciclo.ordenar(reg, ident, "continuar", "continuar-deepseek", HORA)
                self.assertNotIn("rechazada", continuada)
                gh.ver = lambda repo, numero: PrView(numero, reg.fold(ident).entrega.rama, "main", False, sha, "abierto")
                gh.checks = lambda *args, **kw: veredicto(sha, (), [])
                with patch.object(ciclo, "_asegurar_repo", return_value=""), \
                     patch.object(ciclo, "_commit_push", return_value=(sha, "")), \
                     patch.object(ciclo, "_checks_extra", return_value=()), \
                     patch.object(ciclo, "_sonda_entorno", return_value=SimpleNamespace(ok=True)):
                    ciclo.barrer(reg, HORA, 0, None, gh=gh)
                    final = reg.fold(ident)
                    self.assertEqual(final.estado, "terminado")
                    self.assertFalse(dominio.bloqueantes_abiertos(final))
                    self.assertEqual(next(h.estado for h in final.hallazgos if h.id == "F1"), "resuelto")
                    self.assertEqual(next(h.ancla for h in final.hallazgos if h.id == "F2"), "reporte.md:46")
                    antes = len(reg.eventos(ident))
                    ciclo.barrer(reg, HORA, 0, None, gh=gh)
                    self.assertEqual(len(reg.eventos(ident)), antes)
            finally:
                reg.close()

    def test_espera_tardia_conserva_parada_y_continuacion_idempotente(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                ident = ciclo.recibir(
                    reg, "reanudar el mismo ensayo", "espera-tardia", "2026-10-30T20:26:03Z",
                )["encargo"]
                parada = ciclo.ordenar(reg, ident, "parar", "parada", "2026-10-30T20:26:05Z")
                reg.anotar(ident, {"kind": "arbol", "data": {
                    "observacion": "ausente", "hora": "2026-10-30T20:26:05Z",
                }}, "arbol-ausente")
                ciclo.marcar_espera(
                    reg, ident,
                    "esperando revisión de DeepSeek para el SHA f207a080f96f0741e5394894745b7d12869b64d6",
                    "2026-10-30T20:27:04Z", "2026-10-30T20:26:04Z",
                )
                detenido = reg.fold(ident)
                self.assertEqual(detenido.estado, "detenido")
                self.assertFalse(detenido.escrituras)
                self.assertFalse(detenido.intentos_nuevos)
                repetida = ciclo.ordenar(reg, ident, "parar", "parada", "2026-10-30T20:28:00Z")
                self.assertEqual(repetida["recibo"], parada["recibo"])
                self.assertTrue(repetida["retransmision"])
                continuada = ciclo.ordenar(reg, ident, "continuar", "continuacion", "2026-10-30T20:28:01Z")
                self.assertEqual(continuada["estado"], "recibido")
                self.assertNotIn("rechazada", continuada)
                antes = len(reg.eventos(ident))
                repetida = ciclo.ordenar(reg, ident, "continuar", "continuacion", "2026-10-30T20:28:02Z")
                self.assertEqual(repetida["recibo"], continuada["recibo"])
                self.assertTrue(repetida["retransmision"])
                self.assertEqual(len(reg.eventos(ident)), antes)
                self.assertEqual(sum(
                    e["kind"] == "orden" and e["data"]["accion"] == "continuar"
                    for e in reg.eventos(ident)
                ), 1)
                actual = reg.fold(ident)
                self.assertTrue(actual.escrituras)
                self.assertTrue(actual.intentos_nuevos)
                self.assertEqual(actual.efectos, detenido.efectos)
            finally:
                reg.close()

    def test_pedido_de_despliegue_invalido_se_contiene_y_se_recupera(self):
        for invalido in (42, [42]):
            with self.subTest(pedido=invalido), tempfile.TemporaryDirectory() as tmp:
                reg = Registro(Path(tmp) / "base")
                try:
                    ident = ciclo.recibir(
                        reg, "desplegar", "despliegue", HORA,
                        entrega_nivel="desplegado", repo="ejemplo/repo", base_rama="main",
                    )["encargo"]
                    reg.anotar(ident, {"kind": "entrega_externa", "data": {
                        "paso": "integrado", "sha": "a" * 40, "hora": HORA,
                    }}, "integrado")
                    espacio = reg.base / "espacio" / ident
                    interno = espacio / ".interno"
                    interno.mkdir(parents=True)
                    pedido = interno / "pedido-efecto.json"
                    pedido.write_text(json.dumps(invalido))
                    bloqueado = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
                    self.assertEqual(bloqueado.impedimento, "pedido-efecto.json")
                    self.assertTrue(bloqueado.impedimento_causa)
                    self.assertEqual(bloqueado.nivel_alcanzado, "integrado")
                    self.assertFalse(dominio.decision_proceso(
                        bloqueado, "ausente", False, True, True, HORA,
                    )["lanzar"])
                    self.assertFalse((reg.base / "destino").exists())
                    antes = len(reg.eventos(ident))
                    ciclo._paso_despliegue(reg, bloqueado, espacio, HORA)
                    self.assertEqual(len(reg.eventos(ident)), antes)
                    pedido.write_text(json.dumps({
                        "id": "instalar", "operacion": "desplegar",
                        "clave": "maquina-temporal", "cuerpo": "version aprobada",
                    }))
                    self.assertTrue(ciclo._verificar_impedimento(
                        bloqueado.impedimento, bloqueado, espacio, reg.base, reg,
                    ))
                    ciclo._resolver_impedimento(reg, bloqueado, bloqueado.impedimento, HORA)
                    final = ciclo._paso_despliegue(reg, reg.fold(ident), espacio, HORA)
                    self.assertEqual(final.nivel_alcanzado, "desplegado")
                    self.assertTrue(final.efectos[0].recibo)
                    antes = len(reg.eventos(ident))
                    ciclo._paso_despliegue(reg, final, espacio, HORA)
                    self.assertEqual(len(reg.eventos(ident)), antes)
                finally:
                    reg.close()

    def test_actualizar_avisos_conserva_pendientes_y_solo_anuncia_nuevos_avances(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            with sqlite3.connect(base / "registro.sqlite") as db:
                db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "ts TEXT NOT NULL, encargo TEXT NOT NULL, kind TEXT NOT NULL, "
                           "data TEXT NOT NULL, ukey TEXT UNIQUE)")
                eventos = [
                    ("antiguo", "entrega", {"id": "antiguo", "clave": "viejo", "huella": "h",
                        "texto": "pedido anterior", "canal": "local"}),
                    ("antiguo", "fases", {"fases": [{"id": "A", "resultado": "fase anterior"}]}),
                    ("antiguo", "solicitud", {"id": "pregunta", "operacion": "permiso",
                        "fase": "A", "intento": "anterior", "estado": "pregunta",
                        "pregunta": "falta una decisión pendiente"}),
                    ("cerrado", "entrega", {"id": "cerrado", "clave": "cerrado", "huella": "h2",
                        "texto": "resultado previo", "canal": "local", "via": "directa"}),
                    ("cerrado", "resultado_directo", {"evidencia": "resultado legítimo sin enviar"}),
                ]
                for n, (ident, kind, data) in enumerate(eventos):
                    db.execute("INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
                               (HORA, ident, kind, json.dumps({**data, "hora": HORA}), str(n)))
            transporte = base / "transporte.py"
            transporte.write_text('import json,sys\nsys.stdin.read()\n'
                                  'print(json.dumps({"recibo":"temporal"}))\n')
            (base / "canal.json").write_text(json.dumps({"local": {
                "comando": [sys.executable, "-B", str(transporte)], "idempotente": True,
            }}))
            reg = Registro(base)
            corte = reg.corte_avisos()
            nuevo = directores.encargar(
                reg, "nuevo con fecha antigua", "nuevo", HORA, "hermes", "local", "durable",
            )["encargo"]
            reg.anotar(nuevo, {"kind": "fases", "data": {
                "fases": [{"id": "N", "resultado": "fase nueva"}], "hora": HORA,
            }}, "fase-nueva")
            reg.close()
            reg = Registro(base)
            try:
                self.assertEqual(reg.corte_avisos(), corte)
                avisos = directores.drenar(reg)["avisos"]
                enviados = {(a["encargo"], a.get("tipo")) for a in avisos if a["entregado"]}
                self.assertEqual(enviados, {
                    ("antiguo", "pregunta"), ("cerrado", None), (nuevo, "fase"),
                })
                anterior = reg.fold("antiguo", hasta=corte)
                historico = next(h for h in directores.hechos_de_avance(anterior) if h["tipo"] == "fase")
                aviso_historico = directores._aviso_de_hecho(anterior, historico)
                self.assertFalse((base / "avisos" / f"{aviso_historico['id']}.json").exists())
                self.assertTrue(all(a.get("recibo") for a in avisos))
                reg.anotar("antiguo", {"kind": "fases", "data": {
                    "fases": [{"id": "A", "resultado": "fase con evidencia nueva"}], "hora": HORA,
                }}, "avance-posterior")
                avisos = directores.drenar(reg)["avisos"]
                nuevos = [a for a in avisos if a["novedad"]]
                self.assertEqual([(a["encargo"], a["tipo"]) for a in nuevos], [("antiguo", "fase")])
                self.assertFalse(any(a["novedad"] for a in directores.drenar(reg)["avisos"]))
            finally:
                reg.close()
