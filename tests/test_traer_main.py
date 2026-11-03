"""Traer la base vigente a la rama de un encargo desde el host.

El repo es real: un bare y un clon para el encargo. La mezcla limpia se publica
con su nota; el choque deja la mezcla en el espacio y devuelve el encargo al
responsable.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.__main__ import main as motor_main
from motor.encargos import ciclo
from motor.encargos.dominio import Encargo, EntregaAutorizada, Fase, VersionProducto
from motor.encargos.integracion import traer_main

HORA = "2026-11-01T12:00:00Z"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@l", *args],
        check=True, capture_output=True, text=True,
    ).stdout


class _Registro:
    def __init__(self, base: Path, enc):
        self.base = Path(base)
        self.enc = enc
        self.eventos: list[tuple[str, dict, str]] = []

    def fold(self, ident: str):
        return self.enc if ident == self.enc.id else None

    def anotar(self, ident: str, evento: dict, ukey: str) -> str:
        self.eventos.append((ident, evento, ukey))
        return "nuevo"


def _encargo_real(fases, activa: str) -> Encargo:
    return Encargo(
        id="enc_x",
        clave="clave-x",
        huella="huella",
        director="cli",
        texto="pedido",
        adjuntos=(),
        restricciones=(),
        canal="canal",
        entrega=EntregaAutorizada(nivel="pr", repo="o/r", rama="encargo/enc_x", base="main"),
        fases=tuple(fases),
        fase_activa=activa,
        estado="ejecutando",
        generacion=1,
    )


class _TraerMain(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.bare = self.raiz / "repo.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", "--quiet", str(self.bare)], check=True)
        semilla = self.raiz / "semilla"
        semilla.mkdir()
        (semilla / "base.txt").write_text("base\n")
        _git(semilla, "init", "-b", "main", "--quiet")
        _git(semilla, "add", "-A")
        _git(semilla, "commit", "--quiet", "-m", "base")
        _git(semilla, "push", "--quiet", str(self.bare), "main")

        self.base = self.raiz / "registro"
        self.espacio = self.base / "espacio" / "enc_x"
        subprocess.run(["git", "clone", "--quiet", str(self.bare), str(self.espacio)], check=True)
        _git(self.espacio, "checkout", "-B", "encargo/enc_x")
        (self.espacio / "base.txt").write_text("trabajo del encargo\n")
        _git(self.espacio, "commit", "-am", "producto")
        _git(self.espacio, "push", "--quiet", "origin", "HEAD:refs/heads/encargo/enc_x")
        self.antes = _git(self.espacio, "rev-parse", "HEAD").strip()

        self.enc = SimpleNamespace(
            id="enc_x",
            clave="clave-x",
            entrega=SimpleNamespace(base="main", rama="encargo/enc_x"),
            fase_actual=None,
            fases=(),
            proc=None,
            reserva=False,
            estado="ejecutando",
            generacion=1,
        )
        self.reg = _Registro(self.base, self.enc)

    def _avanzar_main(self, archivo: str, texto: str) -> str:
        otro = self.raiz / "otro"
        if not otro.exists():
            subprocess.run(["git", "clone", "--quiet", str(self.bare), str(otro)], check=True)
        (otro / archivo).write_text(texto)
        _git(otro, "add", "-A")
        _git(otro, "commit", "--quiet", "-m", f"main cambia {archivo}")
        _git(otro, "push", "--quiet", "origin", "main")
        return _git(otro, "rev-parse", "HEAD").strip()

    def _rama_remota(self, rama: str = "encargo/enc_x") -> str:
        salida = subprocess.run(
            ["git", "--git-dir", str(self.bare), "rev-parse", f"refs/heads/{rama}"],
            capture_output=True, text=True,
        )
        return salida.stdout.strip() if salida.returncode == 0 else ""

    def _notas(self) -> list[str]:
        return [
            evento["data"]["nota"]
            for _, evento, _ in self.reg.eventos
            if evento.get("kind") == "procedimiento"
        ]

    def test_mezcla_limpia_se_publica_y_deja_evento(self):
        base_sha = self._avanzar_main("main.txt", "desde main\n")

        r = traer_main(self.reg, "enc_x", HORA)

        self.assertEqual(r["estado"], "publicado", r)
        self.assertEqual(r["base_sha"], base_sha)
        self.assertEqual(r["archivos"], [])
        self.assertEqual(self._rama_remota(), r["sha"])
        self.assertEqual(
            _git(self.espacio, "show", "encargo/enc_x:main.txt"), "desde main\n"
        )
        padres = _git(self.espacio, "rev-list", "--parents", "-n1", r["sha"]).split()
        self.assertEqual(len(padres), 3, "la mezcla deja un commit con dos padres")
        self.assertTrue(any("mezcla limpia" in n and base_sha[:12] in n for n in self._notas()))

    def test_encargo_real_con_varias_fases_publica_en_la_rama_de_la_fase(self):
        self.reg.enc = _encargo_real(
            [
                Fase(id="f1", resultado="uno", estado="pendiente"),
                Fase(id="f2", resultado="dos", estado="trabajo"),
            ],
            "f2",
        )
        _git(self.espacio, "checkout", "-B", "encargo/enc_x-f2")
        _git(self.espacio, "push", "--quiet", "origin", "HEAD:refs/heads/encargo/enc_x-f2")
        antes_f2 = _git(self.espacio, "rev-parse", "HEAD").strip()
        self._avanzar_main("main.txt", "desde main\n")

        r = traer_main(self.reg, "enc_x", HORA)

        self.assertEqual(r["estado"], "publicado", r)
        self.assertEqual(r["rama"], "encargo/enc_x-f2")
        self.assertEqual(self._rama_remota("encargo/enc_x-f2"), r["sha"])
        self.assertNotEqual(self._rama_remota("encargo/enc_x-f2"), antes_f2)
        self.assertEqual(
            self._rama_remota("encargo/enc_x"), self.antes,
            "la rama sin fase no se toca",
        )
        self.assertEqual(
            _git(self.espacio, "show", "encargo/enc_x-f2:main.txt"), "desde main\n"
        )

    def test_no_publica_si_el_espacio_no_esta_en_la_rama_de_la_fase(self):
        self.reg.enc = _encargo_real(
            [
                Fase(id="f1", resultado="uno", estado="pendiente"),
                Fase(id="f2", resultado="dos", estado="trabajo"),
            ],
            "f2",
        )

        r = traer_main(self.reg, "enc_x", HORA)

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("encargo/enc_x-f2", r["motivo"])
        self.assertIn("encargo/enc_x", r["motivo"])
        self.assertEqual(self._rama_remota("encargo/enc_x-f2"), "")
        self.assertEqual(self._rama_remota("encargo/enc_x"), self.antes)

    def test_choque_deja_los_conflictos_y_vuelve_al_responsable(self):
        self._avanzar_main("base.txt", "main cambia\n")

        r = traer_main(self.reg, "enc_x", HORA)

        self.assertEqual(r["estado"], "conflicto", r)
        self.assertIn("base.txt", r["archivos"])
        self.assertTrue(r["conflicto"])
        self.assertEqual(self._rama_remota(), self.antes, "el choque no se publica")
        self.assertEqual(
            _git(self.espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").strip(),
            r["base_sha"],
        )
        self.assertIn("<<<<<<<", (self.espacio / "base.txt").read_text())
        self.assertTrue(any("conflicto en base.txt" in n for n in self._notas()))
        estrategias = [
            evento["data"] for _, evento, _ in self.reg.eventos
            if evento.get("kind") == "estrategia"
        ]
        self.assertEqual(estrategias[-1]["rol"], "responsable")
        self.assertEqual(estrategias[-1]["estrategia"], "integrar_main")

    def test_tras_resolver_el_conflicto_se_publica(self):
        self._avanzar_main("base.txt", "main cambia\n")
        traer_main(self.reg, "enc_x", HORA)
        (self.espacio / "base.txt").write_text("resuelto por el responsable\n")
        _git(self.espacio, "add", "base.txt")
        _git(self.espacio, "commit", "--quiet", "-m", "resolver conflicto")

        r = traer_main(self.reg, "enc_x", HORA)

        self.assertEqual(r["estado"], "publicado", r)
        self.assertEqual(self._rama_remota(), r["sha"])
        self.assertEqual(
            _git(self.espacio, "show", "encargo/enc_x:base.txt"),
            "resuelto por el responsable\n",
        )

    def test_la_entrega_no_commitea_una_mezcla_en_curso(self):
        self._avanzar_main("base.txt", "main cambia\n")
        traer_main(self.reg, "enc_x", HORA)
        self.enc.ajustes = ()
        self.enc.ajustes_vistos = 0
        self.enc.nivel_alcanzado = "ninguno"
        self.enc.revisado = None
        self.enc.entrega.nivel = "pr"

        with mock.patch.object(
            ciclo, "_commit_push", side_effect=AssertionError("no debe entregar")
        ):
            ciclo._paso_entrega(self.reg, self.enc, object(), HORA)

        self.assertEqual(self._rama_remota(), self.antes)
        estrategias = [
            evento["data"] for _, evento, _ in self.reg.eventos
            if evento.get("kind") == "estrategia"
        ]
        self.assertEqual(estrategias[-1]["estrategia"], "integrar_main")
        self.assertIn("mezcla en curso", estrategias[-1]["motivo"])

    def test_toma_el_turno_antes_de_tocar_el_espacio(self):
        empezado = threading.Event()
        liberar = threading.Event()

        def retener():
            with ciclo.turno(self.base):
                empezado.set()
                liberar.wait(3)

        hilo = threading.Thread(target=retener)
        hilo.start()
        self.assertTrue(empezado.wait(2), "el test no consiguió el turno")
        resultado = []
        trabajador = threading.Thread(
            target=lambda: resultado.append(traer_main(self.reg, "enc_x", HORA))
        )
        trabajador.start()
        time.sleep(0.3)
        self.assertTrue(trabajador.is_alive(), "traer-main debe esperar el turno")
        liberar.set()
        trabajador.join(5)
        hilo.join(5)

        self.assertFalse(trabajador.is_alive())
        self.assertEqual(resultado[0]["estado"], "al-dia", resultado[0])

    def test_incorporar_entradas_no_mezcla_con_merge_en_curso(self):
        self._avanzar_main("base.txt", "main cambia\n")
        traer_main(self.reg, "enc_x", HORA)
        merge_head = _git(
            self.espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"
        ).strip()
        self.assertTrue(merge_head)
        dep = Fase(
            id="f1", resultado="uno", estado="pendiente",
            producto=VersionProducto(firma="v1", manifiesto=(), sha_git=self.antes),
        )
        fase = Fase(
            id="f2", resultado="dos", estado="trabajo",
            requiere=(("f1", "v0"),),
        )

        causa = ciclo._incorporar_entradas(_encargo_real([dep, fase], "f2"), self.espacio)

        self.assertEqual(causa, "")
        self.assertEqual(
            _git(self.espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").strip(),
            merge_head,
            "la guarda no deja que otra mezcla aborte la que está en curso",
        )
        self.assertIn("<<<<<<<", (self.espacio / "base.txt").read_text())

    def test_sin_agente_en_curso(self):
        self.enc.proc = SimpleNamespace(pid=123, inicio="t0")

        r = traer_main(self.reg, "enc_x", HORA)

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("agente en curso", r["motivo"])
        self.assertEqual(self.reg.eventos, [])
        self.assertEqual(self._rama_remota(), self.antes)

    def test_si_ya_esta_al_dia_no_mezcla(self):
        r = traer_main(self.reg, "enc_x", HORA)

        self.assertEqual(r["estado"], "al-dia", r)
        self.assertEqual(r["sha"], self.antes)
        self.assertEqual(_git(self.espacio, "rev-parse", "HEAD").strip(), self.antes)
        self.assertTrue(any("ya estaba al día" in n for n in self._notas()))


class _VerboTraerMain(unittest.TestCase):
    def test_el_verbo_llega_al_motor(self):
        with tempfile.TemporaryDirectory() as tmp:
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                rc = motor_main(["traer-main", "no-existe", "--base", tmp])

            doc = json.loads(salida.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(doc["estado"], "error")
            self.assertIn("no existe el encargo", doc["motivo"])
