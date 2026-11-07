"""La entrega no se queda girando: la firma ignora lo que git ignora, el paso
sin efecto vuelve a lanzar, el commit se religa y una observacion en rojo no se
publica sin revisar."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import (
    _aplica_entrega,
    _incorporar_responsable,
    _paso_entrega,
    ahora_iso,
    barrer,
)
from motor.encargos.comprobacion import version_producto
from tests.test_flujo_revisiones import Recorrido, _entrega_pr
from tests.dobles import github_con_hallazgos

_EXCLUIDAS_LEGADAS = (".git", "__pycache__", ".runtime", ".interno")


def setUpModule():
    """Estas pruebas no ejercitan la jaula: el flujo de entrega no debe frenarse
    por la sonda del entorno del host que corre la bateria."""
    parche = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    parche.start()
    globals()["_PARCHE_SONDA"] = parche


def tearDownModule():
    parche = globals().pop("_PARCHE_SONDA", None)
    if parche is not None:
        parche.stop()


def _git(espacio: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(espacio), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _firma_legada(espacio: Path) -> str:
    """La firma anterior a este cambio: hashea tambien lo que git ignora."""
    entradas = []
    for path in sorted(espacio.rglob("*")):
        relativo = path.relative_to(espacio)
        if any(p in _EXCLUIDAS_LEGADAS for p in relativo.parts):
            continue
        if path.is_file():
            entradas.append(
                (relativo.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
            )
    h = hashlib.sha256()
    for ruta, huella in entradas:
        h.update(ruta.encode())
        h.update(b"\0")
        h.update(huella.encode())
        h.update(b"\0")
    return h.hexdigest()


def _cache_de_pytest(espacio: Path, lastfailed: str = "{}") -> None:
    cache = espacio / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (espacio / ".pytest_cache" / ".gitignore").write_text("*\n", encoding="utf-8")
    (cache / "lastfailed").write_text(lastfailed, encoding="utf-8")


def _lanzador(roles: list[str]):
    def lanzar(enc, prompt, espacio, sesion):
        roles.append(enc.rol)
        return ["agente-falso"]

    return lanzar


def _barrido(r, gh, lanzador, t: float | None = None):
    t = time.time() if t is None else t
    with mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    ), mock.patch(
        "motor.flota.runtime.exigir_poteto",
        return_value=(Path(r.base) / "poteto.md", "digest"),
    ), mock.patch(
        "motor.encargos.ciclo.preparar_administrado"
    ), mock.patch(
        "motor.encargos.ciclo.lanzar_administrado", return_value={}
    ), mock.patch("motor.encargos.ciclo.observar", return_value="ausente"):
        barrer(r.reg, ahora_iso(t), t, lanzador, gh=gh)
    return r.fold()


class FirmaDelProducto(unittest.TestCase):
    def test_lo_que_git_ignora_no_cambia_la_firma(self):
        with tempfile.TemporaryDirectory() as tmp:
            espacio = Path(tmp) / "espacio"
            espacio.mkdir()
            (espacio / "producto.txt").write_text("hola\n", encoding="utf-8")
            _git(espacio, "init", "-b", "main", "--quiet")
            _git(espacio, "add", "-A")
            _git(
                espacio, "-c", "user.name=t", "-c", "user.email=t@l",
                "commit", "--quiet", "-m", "base",
            )
            antes = version_producto(espacio).firma

            _cache_de_pytest(espacio)
            self.assertEqual(version_producto(espacio).firma, antes)

            _cache_de_pytest(espacio, '{"tests/test_x.py::test_y": true}')
            self.assertEqual(version_producto(espacio).firma, antes)

            (espacio / "nuevo.txt").write_text("nuevo\n", encoding="utf-8")
            self.assertNotEqual(version_producto(espacio).firma, antes)
            (espacio / "nuevo.txt").unlink()

            (espacio / "producto.txt").write_text("otra\n", encoding="utf-8")
            self.assertNotEqual(version_producto(espacio).firma, antes)

    def test_su_propio_gitignore_sigue_ocultando_lo_ignorado(self):
        with tempfile.TemporaryDirectory() as tmp:
            espacio = Path(tmp) / "espacio"
            espacio.mkdir()
            (espacio / ".gitignore").write_text("ignorado.txt\n", encoding="utf-8")
            (espacio / "producto.txt").write_text("hola\n", encoding="utf-8")
            _git(espacio, "init", "-b", "main", "--quiet")
            _git(espacio, "add", "-A")
            _git(
                espacio, "-c", "user.name=t", "-c", "user.email=t@l",
                "commit", "--quiet", "-m", "base",
            )
            antes = version_producto(espacio)
            self.assertEqual(
                [ruta for ruta, _ in antes.manifiesto],
                [".gitignore", "producto.txt"],
            )

            (espacio / "ignorado.txt").write_text("ruido\n", encoding="utf-8")
            self.assertEqual(version_producto(espacio).firma, antes.firma)

            (espacio / "producto.txt").write_text("otra\n", encoding="utf-8")
            self.assertNotEqual(version_producto(espacio).firma, antes.firma)

    def test_un_espacio_dentro_de_un_repo_ajeno_no_hereda_su_ignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            padre = Path(tmp) / "padre"
            padre.mkdir()
            _git(padre, "init", "-b", "main", "--quiet")
            (padre / ".gitignore").write_text("espacio/\n", encoding="utf-8")
            espacio = padre / "espacio"
            espacio.mkdir()
            (espacio / "producto.txt").write_text("hola\n", encoding="utf-8")

            primera = version_producto(espacio)
            self.assertEqual(
                [ruta for ruta, _ in primera.manifiesto], ["producto.txt"]
            )

            (espacio / "producto.txt").write_text("otra\n", encoding="utf-8")
            self.assertNotEqual(version_producto(espacio).firma, primera.firma)

    def test_un_espacio_dentro_de_un_repo_ajeno_no_cuenta_sus_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            padre = Path(tmp) / "padre"
            padre.mkdir()
            _git(padre, "init", "-b", "main", "--quiet")
            espacio = padre / "espacio"
            espacio.mkdir()
            (espacio / "producto.txt").write_text("hola\n", encoding="utf-8")

            antes = version_producto(espacio)
            self.assertEqual(
                [ruta for ruta, _ in antes.manifiesto], ["producto.txt"]
            )

            _cache_de_pytest(espacio)
            despues = version_producto(espacio)
            self.assertEqual(
                [ruta for ruta, _ in despues.manifiesto], ["producto.txt"]
            )
            self.assertEqual(despues.firma, antes.firma)

            (espacio / "producto.txt").write_text("otra\n", encoding="utf-8")
            self.assertNotEqual(version_producto(espacio).firma, antes.firma)

    def test_un_espacio_ignorado_por_su_repo_de_arriba_no_cuenta_sus_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            padre = Path(tmp) / "padre"
            padre.mkdir()
            _git(padre, "init", "-b", "main", "--quiet")
            (padre / ".gitignore").write_text("espacio/\n", encoding="utf-8")
            espacio = padre / "espacio"
            espacio.mkdir()
            (espacio / "producto.txt").write_text("hola\n", encoding="utf-8")

            antes = version_producto(espacio)
            self.assertEqual(
                [ruta for ruta, _ in antes.manifiesto], ["producto.txt"]
            )

            _cache_de_pytest(espacio)
            despues = version_producto(espacio)
            self.assertEqual(
                [ruta for ruta, _ in despues.manifiesto], ["producto.txt"]
            )
            self.assertEqual(despues.firma, antes.firma)

            (espacio / "producto.txt").write_text("otra\n", encoding="utf-8")
            self.assertNotEqual(version_producto(espacio).firma, antes.firma)


class EntregaSinEfecto(unittest.TestCase):
    def _con_commit_sin_sha(self, tmp: str, clave: str):
        vuelta, r = _entrega_pr(self, tmp, clave, github_con_hallazgos)
        e = vuelta("uno")
        commit = e.producto.sha_git
        self.assertTrue(commit)
        self.assertEqual(e.deepseek_sha, commit)
        self.assertEqual(_git(r.espacio, "rev-parse", "HEAD").stdout.strip(), commit)

        _cache_de_pytest(r.espacio, '{"tests/test_x.py::test_y": true}')
        self.assertEqual(_git(r.espacio, "status", "--porcelain").stdout.strip(), "")
        legado = _firma_legada(r.espacio)
        self.assertNotEqual(legado, e.producto.firma)
        r.reg.anotar(
            r.enc,
            {
                "kind": "producto_listo",
                "data": {
                    "version": legado,
                    "manifiesto": [list(x) for x in e.producto.manifiesto],
                    "sha_git": "",
                    "fase": e.fase_activa,
                    "intento": e.intento,
                    "hora": ahora_iso(),
                },
            },
            f"t-producto-legado:{legado}",
        )
        e = r.fold()
        self.assertEqual(e.producto.sha_git, "")
        self.assertTrue(_aplica_entrega(e))
        return vuelta, r, commit, legado

    def test_dos_barridos_con_el_paso_sin_efecto_lanzan_al_responsable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, commit, _legado = self._con_commit_sin_sha(tmp, "efecto")
            antes = len(
                [
                    ev
                    for ev in r.reg.eventos(r.enc)
                    if ev["kind"] == "reserva"
                ]
            )
            roles: list[str] = []
            for _ in range(2):
                _barrido(r, vuelta.gh, _lanzador(roles))
            reservas = [
                ev
                for ev in r.reg.eventos(r.enc)[antes:]
                if ev["kind"] == "reserva"
                and ev["data"].get("rol") == "responsable"
            ]
            self.assertTrue(reservas, "el paso sin efecto no puede frenar el lanzamiento")
            self.assertIn("responsable", roles)
            self.assertEqual(
                r.fold().producto.sha_git,
                commit,
                "el producto nuevo sobre el mismo commit recupera su sha_git",
            )

    def test_un_paso_sin_efecto_no_deja_lanzar_falso_indefinidamente(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, _commit, _legado = self._con_commit_sin_sha(tmp, "red")
            antes = len(
                [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "reserva"]
            )
            roles: list[str] = []

            def paso_sin_efecto(registro, enc, gh, hora):
                return enc

            with mock.patch(
                "motor.encargos.ciclo._paso_entrega", side_effect=paso_sin_efecto
            ):
                for _ in range(3):
                    _barrido(r, vuelta.gh, _lanzador(roles))

            reservas = [
                ev
                for ev in r.reg.eventos(r.enc)[antes:]
                if ev["kind"] == "reserva"
                and ev["data"].get("rol") == "responsable"
            ]
            self.assertTrue(
                reservas,
                "una entrega sin efecto no puede dejar lanzar=False para siempre",
            )
            self.assertEqual(roles, ["responsable"])

    def test_la_ukey_del_commit_incluye_la_firma_del_producto(self):  # no-roja-en-base: solo cambió el doble de GitHub; el comportamiento probado ya estaba en la base
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ukey", github_con_hallazgos)
            e = vuelta("uno")
            commit = e.producto.sha_git
            ukey = (
                f"entrega:{r.enc}:{e.fase_activa}:commit:{commit}:{e.producto.firma}"
            )
            self.assertIsNotNone(
                r.reg.por_ukey(ukey),
                "la firma del producto en la ukey religa el commit al producto nuevo",
            )


class PublicacionConObservacion(unittest.TestCase):
    def _producto_aprobado(self, tmp: str, clave: str):
        vuelta, r = _entrega_pr(self, tmp, clave, github_con_hallazgos)
        (r.espacio / "saludo.txt").write_text("hola rojo\n", encoding="utf-8")
        r.incorporar()
        r.juicio(
            {
                "veredicto": "aprobado",
                "evidencia": "lei saludo.txt; contiene hola rojo",
            }
        )
        r.incorporar()
        e = r.fold()
        self.assertTrue(_aplica_entrega(e))
        return vuelta, r, e

    def _comprobaciones(self, argv: list[str]) -> list[dict]:
        return [{"objetivo": "saludo con el texto pedido", "argv": argv}]

    def _declarar_comprobacion(self, r, argv: list[str] | None = None) -> list[dict]:
        comandos = self._comprobaciones(
            argv or ["python3", "-c", "import sys; sys.exit(1)"]
        )
        interno = r.espacio / ".interno"
        interno.mkdir(exist_ok=True)
        (interno / "comprobaciones.json").write_text(
            json.dumps({"comprobaciones": comandos}),
            encoding="utf-8",
        )
        return comandos

    def _escribir_observacion(self, r, version: str, declaradas: list[dict], rc: int):
        (r.d / "observaciones.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "producto": version,
                    "comprobaciones": declaradas,
                    "ejecuciones": [
                        {
                            **declaradas[0],
                            "rc": rc,
                            "stdout": "",
                            "stderr": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_sin_observacion_la_version_no_se_publica(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._producto_aprobado(tmp, "obs-pendiente")
            self._declarar_comprobacion(r)
            antes = _git(r.espacio, "rev-parse", "HEAD").stdout.strip()
            eventos_antes = len(r.reg.eventos(r.enc))

            with mock.patch("motor.encargos.ciclo.iniciar_comprobacion") as iniciar:
                _paso_entrega(r.reg, e, vuelta.gh, ahora_iso())
            iniciar.assert_called_once()

            despues = _git(r.espacio, "rev-parse", "HEAD").stdout.strip()
            nuevos = r.reg.eventos(r.enc)[eventos_antes:]
            self.assertEqual(despues, antes)
            self.assertFalse(
                any(
                    ev["kind"] == "entrega_externa"
                    and ev["data"].get("paso") in ("commit", "push")
                    for ev in nuevos
                )
            )
            self.assertTrue(
                any(
                    ev["kind"] == "espera"
                    and "observacion del comprobador" in ev["data"].get("motivo", "")
                    for ev in nuevos
                )
            )

    def test_una_observacion_en_rojo_no_publica_la_version(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._producto_aprobado(tmp, "obs-rojo")
            declaradas = self._declarar_comprobacion(r)
            r.reg.anotar(
                r.enc,
                {
                    "kind": "observacion_supervisor",
                    "data": {
                        "version": e.producto.firma,
                        "comprobaciones": declaradas,
                        "ejecuciones": [
                            {
                                **declaradas[0],
                                "rc": 1,
                                "stdout": "",
                                "stderr": "fallo",
                            }
                        ],
                        "hora": ahora_iso(),
                    },
                },
                f"t-observacion-roja:{e.producto.firma}",
            )
            e = r.fold()
            antes = _git(r.espacio, "rev-parse", "HEAD").stdout.strip()
            eventos_antes = len(r.reg.eventos(r.enc))

            _paso_entrega(r.reg, e, vuelta.gh, ahora_iso())

            despues = _git(r.espacio, "rev-parse", "HEAD").stdout.strip()
            nuevos = r.reg.eventos(r.enc)[eventos_antes:]
            self.assertEqual(despues, antes, "la version en rojo no se publica")
            self.assertFalse(
                any(
                    ev["kind"] == "entrega_externa"
                    and ev["data"].get("paso") in ("commit", "push")
                    for ev in nuevos
                )
            )
            self.assertTrue(
                any(
                    ev["kind"] == "estrategia"
                    and ev["data"].get("rol") == "responsable"
                    and "rojo" in ev["data"].get("motivo", "")
                    for ev in nuevos
                )
            )


    def test_corregir_el_comando_desbloquea_la_publicacion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._producto_aprobado(tmp, "f1")
            rojo = self._declarar_comprobacion(
                r, ["python3", "-c", "import sys; sys.exit(1)"]
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "observacion_supervisor",
                    "data": {
                        "version": e.producto.firma,
                        "comprobaciones": rojo,
                        "ejecuciones": [{**rojo[0], "rc": 1}],
                        "hora": ahora_iso(),
                    },
                },
                f"t-observacion-f1:{e.producto.firma}",
            )
            e = r.fold()
            antes = _git(r.espacio, "rev-parse", "HEAD").stdout.strip()

            verde = self._declarar_comprobacion(
                r, ["python3", "-c", "import sys; sys.exit(0)"]
            )
            eventos_antes = len(r.reg.eventos(r.enc))
            with mock.patch("motor.encargos.ciclo.iniciar_comprobacion") as iniciar:
                _paso_entrega(r.reg, e, vuelta.gh, ahora_iso())
            iniciar.assert_called_once()
            nuevos = r.reg.eventos(r.enc)[eventos_antes:]
            self.assertFalse(
                any(
                    ev["kind"] == "estrategia"
                    and ev["data"].get("rol") == "responsable"
                    and "rojo" in ev["data"].get("motivo", "")
                    for ev in nuevos
                ),
                "la observacion roja del comando anterior no congela la version",
            )
            self.assertTrue(
                any(
                    ev["kind"] == "espera"
                    and "observacion del comprobador" in ev["data"].get("motivo", "")
                    for ev in nuevos
                )
            )
            self.assertEqual(
                _git(r.espacio, "rev-parse", "HEAD").stdout.strip(), antes
            )

            self._escribir_observacion(r, e.producto.firma, verde, rc=0)
            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertNotEqual(
                _git(r.espacio, "rev-parse", "HEAD").stdout.strip(), antes
            )

    def test_la_puerta_usa_la_version_que_se_publica(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, _commit, _legado = EntregaSinEfecto._con_commit_sin_sha(
                self, tmp, "f2"
            )
            declaradas = self._declarar_comprobacion(
                r, ["python3", "-c", "import sys; sys.exit(0)"]
            )
            actual = version_producto(r.espacio).firma
            e = r.fold()
            self.assertNotEqual(
                e.producto.firma,
                actual,
                "el caso conserva una firma registrada anterior a la que se publica",
            )
            self._escribir_observacion(r, actual, declaradas, rc=0)
            eventos_antes = len(r.reg.eventos(r.enc))

            _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            nuevos = r.reg.eventos(r.enc)[eventos_antes:]
            self.assertTrue(
                any(
                    ev["kind"] == "entrega_externa"
                    and ev["data"].get("paso") in ("commit", "push")
                    for ev in nuevos
                ),
                "la observacion de la version que se publica abre la entrega",
            )

    def test_un_comprobaciones_invalido_vuelve_al_responsable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._producto_aprobado(tmp, "f3")
            interno = r.espacio / ".interno"
            interno.mkdir(exist_ok=True)
            (interno / "comprobaciones.json").write_text("{", encoding="utf-8")
            eventos_antes = len(r.reg.eventos(r.enc))

            _paso_entrega(r.reg, e, vuelta.gh, ahora_iso())

            nuevos = r.reg.eventos(r.enc)[eventos_antes:]
            self.assertTrue(
                any(
                    ev["kind"] == "estrategia"
                    and ev["data"].get("rol") == "responsable"
                    and ev["data"].get("estrategia") == "reparar_instrumento"
                    for ev in nuevos
                ),
                "un comprobaciones.json invalido vuelve al responsable",
            )
            self.assertFalse(
                any(ev["kind"] == "espera" for ev in nuevos),
                "no se deja esperando sin que nadie pueda corregirlo",
            )


    def test_corregir_el_comando_hace_que_el_comprobador_vuelva_a_observar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "f1-inc", "crea saludo.txt")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
            rojo = self._declarar_comprobacion(
                r, ["python3", "-c", "import sys; sys.exit(1)"]
            )
            self._escribir_observacion(
                r, version_producto(r.espacio).firma, rojo, rc=1
            )
            _incorporar_responsable(
                r.reg, r.fold(), r.espacio, ahora_iso(), r.d
            )
            observadas = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "observacion_supervisor"
            ]
            self.assertTrue(observadas)

            verde = self._declarar_comprobacion(
                r, ["python3", "-c", "import sys; sys.exit(0)"]
            )
            antes = len(observadas)
            with mock.patch("motor.encargos.ciclo.iniciar_comprobacion") as iniciar:
                _incorporar_responsable(
                    r.reg, r.fold(), r.espacio, ahora_iso(), r.d
                )
            iniciar.assert_called_once()
            self.assertEqual(
                len(
                    [
                        ev
                        for ev in r.reg.eventos(r.enc)
                        if ev["kind"] == "observacion_supervisor"
                    ]
                ),
                antes,
                "la observacion roja del comando anterior no se reutiliza",
            )

            self._escribir_observacion(
                r, version_producto(r.espacio).firma, verde, rc=0
            )
            _incorporar_responsable(
                r.reg, r.fold(), r.espacio, ahora_iso(), r.d
            )
            ultima = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "observacion_supervisor"
            ][-1]
            self.assertEqual(ultima["data"]["comprobaciones"], verde)


if __name__ == "__main__":
    unittest.main()
