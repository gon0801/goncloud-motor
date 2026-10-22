"""Un archivo, un texto o un resultado malo ya no tumban al motor (MOTOR 02, bloque B)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "proto"))
import sandbox  # noqa: E402

from motor.daemon import Daemon  # noqa: E402
from motor.notify import PLANTILLAS, lint_plain, render  # noqa: E402
from motor.plan import parse_plan  # noqa: E402


class Banco(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="motor-no-caerse-"))
        self.home, self.plan_md = sandbox.create(self.tmp / "b")
        self.plan = parse_plan(self.plan_md.read_text(encoding="utf-8")).to_dict()

    def daemon(self) -> Daemon:
        d = Daemon(self.home)
        d.fx.start_attempt = lambda plan, snap, a: (
            None
        )  # el tick no lanza agentes de verdad
        return d

    def avisos(self, parte: str) -> list[Path]:
        return sorted((self.home / "avisos" / "pendientes").glob(f"*{parte}*.txt"))

    def eventos(self, kind: str):
        return [e for e in Daemon(self.home).store.events() if e.kind == kind]


class BandejaTest(Banco):
    def deja(self, nombre: str, contenido: str | bytes) -> Path:
        inbox = self.home / "inbox"
        inbox.mkdir(exist_ok=True)
        f = inbox / nombre
        if isinstance(contenido, bytes):
            f.write_bytes(contenido)
        else:
            f.write_text(
                json.dumps(contenido) if not isinstance(contenido, str) else contenido
            )
        return f

    def plan_msg(self, cmd_id: str, plan: dict) -> dict:
        return {"cmd_id": cmd_id, "tipo": "plan", "plan": plan, "por": "Claude"}

    def test_lo_malo_se_aparta_y_el_plan_bueno_se_toma_en_el_mismo_tick(self):
        sin_id = {k: v for k, v in self.plan.items() if k != "id"}
        repo_malo = sandbox._repo(
            self.tmp / "b", "malo", {".motor.toml": "esto = = no es toml\n"}
        )
        con_toml_malo = {
            **self.plan,
            "id": "toml-malo",
            "blocks": [{**b, "repo": repo_malo} for b in self.plan["blocks"]],
        }
        malos = {
            "1-roto.json": "{no es json",
            "2-utf8.json": b"\xff\xfe\x00{",
            "3-sin-cmd-id.json": {"tipo": "plan", "plan": self.plan},
            "4-sin-id.json": self.plan_msg("c4", sin_id),
            "5-toml.json": self.plan_msg("c5", con_toml_malo),
            "6-nota-sin-block.json": {"cmd_id": "c6", "tipo": "nota", "texto": "x"},
            "7-tipo-numero.json": {"cmd_id": "c7", "tipo": 3},
        }
        for nombre, contenido in malos.items():
            self.deja(nombre, contenido)
        self.deja("9-bueno.json", self.plan_msg("c9", self.plan))
        d = self.daemon()
        d.tick()
        rechazados = sorted(
            p.name for p in (self.home / "inbox" / "rechazados").iterdir()
        )
        self.assertEqual(rechazados, sorted(malos))
        self.assertEqual([p.id for p in d.plans()], ["demo"])
        recibos = {
            e.data["cmd_id"]: e.data["resultado"]
            for e in self.eventos("CommandReceived")
        }
        self.assertEqual(recibos["c9"], "tomado")
        for cmd_id in ("c4", "c5", "c6", "c7", "3-sin-cmd-id", "1-roto", "2-utf8"):
            self.assertTrue(
                recibos[cmd_id].startswith("rechazado: "), (cmd_id, recibos)
            )
        self.assertEqual(
            len(self.avisos("rechazo")), len(malos), "un aviso por archivo rechazado"
        )
        self.assertTrue(list(self.home.glob("inbox/procesados/9-bueno.json")))

    def test_un_aviso_por_rechazo_aunque_pasen_mas_ticks(self):
        self.deja("1-roto.json", "{")
        d = self.daemon()
        d.tick()
        d.tick()
        self.assertEqual(len(self.avisos("rechazo")), 1)


CAMPOS = {
    "motivo": "no pude leer el plan",
    "por": "Claude",
    "n": "1",
    "total": "3",
    "aviso": "ya puedes saludar",
    "prueba": "pasó",
    "que_hacer": "entra a Seller Central",
    "hasta": "1792000000",
    "bloque": "el despliegue",
    "listas": "2",
    "ahora": "Ahora trabajo en «el reporte».",
    "atoron": "Nada atorado.",
}


class AvisosTest(unittest.TestCase):
    def test_ningun_titulo_con_jerga_tumba_un_aviso(self):
        titulos = (
            "Webhooks JSON de Shopify",
            "Sincronizar /ads/campaign",
            "Reporte 20261021",
            "commit diario",
        )
        for plantilla in PLANTILLAS:
            for titulo in titulos:
                with self.subTest(plantilla=plantilla, titulo=titulo):
                    texto = render(plantilla, CAMPOS, "Mini", titulo)
                    self.assertEqual(lint_plain(texto), texto)

    def test_con_jerga_en_los_campos_o_campos_que_faltan_tampoco_lanza(self):
        malos = {k: "commit 3eda9854 en /var/log/x" for k in CAMPOS} | {
            "hasta": "no es una fecha"
        }
        for plantilla in PLANTILLAS:
            for campos in (malos, {}):
                with self.subTest(plantilla=plantilla, campos=bool(campos)):
                    texto = render(plantilla, campos, "Mini", "x")
                    self.assertEqual(lint_plain(texto), texto)

    def test_primero_cambia_el_titulo_y_conserva_los_datos(self):
        texto = render("entregado", CAMPOS, "Mini", "commit diario")
        self.assertIn("En la Mini · un plan:", texto)
        self.assertIn("ya puedes saludar", texto)

    def test_un_aviso_sano_no_cambia(self):
        texto = render("terminado", {}, "Mini", "Reporte de ventas")
        self.assertTrue(texto.startswith("En la Mini · Reporte de ventas:"))


if __name__ == "__main__":
    unittest.main()
