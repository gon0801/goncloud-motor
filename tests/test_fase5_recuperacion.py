import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from motor.encargos import ciclo, dominio
from motor.encargos.registro import Registro
from motor.integraciones import directores


HORA = "2026-10-29T10:00:00Z"


class RecuperacionFase5(unittest.TestCase):
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
