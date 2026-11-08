"""El nivel efectivo: el sondeo de solo lectura registra impedimentos fijos y
ninguna compuerta de acción integra o despliega más allá del nivel efectivo.

Causa 5 del pedido: el motor descubre tarde lo que le impide integrar o
desplegar. `capacidad` es la mirada de solo lectura nueva del puerto (real y
falso); el nivel efectivo se registra con el evento `nivel_efectivo` y las
compuertas de merge, despliegue y efecto `desplegar` lo respetan.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.ciclo import _sondeo_nivel, ahora_iso, recibir
from motor.encargos.dominio import (
    TransicionIlegal,
    nivel_cumplido,
    nivel_objetivo,
    reducir,
)
from motor.github import (
    CapacidadRemota,
    FakeGitHub,
    GhGitHub,
    PoliticaBloquea,
)
from tests.test_flujo_revisiones import (
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)
from tests.dobles import github_con_hallazgos


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _bare(tmp: Path) -> str:
    bare = tmp / "repo.git"
    semilla = tmp / "semilla"
    semilla.mkdir(exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", "--quiet", str(bare)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(bare), "config", "core.longpaths", "true"],
        check=True, capture_output=True,
    )
    for args in (
        ["init", "-b", "main", "--quiet"],
        ["-c", "user.name=t", "-c", "user.email=t@l", "commit", "--allow-empty", "-q", "-m", "base"],
        ["push", "--quiet", str(bare), "main"],
    ):
        subprocess.run(["git", "-C", str(semilla), *args], check=True, capture_output=True)
    return str(bare)


def _maquina(base: Path, tiene: list[str]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "maquina.toml").write_text(
        'nombre = "ensayo"\nconf = "conf.toml"\ntiene = '
        + json.dumps(tiene)
        + "\n",
        encoding="utf-8",
    )


def _pasos(r, paso: str) -> list[dict]:
    return [
        ev
        for ev in r.eventos("entrega_externa")
        if ev["data"].get("paso") == paso
    ]


class _GhEnsayo(GhGitHub):
    """El puerto real con respuestas de `gh` pregrabadas: sin red."""

    def __init__(self, guion):
        super().__init__({})
        self.guion = guion
        self.llamadas: list[tuple] = []

    @staticmethod
    def nombre(repo: str) -> str:
        return repo

    def _gh(self, *a: str) -> str:
        self.llamadas.append(a)
        return self.guion(a)


class CapacidadDelPuerto(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _bare(Path(self._tmp.name))

    def _fake(self, config: dict) -> FakeGitHub:
        d = Path(self._tmp.name) / "fake"
        d.mkdir(exist_ok=True)
        if config:
            (d / "repos.json").write_text(json.dumps({self.repo: config}))
        return FakeGitHub(d)

    def test_sin_reglas_fijas_no_bloquea(self):
        v = self._fake({}).capacidad(self.repo, "main")
        self.assertEqual(
            v,
            CapacidadRemota(True, True, False, "sin reglas fijas de merge en el remoto"),
        )

    def test_revisiones_requeridas_bloquean(self):
        v = self._fake({"revisiones_requeridas": 1}).capacidad(self.repo, "main")
        self.assertTrue(v.repo_ok)
        self.assertTrue(v.base_ok)
        self.assertTrue(v.integrar_bloqueado)
        self.assertIn("aprobación", v.detalle)

    def test_sin_push_bloquea(self):
        v = self._fake({"sin_push": True}).capacidad(self.repo, "main")
        self.assertTrue(v.integrar_bloqueado)
        self.assertIn("push", v.detalle)

    def test_remoto_sin_leer_queda_desconocido(self):
        v = self._fake({"capacidad": "desconocida"}).capacidad(self.repo, "main")
        self.assertEqual(
            v, CapacidadRemota(True, None, None, "el remoto no deja leer sus reglas")
        )

    def test_base_ausente_no_declara_bloqueo(self):
        v = self._fake({}).capacidad(self.repo, "otra-rama")
        self.assertFalse(v.base_ok)
        self.assertIsNone(v.integrar_bloqueado)
        self.assertIn("otra-rama", v.detalle)

    def test_repo_ilegible_no_declara_bloqueo(self):
        v = self._fake({}).capacidad(str(Path(self._tmp.name) / "no-existe"), "main")
        self.assertFalse(v.repo_ok)
        self.assertIsNone(v.integrar_bloqueado)

    def test_el_merge_del_falso_se_rechaza_de_forma_fija(self):
        gh = self._fake({"revisiones_requeridas": 1})
        pr = gh.abrir_pr(self.repo, "encargo/x", "main", "t", "c")
        gh.marcar_listo(self.repo, pr.numero)
        with self.assertRaises(PoliticaBloquea) as lanzado:
            gh.merge(self.repo, pr.numero, pr.head, "squash", "m")
        self.assertIn("de forma fija", str(lanzado.exception))

    def test_el_real_lee_proteccion_reglas_y_permisos(self):
        def con_revisiones(a):
            if a[:2] == ("api", f"repos/{self.repo}/branches/main/protection"):
                return json.dumps(
                    {"required_pull_request_reviews": {"required_approving_review_count": 1}}
                )
            if a[:2] == ("api", f"repos/{self.repo}/rules/branches/main"):
                return "[]"
            if a[:2] == ("api", f"repos/{self.repo}/branches/main"):
                return json.dumps({"name": "main"})
            if a[:2] == ("api", f"repos/{self.repo}"):
                return json.dumps({"permissions": {"push": True, "pull": True}})
            raise AssertionError(a)

        v = _GhEnsayo(con_revisiones).capacidad(self.repo, "main")
        self.assertTrue(v.integrar_bloqueado)
        self.assertIn("1 aprobación", v.detalle)

    def test_el_real_sin_proteccion_no_bloquea(self):
        def sin_proteccion(a):
            if a[:2] == ("api", f"repos/{self.repo}/branches/main/protection"):
                raise RuntimeError("gh api ...: HTTP 422: Branch not protected")
            if a[:2] == ("api", f"repos/{self.repo}/rules/branches/main"):
                return "[]"
            if a[:2] == ("api", f"repos/{self.repo}/branches/main"):
                return json.dumps({"name": "main"})
            if a[:2] == ("api", f"repos/{self.repo}"):
                return json.dumps({"permissions": {"push": True, "pull": True}})
            raise AssertionError(a)

        v = _GhEnsayo(sin_proteccion).capacidad(self.repo, "main")
        self.assertFalse(v.integrar_bloqueado)

    def test_el_real_con_403_ambiguo_queda_desconocido(self):
        def ambiguo(a):
            if a[:2] == ("api", f"repos/{self.repo}/branches/main/protection"):
                raise RuntimeError("gh api ...: HTTP 403: rate limit exceeded")
            if a[:2] == ("api", f"repos/{self.repo}/branches/main"):
                return json.dumps({"name": "main"})
            if a[:2] == ("api", f"repos/{self.repo}"):
                return json.dumps({"permissions": {"push": True, "pull": True}})
            raise AssertionError(a)

        v = _GhEnsayo(ambiguo).capacidad(self.repo, "main")
        self.assertIsNone(v.integrar_bloqueado)
        self.assertIn("sin leer", v.detalle)

    def test_el_real_sin_permiso_de_push_bloquea(self):
        def sin_push(a):
            ruta = a[1] if len(a) > 1 else ""
            if ruta.endswith("/branches/main/protection"):
                raise RuntimeError("gh api ...: HTTP 422: Branch not protected")
            if ruta.endswith("/rules/branches/main"):
                return "[]"
            if ruta.endswith("/branches/main"):
                return json.dumps({"name": "main"})
            if ruta == f"repos/{self.repo}":
                return json.dumps({"permissions": {"push": False, "pull": True}})
            raise AssertionError(a)

        v = _GhEnsayo(sin_push).capacidad(self.repo, "main")
        self.assertTrue(v.integrar_bloqueado)
        self.assertIn("push", v.detalle)

    def test_el_real_con_repo_ilegible_no_bloquea(self):
        def muerto(a):
            raise RuntimeError("gh api ...: HTTP 404: Not Found")

        v = _GhEnsayo(muerto).capacidad(self.repo, "main")
        self.assertFalse(v.repo_ok)
        self.assertIsNone(v.integrar_bloqueado)

    def test_el_real_con_regla_de_conjunto_bloquea(self):
        def regla_pr(a):
            if a[:2] == ("api", f"repos/{self.repo}/branches/main/protection"):
                raise RuntimeError("gh api ...: HTTP 422: Branch not protected")
            if a[:2] == ("api", f"repos/{self.repo}/rules/branches/main"):
                return json.dumps(
                    [
                        {
                            "type": "pull_request",
                            "parameters": {"required_approving_review_count": 2},
                        }
                    ]
                )
            if a[:2] == ("api", f"repos/{self.repo}/branches/main"):
                return json.dumps({"name": "main"})
            if a[:2] == ("api", f"repos/{self.repo}"):
                return json.dumps({"permissions": {"push": True, "pull": True}})
            raise AssertionError(a)

        v = _GhEnsayo(regla_pr).capacidad(self.repo, "main")
        self.assertTrue(v.integrar_bloqueado)
        self.assertIn("2 aprobación", v.detalle)


class RecepcionSondea(unittest.TestCase):
    """Al recibir, el sondeo con puerto registra solo lo ya comprobado."""

    def _registro(self, tmp: Path):
        from motor.encargos.registro import Registro

        r = Registro(tmp / "base")
        self.addCleanup(r.close)
        return r

    def test_el_remoto_que_prohibe_merge_limita_a_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            d = t / "fake"
            d.mkdir()
            (d / "repos.json").write_text(json.dumps({repo: {"revisiones_requeridas": 1}}))
            _maquina(t / "base", ["ssh-goncloud"])
            reg = self._registro(t)
            salida = recibir(
                reg, "pedido", "sondea-pr", ahora_iso(),
                entrega_nivel="integrado", repo=repo, base_rama="main",
                gh=FakeGitHub(d),
            )
            enc = reg.fold(salida["encargo"])
            self.assertEqual(enc.nivel_efectivo, "pr")
            self.assertIn("limitado a pr", enc.nivel_efectivo_motivo)
            self.assertEqual(
                salida["entrega"]["nivel_efectivo"], "pr"
            )
            self.assertIn("limitado a pr", salida["entrega"]["nivel_efectivo_motivo"])
            eventos = [e for e in reg.eventos(enc.id) if e["kind"] == "nivel_efectivo"]
            self.assertEqual(len(eventos), 1)
            self.assertEqual(eventos[0]["data"]["nivel"], "pr")
            self.assertEqual(eventos[0]["data"]["motivo"], enc.nivel_efectivo_motivo)
            from motor.encargos.dominio import prompt_responsable

            self.assertIn("limitado a pr", prompt_responsable(enc))

    def test_pedido_hasta_pr_no_genera_limitacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            d = t / "fake"
            d.mkdir()
            (d / "repos.json").write_text(json.dumps({repo: {"revisiones_requeridas": 1}}))
            _maquina(t / "base", ["ssh-goncloud"])
            reg = self._registro(t)
            salida = recibir(
                reg, "pedido", "sondea-pr-nivel", ahora_iso(),
                entrega_nivel="pr", repo=repo, base_rama="main",
                gh=FakeGitHub(d),
            )
            enc = reg.fold(salida["encargo"])
            self.assertEqual(enc.nivel_efectivo, "")
            self.assertEqual(enc.nivel_efectivo_motivo, "")
            self.assertEqual(nivel_objetivo(enc), "pr")

    def test_remoto_sin_leer_no_cambia_nivel(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            d = t / "fake"
            d.mkdir()
            (d / "repos.json").write_text(json.dumps({repo: {"capacidad": "desconocida"}}))
            _maquina(t / "base", ["ssh-goncloud"])
            reg = self._registro(t)
            salida = recibir(
                reg, "pedido", "sondea-desconocido", ahora_iso(),
                entrega_nivel="desplegado", repo=repo, base_rama="main",
                gh=FakeGitHub(d),
            )
            enc = reg.fold(salida["encargo"])
            self.assertEqual(enc.nivel_efectivo, "")
            self.assertEqual(
                [e for e in reg.eventos(enc.id) if e["kind"] == "nivel_efectivo"], []
            )

    def test_la_mirada_lenta_del_remoto_no_bloquea_la_recepcion(self):
        """F1 de DeepSeek: la recepción no se queda esperando minutos al
        remoto. El sondeo acota la mirada: si el puerto no contesta a
        tiempo, la capacidad queda desconocida y el nivel no cambia; el
        remoto se vuelve a mirar al adoptar fases y al integrar, donde las
        esperas ya tienen sus caminos propios."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            _maquina(t / "base", ["ssh-goncloud"])
            reg = self._registro(t)

            class _Lento:
                def __init__(self, segundos: float):
                    self.segundos = segundos

                def capacidad(self, repo, base):
                    time.sleep(self.segundos)
                    return CapacidadRemota(True, True, True, "tarde pero bloquea")

            with mock.patch("motor.encargos.ciclo.TOPE_MIRADA_S", 0.2):
                inicio = time.monotonic()
                salida = recibir(
                    reg, "pedido", "sondea-lenta", ahora_iso(),
                    entrega_nivel="desplegado", repo=repo, base_rama="main",
                    gh=_Lento(3.0),
                )
                transcurrido = time.monotonic() - inicio
            enc = reg.fold(salida["encargo"])
            self.assertLess(transcurrido, 2.0)
            self.assertEqual(enc.nivel_efectivo, "")
            self.assertEqual(
                [e for e in reg.eventos(enc.id) if e["kind"] == "nivel_efectivo"], []
            )

    def test_el_fin_de_la_cli_no_espera_al_remoto_lento(self):
        """h_dc2a2b5a: el tope de la mirada tiene que desbloquear también el
        fin del proceso de `motor encargar`. La salida del intérprete se
        junta con los hilos no daemon (el pool de concurrent.futures lo
        registraba aunque shutdown fuera wait=False), así que la prueba mide
        el fin del proceso real de la CLI como subproceso, con el gh real
        elegido por base/github.json {"real": true} y un gh lento en el
        PATH: la recepción vuelve con el tope y el proceso termina después,
        sin esperar las cuatro llamadas del gh rezagado."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = t / "repo"
            repo.mkdir()
            for args in (
                ("init", "-q", "-b", "main"),
                ("remote", "add", "origin", "https://github.com/ejemplo/repo.git"),
            ):
                subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True, capture_output=True,
                )
            gh_lento = t / "gh-lento"
            gh_lento.mkdir()
            falso = gh_lento / "gh"
            falso.write_text("#!/bin/sh\nsleep 15\necho '{}'\n", encoding="utf-8")
            falso.chmod(0o755)
            base = t / "base"
            base.mkdir()
            (base / "github.json").write_text('{"real": true}', encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = f"{gh_lento}{os.pathsep}{env.get('PATH', '')}"
            inicio = time.monotonic()
            hecho = subprocess.run(
                [
                    sys.executable, "-c",
                    "import sys; from motor.encargos.cli import main; "
                    "sys.exit(main(sys.argv[1:]))",
                    "encargar",
                    "--base", str(base),
                    "--texto", "pedido",
                    "--clave", "k1-cli-lenta",
                    "--entrega", "desplegado",
                    "--repo", str(repo),
                    "--base-rama", "main",
                ],
                capture_output=True, text=True, env=env, timeout=90,
            )
            transcurrido = time.monotonic() - inicio
            self.assertEqual(hecho.returncode, 0, hecho.stderr[-400:])
            self.assertIn('"encargo"', hecho.stdout)
            # El tope de la mirada es 10 s: la recepción vuelve con él y el
            # proceso termina después; el gh lento tarda 15 s por llamada y
            # sin el hilo daemon el proceso lo esperaba al salir (~60 s).
            self.assertLess(transcurrido, 18.0)

    def test_sin_puerto_queda_desconocido(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            _maquina(t / "base", ["ssh-goncloud"])
            reg = self._registro(t)
            salida = recibir(
                reg, "pedido", "sondea-sin-puerto", ahora_iso(),
                entrega_nivel="integrado", repo=repo, base_rama="main",
            )
            enc = reg.fold(salida["encargo"])
            self.assertEqual(enc.nivel_efectivo, "")

    def test_la_maquina_sin_capacidad_limita_a_integrado_al_recibir(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            _maquina(t / "base", [])
            reg = self._registro(t)
            salida = recibir(
                reg, "pedido", "sondea-maquina", ahora_iso(),
                entrega_nivel="desplegado", repo=repo, base_rama="main",
            )
            enc = reg.fold(salida["encargo"])
            self.assertEqual(enc.nivel_efectivo, "integrado")
            self.assertIn("limitado a integrado", enc.nivel_efectivo_motivo)

    def test_sin_configuracion_de_maquina_no_cambia_nivel(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as sin_conf:
            t = Path(tmp)
            repo = _bare(t)
            reg = self._registro(t)
            with mock.patch.dict(os.environ, {"MOTOR_HOME": sin_conf}):
                salida = recibir(
                    reg, "pedido", "sondea-sin-conf", ahora_iso(),
                    entrega_nivel="desplegado", repo=repo, base_rama="main",
                )
            enc = reg.fold(salida["encargo"])
            self.assertEqual(enc.nivel_efectivo, "")


class CapacidadAlAdoptarFases(unittest.TestCase):
    """Al adoptar las fases, la capacidad declarada de la máquina limita."""

    def _recorrido(self, tmp: str, tiene: list[str]) -> object:
        from tests.test_flujo_revisiones import Recorrido

        r = Recorrido(
            tmp, "adoptar-nivel", "agrega saludo.txt con hola",
            entrega_nivel="desplegado", repo=_bare(Path(tmp)), base_rama="main",
        )
        self.addCleanup(r.reg.close)
        _maquina(r.base, tiene)
        return r

    def _con_fases(self, r) -> None:
        interno = r.espacio / ".interno"
        interno.mkdir(exist_ok=True)
        (interno / "fases.json").write_text(
            json.dumps(
                {
                    "fases": [
                        {"id": "a", "resultado": "saludo.txt con hola"},
                        {"id": "b", "resultado": "otro.txt con dos", "requiere": ["a"]},
                    ]
                }
            )
        )

    def test_la_maquina_sin_capacidad_limita_al_adoptar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = self._recorrido(tmp, [])
            self._con_fases(r)
            (r.espacio / "saludo.txt").write_text("hola")
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.nivel_efectivo, "integrado")
            self.assertIn("limitado a integrado", e.nivel_efectivo_motivo)
            eventos = [ev for ev in r.eventos("nivel_efectivo")]
            self.assertEqual(len(eventos), 1)
            self.assertEqual(eventos[0]["data"]["nivel"], "integrado")

    def test_la_maquina_capaz_no_limita(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = self._recorrido(tmp, ["ssh-goncloud"])
            self._con_fases(r)
            (r.espacio / "saludo.txt").write_text("hola")
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.nivel_efectivo, "")
            self.assertEqual(r.eventos("nivel_efectivo"), [])

    def test_sin_configuracion_de_maquina_no_limita(self):
        with tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        ) as tmp, tempfile.TemporaryDirectory() as sin_conf:
            r = self._recorrido(tmp, [])
            (r.base / "maquina.toml").unlink()
            self._con_fases(r)
            (r.espacio / "saludo.txt").write_text("hola")
            with mock.patch.dict(os.environ, {"MOTOR_HOME": sin_conf}):
                r.incorporar()
            e = r.fold()
            self.assertEqual(e.nivel_efectivo, "")
            self.assertEqual(r.eventos("nivel_efectivo"), [])


class ReduceNivelEfectivo(unittest.TestCase):
    """El reductor valida el nivel efectivo contra el solicitado."""

    def _encargo(self, nivel: str):
        return reducir(
            None,
            {
                "kind": "entrega",
                "data": {
                    "id": "enc_ensayo",
                    "clave": "k",
                    "huella": "f",
                    "texto": "pedido",
                    "hora": "2026-11-07T00:00:00Z",
                    "entrega_nivel": nivel,
                    "repo": "r",
                    "via": "durable",
                },
            },
        )

    def _aplicar(self, enc, nivel: str, motivo: str = "motivo de ensayo"):
        return reducir(
            enc,
            {
                "kind": "nivel_efectivo",
                "data": {"nivel": nivel, "motivo": motivo, "hora": "2026-11-07T00:01:00Z"},
            },
            nuevo=True,
        )

    def test_pr_limita_a_un_desplegado(self):
        enc = self._aplicar(self._encargo("desplegado"), "integrado")
        self.assertEqual(enc.nivel_efectivo, "integrado")
        self.assertEqual(enc.nivel_efectivo_motivo, "motivo de ensayo")
        self.assertEqual(nivel_objetivo(enc), "integrado")

    def test_pr_limita_a_un_integrado(self):
        enc = self._aplicar(self._encargo("integrado"), "pr")
        self.assertEqual(nivel_objetivo(enc), "pr")

    def test_nunca_supera_al_solicitado(self):
        with self.assertRaises(TransicionIlegal):
            self._aplicar(self._encargo("pr"), "integrado")
        with self.assertRaises(TransicionIlegal):
            self._aplicar(self._encargo("integrado"), "integrado")
        with self.assertRaises(TransicionIlegal):
            self._aplicar(self._encargo("desplegado"), "desplegado")

    def test_valor_desconocido_es_ilegal(self):
        with self.assertRaises(TransicionIlegal):
            self._aplicar(self._encargo("desplegado"), "terminado")

    def test_la_misma_limitacion_es_un_sin_registro(self):
        enc = self._aplicar(self._encargo("desplegado"), "integrado")
        self.assertIs(self._aplicar(enc, "integrado", "otro motivo"), enc)

    def test_sin_limitacion_es_vacio(self):
        base = self._encargo("desplegado")
        enc = self._aplicar(base, "")
        self.assertIs(enc, base)
        self.assertEqual(enc.nivel_efectivo, "")
        self.assertEqual(enc.nivel_efectivo_motivo, "")

    def test_el_nivel_efectivo_solo_se_limita_mas(self):
        # h_6a425441: una determinación menos restrictiva no puede relajar la
        # limitación vigente (pr fijado por el remoto, integrado por la máquina).
        enc = self._aplicar(self._encargo("desplegado"), "pr")
        with self.assertRaises(TransicionIlegal):
            self._aplicar(enc, "integrado")
        with self.assertRaises(TransicionIlegal):
            self._aplicar(enc, "")

    def test_una_limitacion_mas_estricta_si_se_aplica(self):
        enc = self._aplicar(self._encargo("desplegado"), "integrado")
        apretado = self._aplicar(enc, "pr")
        self.assertEqual(apretado.nivel_efectivo, "pr")
        self.assertEqual(nivel_objetivo(apretado), "pr")


def _aprobador(d):
    """DeepSeek aprueba: el doble unico del main sin hallazgos."""
    return github_con_hallazgos(d, titulos=())


class CompuertasDeAccion(unittest.TestCase):
    """Las compuertas de acción: pr nunca integra; integrado sin despliegue
    cierra en integrado; con merge prohibido el encargo cierra en pr."""

    def _preparar(self, tmp: str, nivel: str, config: dict, tiene: list[str]):
        vuelta, r = _entrega_pr(self, tmp, "compuerta-" + nivel, _aprobador, config=config, nivel=nivel)
        _maquina(r.base, tiene)
        return vuelta, r

    def _sondea(self, r, gh) -> None:
        _sondeo_nivel(r.reg, r.fold(), gh, r.espacio, ahora_iso())

    def test_pedido_hasta_pr_nunca_integra_con_maquina_capaz(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._preparar(tmp, "pr", {}, ["ssh-goncloud"])
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(_pasos(r, "integrado"), [])
            self.assertEqual(_pasos(r, "desplegado"), [])
            self.assertEqual(nivel_objetivo(e), "pr")
            self.assertTrue(nivel_cumplido(e))

    def test_pedido_hasta_pr_nunca_integra_sin_capacidad_de_desplegar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._preparar(tmp, "pr", {}, [])
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(_pasos(r, "integrado"), [])
            self.assertEqual(_pasos(r, "desplegado"), [])

    def test_integrado_con_merge_prohibido_cierra_en_pr(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._preparar(tmp, "integrado", {"revisiones_requeridas": 1}, ["ssh-goncloud"])
            self._sondea(r, vuelta.gh)
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(e.nivel_efectivo, "pr")
            self.assertEqual(_pasos(r, "integrado"), [])
            self.assertEqual(_pasos(r, "desplegado"), [])
            self.assertTrue(nivel_cumplido(e))
            cierre = [
                ev
                for ev in r.eventos("comprobacion")
                if ev["data"].get("clase") == "cierre_comprobado"
            ]
            self.assertTrue(cierre)
            self.assertTrue(
                any("limitado a pr" in ev["data"].get("evidencia") or "" for ev in cierre)
            )

    def test_desplegado_sin_capacidad_integra_y_no_despliega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._preparar(tmp, "desplegado", {}, [])
            self._sondea(r, vuelta.gh)
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "integrado")
            self.assertEqual(e.nivel_efectivo, "integrado")
            self.assertIn("limitado a integrado", e.nivel_efectivo_motivo)
            self.assertEqual(len(_pasos(r, "integrado")), 1)
            self.assertEqual(_pasos(r, "desplegado"), [])
            self.assertTrue(nivel_cumplido(e))

    def test_desplegado_con_merge_prohibido_cierra_en_pr(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._preparar(
                tmp, "desplegado", {"revisiones_requeridas": 1}, ["ssh-goncloud"]
            )
            self._sondea(r, vuelta.gh)
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(_pasos(r, "integrado"), [])
            self.assertEqual(_pasos(r, "desplegado"), [])

    def test_la_adopcion_no_relaja_el_pr_del_sondeo_de_recibir(self):
        # h_6a425441: con merge prohibido en el remoto y máquina sin capacidad,
        # la adopción de fases (sin gh) no sube pr a integrado ni el motor
        # intenta el merge que ya sabía prohibido.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._preparar(tmp, "desplegado", {"revisiones_requeridas": 1}, [])
            self._sondea(r, vuelta.gh)
            self.assertEqual(r.fold().nivel_efectivo, "pr")
            interno = r.espacio / ".interno"
            interno.mkdir(exist_ok=True)
            (interno / "fases.json").write_text(
                json.dumps({"fases": [{"id": "a", "resultado": "saludo.txt con hola"}]})
            )
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.nivel_efectivo, "pr")
            self.assertEqual(
                [ev["data"]["nivel"] for ev in r.eventos("nivel_efectivo")], ["pr"]
            )
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertNotIn("necesita_usuario", e.estado)
            self.assertEqual(_pasos(r, "integrado"), [])


class ViasDeRecepcion(unittest.TestCase):
    """h_e94bfa99: la vía de los directores también sondea el remoto."""

    def test_directores_encargar_pasa_el_puerto(self):
        from motor.integraciones.directores import encargar as encargar_director
        from motor.encargos.registro import Registro

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            d = t / "fake"
            d.mkdir()
            (d / "repos.json").write_text(json.dumps({repo: {"revisiones_requeridas": 1}}))
            _maquina(t / "base", ["ssh-goncloud"])
            reg = Registro(t / "base")
            self.addCleanup(reg.close)
            salida = encargar_director(
                reg, "pedido", "director-1", ahora_iso(),
                "hermes", "hermes", "durable",
                entrega_nivel="integrado", repo=repo, base_rama="main",
                gh=FakeGitHub(d),
            )
            enc = reg.fold(salida["encargo"])
            self.assertEqual(enc.nivel_efectivo, "pr")
            self.assertIn("limitado a pr", enc.nivel_efectivo_motivo)

    def test_la_cli_de_directores_sondea_el_remoto(self):
        from motor.integraciones import cli as cli_directores
        from motor.encargos.registro import Registro
        from contextlib import redirect_stdout
        import io

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = _bare(t)
            base = t / "base"
            base.mkdir()
            (base / "github.json").write_text(json.dumps({"falso": str(t / "fake")}))
            _maquina(base, ["ssh-goncloud"])
            d = t / "fake"
            d.mkdir()
            (d / "repos.json").write_text(json.dumps({repo: {"revisiones_requeridas": 1}}))
            salida = io.StringIO()
            with redirect_stdout(salida):
                codigo = cli_directores.main(
                    [
                        "encargar", "--base", str(base),
                        "--texto", "pedido", "--clave", "director-cli",
                        "--director", "hermes", "--canal", "hermes",
                        "--via", "durable", "--entrega", "integrado",
                        "--repo", repo, "--base-rama", "main",
                    ]
                )
            self.assertEqual(codigo, 0)
            encargo = json.loads(salida.getvalue())["encargo"]
            reg = Registro(base)
            self.addCleanup(reg.close)
            enc = reg.fold(encargo)
            self.assertEqual(enc.nivel_efectivo, "pr")
            self.assertIn("limitado a pr", enc.nivel_efectivo_motivo)


if __name__ == "__main__":
    unittest.main()
