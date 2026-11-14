"""Una réplica aplica solo publicaciones más nuevas que la que ya tiene."""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from motor.flota import cli, compartida
from motor.flota.compartida import (
    _json_bytes,
    _sha256,
    exportar,
    importar,
    leer_fuente,
)
from motor.flota.manifiesto import Herramienta, Politica, Puesto, guardar
from ops import flota_ciclo

GROK_CANAL = "https://registry.npmjs.org/%40xai-official%2Fgrok/latest"


def _politica(generacion: str, herramientas: tuple[Herramienta, ...] = ()) -> Politica:
    return Politica(
        generacion,
        (Puesto("director", (("openai/gpt-5.4", "high"),)),),
        (),
        herramientas,
        (),
        None,
        (),
    )


def _grok(version: str, letra: str) -> dict:
    return {
        "nombre": "grok",
        "tipo": "npm",
        "paquete": "@xai-official/grok",
        "version": version,
        "fuente": GROK_CANAL,
        "tarball": f"https://registry.npmjs.org/@xai-official/grok/-/grok-{version}.tgz",
        "integridad": "sha512-" + letra * 88,
    }


def _archivos(home: Path) -> dict[str, bytes]:
    return {
        p.relative_to(home).as_posix(): p.read_bytes()
        for p in sorted(home.rglob("*"))
        if p.is_file()
    }


def _leer(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _confirmar_routing(repo: Path, thinking: bool) -> None:
    (repo / "claw-routing.json").write_text(
        json.dumps({"agents": {"main": {"row": "director", "thinking": thinking}}})
        + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", "claw-routing.json")
    _git(repo, "commit", "-qm", "routing")


class PublicacionesDeLaFlota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="flota-publicacion-")
        self.base = Path(self.tmp.name)
        self.publicador = self.base / "publicador"
        self.replica = self.base / "replica"
        self.origen = self.base / "motor"
        self.origen.mkdir()
        self.salidas = 0

    def tearDown(self):
        self.tmp.cleanup()

    def _publicar(
        self,
        politica: Politica,
        repo_hermes: Path | None = None,
        home: Path | None = None,
    ) -> Path:
        home = home or self.publicador
        guardar(home / "manifiesto.toml", politica)
        self.salidas += 1
        salida = self.base / f"snapshot-{self.salidas}.json"
        exportar(home, salida, repo_hermes=repo_hermes, motor_origen=self.origen)
        return salida

    def _publicar_grok(self, version: str, letra: str) -> Path:
        componente = _grok(version, letra)
        runtime = self.publicador / "actual" / "runtime.json"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_text(json.dumps({"componentes": [componente]}), encoding="utf-8")
        herramienta = Herramienta(
            "grok", componente["fuente"], version, componente["integridad"]
        )
        return self._publicar(_politica("v8", (herramienta,)))

    def _repo_hermes(self, thinking: bool) -> Path:
        repo = self.base / "goncloud-hermes"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "ensayo@localhost")
        _git(repo, "config", "user.name", "Ensayo")
        _confirmar_routing(repo, thinking)
        return repo

    def _como(self, snapshot: Path, version: int) -> Path:
        """El mismo contenido tal como lo escribe un publicador sin actualizar."""
        doc = _leer(snapshot)
        doc.pop("publicacion")
        doc.pop("snapshot_sha256")
        if version == 1:
            doc.pop("claw_routing")
        doc["schema"] = f"goncloud.flota.compartida.v{version}"
        doc["snapshot_sha256"] = _sha256(_json_bytes(doc))
        salida = self.base / f"snapshot-v{version}.json"
        salida.write_bytes(_json_bytes(doc))
        return salida

    def _con_numero(self) -> tuple[dict[str, bytes], Path]:
        importar(self.replica, self._publicar(_politica("v8")))
        return _archivos(self.replica), self.replica / "fuente-compartida.json"

    def _ciclo_replica(self, snapshot: Path) -> tuple[int, str]:
        error = io.StringIO()
        with (
            contextlib.redirect_stderr(error),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = flota_ciclo.main(
                ["--home", str(self.replica), "--replica", str(snapshot), "--json"]
            )
        return rc, error.getvalue()

    def test_un_snapshot_anterior_se_rechaza_sin_escribir_en_la_replica(self):
        primera = self._publicar(_politica("v8"))
        segunda = self._publicar(_politica("v7"))
        importar(self.replica, segunda)
        antes = _archivos(self.replica)

        with self.assertRaises(compartida.PublicacionRechazada):
            importar(self.replica, primera)
        self.assertEqual(_archivos(self.replica), antes)

        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            rc = flota_ciclo.main(
                ["--home", str(self.replica), "--replica", str(primera), "--json"]
            )
        self.assertEqual(rc, 1)
        self.assertEqual(_archivos(self.replica), antes)
        self.assertRegex(error.getvalue(), r"\b1\b")
        self.assertRegex(error.getvalue(), r"\b2\b")

    def test_el_mismo_snapshot_no_reescribe_la_replica(self):
        snapshot = self._publicar(_politica("v8"))
        importar(self.replica, snapshot)
        fuente = self.replica / "fuente-compartida.json"
        antes, sello = _archivos(self.replica), fuente.stat().st_mtime_ns

        resultado = importar(self.replica, snapshot)

        self.assertEqual(resultado["accion"], "sin_cambios")
        self.assertEqual(resultado["publicacion"], 1)
        self.assertEqual(_archivos(self.replica), antes)
        self.assertEqual(fuente.stat().st_mtime_ns, sello)

    def test_el_mismo_numero_con_otro_contenido_se_rechaza_y_dice_como_seguir(self):
        repo = self._repo_hermes(thinking=False)
        importar(self.replica, self._publicar(_politica("v8"), repo_hermes=repo))
        sin_contador = self.base / "publicador-sin-home"
        otra = self._publicar(_politica("v9"), repo_hermes=repo, home=sin_contador)
        self.assertEqual(_leer(otra)["publicacion"], 1)
        antes = _archivos(self.replica)

        with self.assertRaises(compartida.PublicacionRechazada) as rechazo:
            importar(self.replica, otra)
        self.assertEqual(_archivos(self.replica), antes)

        desde = re.search(r"--desde (\d+)", str(rechazo.exception))
        assert desde is not None, str(rechazo.exception)
        renumerada = self.base / "renumerada.json"
        orden = [
            "publicar",
            "--home",
            str(sin_contador),
            "--salida",
            str(renumerada),
            "--repo-hermes",
            str(repo),
            "--desde",
            desde.group(1),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(orden), 0)
        self.assertEqual(importar(self.replica, renumerada)["accion"], "sincronizada")
        self.assertEqual(
            _leer(self.replica / "fuente-compartida.json")["generacion"], "v9"
        )
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(orden), 1, "--desde no baja ni repite el numero")

    def test_un_numero_mayor_se_aplica_aunque_la_generacion_parezca_vieja(self):
        primera = self._publicar(_politica("v8"))
        segunda = self._publicar(_politica("v7"))
        self.assertEqual(importar(self.replica, primera)["publicacion"], 1)

        resultado = importar(self.replica, segunda)

        self.assertEqual(
            (resultado["accion"], resultado["publicacion"]), ("sincronizada", 2)
        )
        self.assertEqual(
            _leer(self.replica / "fuente-compartida.json")["generacion"], "v7"
        )

    def test_la_replica_deja_los_snapshots_sin_numero_en_cuanto_tiene_uno(self):
        repo = self._repo_hermes(thinking=False)
        v3 = self._publicar(_politica("v8"), repo_hermes=repo)
        v2 = self._como(v3, 2)
        self.assertEqual(importar(self.replica, v2)["accion"], "sincronizada")
        self.assertEqual(importar(self.replica, v3)["accion"], "sincronizada")
        antes = _archivos(self.replica)

        with self.assertRaises(compartida.PublicacionRechazada):
            importar(self.replica, v2)
        self.assertEqual(_archivos(self.replica), antes)

    def test_un_v1_entra_mientras_la_replica_no_tiene_numero(self):
        v3 = self._publicar(_politica("v8"))
        v1 = self._como(v3, 1)

        resultado = importar(self.replica, v1)

        self.assertEqual(resultado["accion"], "sincronizada")
        self.assertEqual(
            resultado["claw_routing"],
            {"incluido": False, "motivo": "el snapshot no lleva claw-routing"},
        )
        fuente = leer_fuente(self.replica)
        assert fuente is not None
        self.assertEqual(fuente["schema"], "goncloud.flota.compartida.v1")
        self.assertEqual(importar(self.replica, v3)["accion"], "sincronizada")
        antes = _archivos(self.replica)

        with self.assertRaises(compartida.PublicacionRechazada):
            importar(self.replica, v1)
        self.assertEqual(_archivos(self.replica), antes)

    def test_un_v3_sin_checkout_dice_que_el_publicador_no_publico_routing(self):
        resultado = importar(self.replica, self._publicar(_politica("v8")))

        self.assertFalse(resultado["claw_routing"]["incluido"])
        self.assertIn("no hubo checkout", resultado["claw_routing"]["motivo"])

    def test_un_schema_que_no_es_texto_es_un_snapshot_invalido(self):  # no-roja-en-base: el TypeError lo introdujo la tabla _EXTRA de este PR; la base comparaba con == y ya daba SnapshotInvalido
        antes, _ = self._con_numero()
        for schema in (["goncloud.flota.compartida.v3"], {"v": 3}):
            with self.subTest(schema=schema):
                doc = _leer(self._publicar(_politica("v9")))
                doc["schema"] = schema
                roto = self.base / "schema-roto.json"
                roto.write_text(json.dumps(doc), encoding="utf-8")

                with self.assertRaises(compartida.SnapshotInvalido):
                    importar(self.replica, roto)
                self.assertEqual(_archivos(self.replica), antes)

    def test_una_fuente_vigente_ilegible_se_diagnostica_sin_escribir(self):
        corruptas = {
            "json roto": b"{roto",
            "vacia": b"",
            "lista": b"[]",
            "sin hash ni origen": b'{"publicacion": 1}',
            "numero de texto": b'{"publicacion": "1", "hash": "x", "origen": "pc"}',
            "numero nulo": b'{"publicacion": null, "hash": "x", "origen": "pc"}',
        }
        _, fuente = self._con_numero()
        nueva = self._publicar(_politica("v9"))
        for caso, contenido in corruptas.items():
            with self.subTest(caso=caso):
                fuente.write_bytes(contenido)
                antes = _archivos(self.replica)

                with self.assertRaises(compartida.SnapshotInvalido) as rechazo:
                    importar(self.replica, nueva)
                self.assertIn(str(fuente), str(rechazo.exception))
                self.assertEqual(_archivos(self.replica), antes)

                rc, error = self._ciclo_replica(nueva)
                self.assertEqual(rc, 1)
                self.assertIn(str(fuente), error)
                self.assertNotIn("Traceback", error)
                self.assertEqual(_archivos(self.replica), antes)

    def test_un_contador_ilegible_se_diagnostica_y_desde_lo_resuelve(self):
        repo = self._repo_hermes(thinking=False)
        self._publicar(_politica("v8"), repo_hermes=repo)
        contador = self.publicador / "ultima-publicacion.json"
        corruptos = {"json roto": b"{roto", "vacio": b"{}", "lista": b"[]"}
        for indice, (caso, contenido) in enumerate(corruptos.items()):
            with self.subTest(caso=caso):
                contador.write_bytes(contenido)
                salida = self.base / f"contador-{indice}.json"
                orden = [
                    "publicar",
                    "--home",
                    str(self.publicador),
                    "--salida",
                    str(salida),
                    "--repo-hermes",
                    str(repo),
                ]
                error = io.StringIO()
                with (
                    contextlib.redirect_stderr(error),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    rc = cli.main(orden)

                self.assertEqual(rc, 1)
                self.assertIn(str(contador), error.getvalue())
                self.assertIn("--desde", error.getvalue())
                self.assertFalse(salida.exists())
                self.assertEqual(contador.read_bytes(), contenido)

                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main([*orden, "--desde", "5"]), 0)
                self.assertEqual(_leer(salida)["publicacion"], 5)

    def test_una_fuente_que_ya_no_valida_sigue_ordenando_y_la_cura_la_siguiente(self):
        primera = self._publicar(_politica("v7"))
        segunda = self._publicar(_politica("v8"))
        tercera = self._publicar(_politica("v9"))
        importar(self.replica, segunda)
        fuente = self.replica / "fuente-compartida.json"
        doc = _leer(fuente)
        doc["generacion"] = "lo que una version nueva del motor ya no acepta"
        fuente.write_bytes(_json_bytes(doc))

        with self.assertRaises(compartida.PublicacionRechazada):
            importar(self.replica, primera)
        self.assertEqual(importar(self.replica, tercera)["accion"], "sincronizada")
        fuente_curada = leer_fuente(self.replica)
        assert fuente_curada is not None
        self.assertEqual(fuente_curada["generacion"], "v9")

    def test_un_routing_nuevo_sube_el_numero_y_la_replica_lo_aplica(self):
        repo = self._repo_hermes(thinking=False)
        primera = self._publicar(_politica("v8"), repo_hermes=repo)
        repetida = self._publicar(_politica("v8"), repo_hermes=repo)
        _confirmar_routing(repo, thinking=True)
        nueva = self._publicar(_politica("v8"), repo_hermes=repo)

        self.assertEqual(primera.read_bytes(), repetida.read_bytes())
        self.assertEqual(
            [_leer(p)["publicacion"] for p in (primera, repetida, nueva)], [1, 1, 2]
        )
        importar(self.replica, primera)
        self.assertEqual(importar(self.replica, nueva)["accion"], "sincronizada")
        routing = _leer(self.replica / "fuente-compartida.json")["claw_routing"]
        self.assertIs(json.loads(routing["contenido"])["agents"]["main"]["thinking"], True)

    def test_otra_version_de_herramienta_sube_el_numero_y_la_replica_la_aplica(self):
        primera = self._publicar_grok("v2", "A")
        nueva = self._publicar_grok("v3", "C")

        self.assertEqual([_leer(p)["publicacion"] for p in (primera, nueva)], [1, 2])
        importar(self.replica, primera)
        self.assertEqual(importar(self.replica, nueva)["accion"], "sincronizada")
        catalogo = _leer(self.replica / "fuente-compartida.json")["catalogo"]
        self.assertEqual([c["version"] for c in catalogo["componentes"]], ["v3"])


if __name__ == "__main__":
    unittest.main()
