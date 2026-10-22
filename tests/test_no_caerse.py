"""Un archivo, un texto o un resultado malo ya no tumban al motor (MOTOR 02, bloque B)."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "proto"))
import sandbox  # noqa: E402

from motor import machine as m  # noqa: E402
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


class TickTest(Banco):
    def entrega(self, plan_id: str) -> Daemon:
        d = self.daemon()
        d.store.append(
            plan_id,
            "",
            "PlanLoaded",
            {"plan": {**self.plan, "id": plan_id}, "por": "test"},
            ukey=f"plan:{plan_id}",
        )
        return d

    def fallas(self, d: Daemon) -> dict[str, list]:
        porfase: dict[str, list] = {}
        for e in d.store.events():
            if e.kind == "EffectFailed":
                porfase.setdefault(e.data["accion"], []).append(e)
        return porfase

    def test_un_plan_con_el_log_roto_no_detiene_a_otro(self):
        d = self.entrega("roto")
        d.store.append(
            "roto",
            "A",
            "Reviewed",
            {"sha": "x", "veredicto": "CAMBIOS", "bloqueantes": ["no soy un dict", 3]},
            ukey="malo",
        )
        d = self.entrega("sano")
        lanzados = []
        d.fx.start_attempt = lambda plan, snap, a: lanzados.append(plan.id)
        d.tick()
        self.assertIn("sano", lanzados, "el plan sano avanza")
        fallas = self.fallas(d)
        self.assertEqual({e.plan for es in fallas.values() for e in es}, {"roto"})

    def test_un_ajusta_sueno_que_se_pasa_del_tope_no_detiene_el_tick(self):
        d = self.entrega("demo")
        lanzados = []
        d.fx.start_attempt = lambda plan, snap, a: lanzados.append(plan.id)

        def se_pasa(hay_trabajo, now):
            raise subprocess.TimeoutExpired("caffeinate", 60)

        d.ajusta_sueno = se_pasa
        d.tick()
        self.assertTrue(lanzados, "los planes avanzaron antes de la fase que falló")
        self.assertEqual(list(self.fallas(d)), ["ajusta_sueno"])

    def test_cada_fase_suelta_se_anota_y_deja_seguir(self):
        for fase, donde in (
            ("trae_conf", "trae_conf"),
            ("recargar_conf", "recargar_conf"),
            ("purga_registros", "fx"),
            ("envia_pendientes", "notifier"),
        ):
            with self.subTest(fase=fase):
                d = self.entrega("demo")
                destino = {"fx": d.fx, "notifier": d.cx.notifier}.get(donde, d)

                def revienta(*a, **k):
                    raise OSError("se cayó")

                setattr(destino, fase, revienta)
                d.tick()
                self.assertIn(fase, self.fallas(d))
                self.assertIn(
                    "demo",
                    {e.plan for e in d.store.events() if e.kind == "AttemptStarted"}
                    | {e.plan for e in d.store.events() if e.kind == "PlanLoaded"},
                )

    def test_run_atrapa_un_tick_malo_lo_escribe_y_sigue(self):
        d = self.daemon()
        llamadas = []

        def tick(now=None):
            llamadas.append(1)
            if len(llamadas) == 1:
                raise RuntimeError("tick roto")
            return True

        d.tick = tick
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(d.run(0.01, True, 5), 0)
        self.assertEqual(len(llamadas), 2)
        self.assertIn("tick roto", err.getvalue())


class ResultadoTest(Banco):
    def collect(self, rol: str, resultado: object | str) -> dict | None:
        d = self.daemon()
        d.store.append("demo", "", "PlanLoaded", {"plan": self.plan, "por": "t"})
        carpeta = self.tmp / f"intento-{rol}"
        carpeta.mkdir(exist_ok=True)
        (carpeta / "outcome.json").write_text(
            json.dumps(
                {"exit": 0, "timed_out": False, "no_progress": False, "tail": ""}
            )
        )
        (carpeta / "result.json").write_text(
            resultado if isinstance(resultado, str) else json.dumps(resultado)
        )
        att = m.Attempt(
            n=1,
            role=rol,
            modelo="sonnet",
            started=0.0,
            proc={},
            dir=str(carpeta),
            sha="a" * 40,
        )
        plan = d.plans()[0]
        snap = m.fold(plan, d.store.events("demo"))
        return d.fx.collect(plan, snap, "A", att)

    def veredicto(self, **cambios) -> dict:
        return {
            "sha": "a" * 40,
            "veredicto": "CAMBIOS",
            "bloqueantes": [{"texto": "falla"}],
            "notas": ["una nota"],
            **cambios,
        }

    def test_implementar_con_un_resultado_que_no_cumple_es_sin_resultado(self):
        casos = {
            "una lista": ["sha", "resumen"],
            "un texto": '"hecho"',
            "sin resumen": {"sha": "a" * 40},
            "sha que no es texto": {"sha": 3, "resumen": "x"},
            "resumen nulo": {"sha": "a" * 40, "resumen": None},
            "json roto": "{no",
        }
        for nombre, res in casos.items():
            with self.subTest(nombre):
                out = self.collect("implementar", res)
                self.assertEqual(out["kind"], "sin_resultado", out)
                self.assertTrue(out["detalle"])

    def test_revisar_con_un_resultado_que_no_cumple_es_veredicto_invalido(self):
        casos = {
            "una lista": [],
            "bloqueantes con textos": self.veredicto(bloqueantes=["texto"]),
            "bloqueantes nulo": self.veredicto(bloqueantes=None),
            "bloqueante sin texto": self.veredicto(bloqueantes=[{"repro": "false"}]),
            "repro que no es texto": self.veredicto(
                bloqueantes=[{"texto": "x", "repro": 1}]
            ),
            "arreglo que no es texto": self.veredicto(
                bloqueantes=[{"texto": "x", "arreglo": ["a"]}]
            ),
            "sin notas": {k: v for k, v in self.veredicto().items() if k != "notas"},
            "notas que no son textos": self.veredicto(notas=[1]),
            "veredicto raro": self.veredicto(veredicto="TAL VEZ"),
            "sin sha": {k: v for k, v in self.veredicto().items() if k != "sha"},
        }
        for nombre, res in casos.items():
            with self.subTest(nombre):
                out = self.collect("revisar", res)
                self.assertEqual(out["kind"], "veredicto_invalido", out)
                self.assertTrue(out["detalle"])

    def test_el_motivo_dice_que_falla(self):
        out = self.collect("revisar", self.veredicto(bloqueantes=[{"repro": "x"}]))
        self.assertIn("bloqueantes[0]", out["detalle"])
        self.assertIn("texto", out["detalle"])
        out = self.collect("implementar", ["x"])
        self.assertIn("no es un objeto", out["detalle"])

    def test_un_veredicto_bien_formado_se_registra(self):
        res = self.veredicto(
            bloqueantes=[{"texto": "falla", "repro": "", "arreglo": "x"}]
        )
        out = self.collect("revisar", res)
        self.assertEqual(out, {"kind": "veredicto"})


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
