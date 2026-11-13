"""Un PR mergeado a mano se concilia aunque su fase no sea la activa.

La fase A se entregó con pendientes y quedó bloqueada; David mergeó su PR.
Cuando la fase activa ya es otra, el motor debe integrar A, desplegarla si el
encargo lo pide y destrabar a quienes dependen de ella, sin reabrir al
responsable ni mover el turno de la fase activa.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo, despliegue
from motor.encargos.dominio import _fase_disponible
from motor.encargos.registro import Registro
from motor.github import PrView
from tests.dobles import github_por_numero
from tests.test_flujo_revisiones import sonda_sin_aislamiento_del_host


def setUpModule():
    sonda_sin_aislamiento_del_host()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _lanzar_en_proceso(cmd, cwd, env, log):
    """La receta corre en el mismo proceso: la prueba mide el efecto, no el fork."""
    from motor.encargos import despliegue as ejecutor

    ejecutor.ejecutar(Path(cmd[-1]))
    return {
        "pid": 99999999, "boot_id": plataforma.boot_id(),
        "inicio": "", "job": "", "separacion": False,
    }


class _Banco(unittest.TestCase):
    RECETA_EN_REPO = False

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.destino = self.raiz / "servidor"
        self.destino.mkdir()
        self.bare = self.raiz / "repo.git"
        self.trabajo = self.raiz / "trabajo"
        git(self.raiz, "init", "-q", "--bare", "-b", "main", str(self.bare))
        git(self.raiz, "init", "-q", "-b", "main", str(self.trabajo))
        for nombre, texto in (
            ("d.sh", "desplegar"),
            ("c.sh", "checklist"),
            ("r.sh", "rollback"),
        ):
            (self.trabajo / nombre).write_text(
                f'printf "{texto}:%s\\n" "$MOTOR_SHA" '
                f'>> "{self.destino.as_posix()}/pasos"\n'
                'printf "ESTADO=sin_cambios\\n"\n'
            )
        (self.trabajo / "base.txt").write_text("base\n")
        if self.RECETA_EN_REPO:
            (self.trabajo / ".motor.toml").write_text(
                '[deploy]\ndesplegar = "d.sh"\nchecklist = "c.sh"\n'
                'rollback = "r.sh"\n'
            )
        git(self.trabajo, "add", "-A")
        git(
            self.trabajo, "-c", "user.name=t", "-c", "user.email=t@l",
            "commit", "-q", "-m", "base",
        )
        self.sha_base = git(self.trabajo, "rev-parse", "HEAD")
        git(self.trabajo, "push", "-q", str(self.bare), "main")
        git(self.trabajo, "checkout", "-q", "-b", "encargo/A")
        (self.trabajo / "a.txt").write_text("de A\n")
        git(self.trabajo, "add", "-A")
        git(
            self.trabajo, "-c", "user.name=t", "-c", "user.email=t@l",
            "commit", "-q", "-m", "A",
        )
        self.sha_a = git(self.trabajo, "rev-parse", "HEAD")
        git(self.trabajo, "push", "-q", str(self.bare), "encargo/A")
        self.rama_a = "encargo/A"

    def hacer_receta_ocupada(self) -> None:
        """La rama A publica una receta que declara el destino ocupado."""
        (self.trabajo / "d.sh").write_text(
            'printf "desplegar:%s\\n" "$MOTOR_SHA" '
            f'>> "{self.destino.as_posix()}/pasos"\n'
            'printf "app.cli vivo\\n"\n'
            "exit 75\n"
        )
        git(self.trabajo, "add", "-A")
        git(
            self.trabajo, "-c", "user.name=t", "-c", "user.email=t@l",
            "commit", "-q", "-m", "receta ocupada",
        )
        self.sha_a = git(self.trabajo, "rev-parse", "HEAD")
        git(self.trabajo, "push", "-q", str(self.bare), self.rama_a)

    def hacer_receta_fallida(self) -> None:
        """La rama A publica una receta que falla sin declarar ocupación."""
        (self.trabajo / "d.sh").write_text(
            'printf "desplegar:%s\\n" "$MOTOR_SHA" '
            f'>> "{self.destino.as_posix()}/pasos"\n'
            'printf "fallo propio de la receta\\n"\n'
            "exit 1\n"
        )
        git(self.trabajo, "add", "-A")
        git(
            self.trabajo, "-c", "user.name=t", "-c", "user.email=t@l",
            "commit", "-q", "-m", "receta fallida",
        )
        self.sha_a = git(self.trabajo, "rev-parse", "HEAD")
        git(self.trabajo, "push", "-q", str(self.bare), self.rama_a)

    def encargo(self, fases: list[dict]) -> tuple[Registro, str, Path]:
        reg = Registro(self.raiz / "base")
        self.addCleanup(reg.close)
        ident = ciclo.recibir(
            reg, "pedido", "conciliacion", ciclo.ahora_iso(),
            entrega_nivel="desplegado", repo=str(self.bare), base_rama="main",
        )["encargo"]
        espacio = reg.base / "espacio" / ident
        espacio.mkdir(parents=True)
        self.assertEqual(ciclo._asegurar_repo(reg.fold(ident), espacio), "")
        (reg.base / "maquina.toml").write_text(
            'nombre="ensayo"\nconf="."\ntiene=["ssh-goncloud"]\n'
        )
        reg.anotar(
            ident, {"kind": "fases", "data": {"fases": fases}}, "fases-prueba"
        )
        return reg, ident, espacio

    def preparar_z(self, reg: Registro, ident: str) -> None:
        """La base Z queda preparada sin ejecutar su fase: acredita integración."""
        reg.anotar(
            ident,
            {
                "kind": "politica_despliegue",
                "data": {
                    "fase": "Z", "requiere_despliegue": False,
                    "fuente": "prueba", "huella": "huella-Z",
                    "hora": ciclo.ahora_iso(),
                },
            },
            "politica-Z",
        )
        reg.anotar(
            ident,
            {
                "kind": "entrega_externa",
                "data": {
                    "fase": "Z", "paso": "integrado", "sha": self.sha_base,
                    "recibo": self.sha_base, "hora": ciclo.ahora_iso(),
                },
            },
            "integrado-Z",
        )
        reg.anotar(
            ident,
            {
                "kind": "comprobacion",
                "data": {
                    "clase": "confirmado", "evidencia": "Z lista",
                    "hora": ciclo.ahora_iso(),
                },
            },
            "comprobacion-Z",
        )

    def dejar_a_bloqueada(
        self, reg: Registro, ident: str, pr, sha: str
    ) -> None:
        for paso in ("commit", "push", "pr_listo", "pr_con_pendientes"):
            reg.anotar(
                ident,
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": "A", "paso": paso, "repo": str(self.bare),
                        "rama": self.rama_a, "base": "main",
                        "numero": pr.numero, "sha": sha, "recibo": sha,
                        "hora": ciclo.ahora_iso(),
                    },
                },
                f"entrega-A:{paso}",
            )
        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "evidencia": "entregada con pendientes",
                    "causa": "el relevo agotó los modelos autorizados",
                    "hora": ciclo.ahora_iso(),
                },
            },
            "bloqueo-A",
        )

    def abrir_y_mergear(self, gh, base: str = "main"):
        pr = gh.abrir_pr(str(self.bare), self.rama_a, base, "A: fase", "cuerpo")
        gh.marcar_listo(str(self.bare), pr.numero)
        return gh.merge(str(self.bare), pr.numero, self.sha_a, "squash", "A")

    def _fases(
        self, dependiente: bool = False, receta_en_repo: bool = False
    ) -> list[dict]:
        fase_a = {"id": "A", "resultado": "fase A", "requiere_despliegue": True}
        if not receta_en_repo:
            fase_a["despliegue"] = {
                "desplegar": "d.sh", "checklist": "c.sh", "rollback": "r.sh",
            }
        fases = [
            {"id": "Z", "resultado": "base", "requiere_despliegue": False},
            fase_a,
        ]
        if dependiente:
            fases.append({"id": "C", "resultado": "fase C", "requiere": ["A"]})
        fases.append({"id": "B", "resultado": "fase B", "requiere": ["Z"]})
        return fases

    def _preparar(self, dependiente: bool = False, receta_en_repo: bool = False):
        reg, ident, espacio = self.encargo(
            self._fases(dependiente, receta_en_repo)
        )
        self.preparar_z(reg, ident)
        self.assertEqual(reg.fold(ident).fase_activa, "A")
        gh = github_por_numero(self.raiz / "fake")
        fusionado = self.abrir_y_mergear(gh)
        self.dejar_a_bloqueada(reg, ident, fusionado, self.sha_a)
        enc = reg.fold(ident)
        self.assertEqual(enc.fase_activa, "B")
        self.assertEqual(enc.fases[1].nivel_alcanzado, "pr")
        self.assertEqual(enc.fases[1].estado, "bloqueada")
        parche = mock.patch.object(
            plataforma, "lanzar_separado", side_effect=_lanzar_en_proceso
        )
        parche.start()
        self.addCleanup(parche.stop)
        return reg, ident, gh, fusionado

    def _barrer(self, reg, gh, vueltas=10, salto_s=60, inicio=None):
        # Cada pasada avanza un minuto: las esperas por fase se cumplen sin
        # dormir el proceso de la prueba.
        inicio = inicio or datetime.now(timezone.utc)
        for i in range(vueltas):
            hora = (inicio + timedelta(seconds=salto_s * i)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            ciclo.barrer(reg, hora, time.time(), None, gh=gh)
            time.sleep(0.02)

    def _fase(self, enc, ident: str):
        return next(f for f in enc.fases if f.id == ident)

    def _completar_b(self, reg: Registro, ident: str) -> None:
        for evento, ukey in (
            (
                {
                    "kind": "politica_despliegue",
                    "data": {
                        "fase": "B", "requiere_despliegue": False,
                        "fuente": "prueba", "huella": "huella-B",
                        "hora": ciclo.ahora_iso(),
                    },
                },
                "politica-B",
            ),
            (
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": "B", "paso": "integrado", "sha": self.sha_base,
                        "recibo": self.sha_base, "hora": ciclo.ahora_iso(),
                    },
                },
                "integrado-B",
            ),
            (
                {
                    "kind": "comprobacion",
                    "data": {
                        "clase": "confirmado", "evidencia": "B lista",
                        "hora": ciclo.ahora_iso(),
                    },
                },
                "comprobacion-B",
            ),
        ):
            reg.anotar(ident, evento, ukey)


class ConciliacionFueraDeTurno(_Banco):
    def test_fase_bloqueada_de_otra_rama_se_integra_y_despliega(self):
        reg, ident, gh, fusionado = self._preparar()
        antes = reg.fold(ident)
        generacion = antes.generacion
        estado_b = self._fase(antes, "B").estado

        self._barrer(reg, gh)

        enc = reg.fold(ident)
        a, b = self._fase(enc, "A"), self._fase(enc, "B")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertTrue(a.desplegado_recibo)
        self.assertEqual(b.estado, estado_b)
        self.assertEqual(enc.fase_activa, "B")
        self.assertEqual(enc.generacion, generacion)
        self.assertFalse(any(
            ev["kind"] == "intento_abierto"
            and ev["data"].get("fase") == "A"
            for ev in reg.eventos(ident)
        ))
        pasos = (self.destino / "pasos").read_text().splitlines()
        self.assertEqual(pasos, [
            f"desplegar:{fusionado.merge_commit}",
            f"checklist:{fusionado.merge_commit}",
        ])

    def test_fase_dependiente_queda_lista_tras_conciliar_a(self):
        reg, ident, gh, _ = self._preparar(dependiente=True)
        self.assertNotEqual(_fase_disponible(reg.fold(ident).fases).id, "C")

        self._barrer(reg, gh)

        self.assertEqual(
            _fase_disponible(reg.fold(ident).fases).id, "C"
        )

    def test_receta_que_ya_contiene_el_sha_deja_desplegada(self):
        reg, ident, gh, _ = self._preparar()
        # La receta de prueba termina con ESTADO=sin_cambios y salida 0.
        self._barrer(reg, gh)
        a = self._fase(reg.fold(ident), "A")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertTrue(a.desplegado_recibo)

    def test_pr_cerrado_sin_merge_no_cambia_la_fase(self):  # no-roja-en-base: la base tampoco concilia un PR cerrado; la prueba guarda que el arreglo no lo cuente como integrado
        reg, ident, gh, _ = self._preparar()
        gh.responde_ver = lambda repo, numero: PrView(
            numero, self.rama_a, "main", False, self.sha_a, "cerrado", ""
        )
        ciclo.barrer(reg, ciclo.ahora_iso(), time.time(), None, gh=gh)
        a = self._fase(reg.fold(ident), "A")
        self.assertEqual(a.estado, "bloqueada")
        self.assertEqual(a.nivel_alcanzado, "pr")
        self.assertEqual(a.integrado_sha, "")
        self.assertFalse((self.destino / "pasos").exists())

    def test_merge_en_otra_base_no_cuenta_como_integrado(self):  # no-roja-en-base: la base tampoco integra un PR mergeado en otra base; la prueba guarda ese límite
        reg, ident, gh, fusionado = self._preparar()
        gh.responde_ver = lambda repo, numero: PrView(
            numero, self.rama_a, "otra", False, self.sha_a, "mergeado",
            fusionado.merge_commit,
        )
        ciclo.barrer(reg, ciclo.ahora_iso(), time.time(), None, gh=gh)
        a = self._fase(reg.fold(ident), "A")
        self.assertEqual(a.estado, "bloqueada")
        self.assertEqual(a.nivel_alcanzado, "pr")
        self.assertEqual(a.integrado_sha, "")
        self.assertFalse((self.destino / "pasos").exists())


class ConciliacionCierre(_Banco):
    def test_el_cierre_no_abandona_un_despliegue_pendiente(self):
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        self._barrer(reg, gh, vueltas=1)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertEqual(a.nivel_alcanzado, "integrado")
        self.assertEqual(a.estado, "bloqueada")

        self._completar_b(reg, ident)
        self._barrer(reg, gh, vueltas=4)

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertNotEqual(enc.estado, "terminado")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertTrue(a.desplegado_recibo)
        self.assertEqual(_fase_disponible(enc.fases).id, "C")
        self.assertFalse(any(
            ev["kind"] == "intento_abierto"
            and ev["data"].get("fase") == "A"
            for ev in reg.eventos(ident)
        ))

    def test_el_cierre_con_pendientes_no_abandona_el_despliegue_de_a(self):
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        self._barrer(reg, gh, vueltas=1)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertEqual(a.nivel_alcanzado, "integrado")
        self.assertEqual(a.estado, "bloqueada")

        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "fase": "B", "evidencia": "B entregada con pendientes",
                    "causa": "relevo agotado", "hora": ciclo.ahora_iso(),
                },
            },
            "review-bloqueo-B",
        )
        self._barrer(reg, gh, vueltas=4)

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertNotEqual(enc.estado, "terminado")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertTrue(a.desplegado_recibo)
        self.assertEqual(_fase_disponible(enc.fases).id, "C")
        self.assertEqual(enc.fase_activa, "C")
        self.assertFalse(any(
            ev["kind"] == "intento_abierto"
            and ev["data"].get("fase") == "A"
            for ev in reg.eventos(ident)
        ))

    def test_una_b_bloqueada_no_impide_conciliar_el_recibo_de_a(self):
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        self._barrer(reg, gh, vueltas=1)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertEqual(a.nivel_alcanzado, "integrado")

        reg.anotar(
            ident,
            {
                "kind": "politica_despliegue",
                "data": {
                    "fase": "B", "requiere_despliegue": True,
                    "fuente": "prueba", "huella": "huella-B-despliega",
                    "hora": ciclo.ahora_iso(),
                },
            },
            "politica-B-despliega",
        )
        reg.anotar(
            ident,
            {
                "kind": "entrega_externa",
                "data": {
                    "fase": "B", "paso": "integrado", "sha": self.sha_base,
                    "recibo": self.sha_base, "hora": ciclo.ahora_iso(),
                },
            },
            "integrado-B-despliega",
        )
        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "fase": "B", "evidencia": "B entregada con pendientes",
                    "causa": "relevo agotado", "hora": ciclo.ahora_iso(),
                },
            },
            "review-bloqueo-B",
        )
        self._barrer(reg, gh, vueltas=4)

        enc = reg.fold(ident)
        a, b = self._fase(enc, "A"), self._fase(enc, "B")
        self.assertNotEqual(enc.estado, "terminado")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertEqual(_fase_disponible(enc.fases).id, "C")
        self.assertEqual(enc.fase_activa, "C")
        self.assertEqual(b.estado, "bloqueada")


class ConciliacionCierreDuranteReceta(_Banco):
    RECETA_EN_REPO = True

    def _barrer_con_cierre(self, cierre: str):
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        correr_real = despliegue._correr

        def correr(reg_worker, encargo, paso, script, copia, env, bash):
            salida = correr_real(
                reg_worker, encargo, paso, script, copia, env, bash
            )
            if paso == "desplegar":
                self.assertEqual(salida[0].returncode, 0)
                if cierre == "confirmado":
                    self._completar_b(reg, ident)
                else:
                    reg.anotar(
                        ident,
                        {
                            "kind": "bloqueo_fase",
                            "data": {
                                "fase": "B",
                                "evidencia": "B entregada con pendientes",
                                "causa": "relevo agotado",
                                "hora": ciclo.ahora_iso(),
                            },
                        },
                        f"review-bloqueo-B-{cierre}",
                    )
            return salida

        with mock.patch.object(despliegue, "_correr", side_effect=correr):
            self._barrer(reg, gh, vueltas=1)
        self._barrer(reg, gh, vueltas=4)
        return reg.fold(ident), fusionado

    def test_el_cierre_confirmado_espera_al_trabajador_de_a(self):
        enc, fusionado = self._barrer_con_cierre("confirmado")
        a = self._fase(enc, "A")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertTrue(a.desplegado_recibo)
        self.assertNotEqual(enc.estado, "terminado")
        self.assertEqual(_fase_disponible(enc.fases).id, "C")

    def test_el_cierre_con_pendientes_espera_al_trabajador_de_a(self):
        enc, fusionado = self._barrer_con_cierre("bloqueado")
        a = self._fase(enc, "A")
        b = self._fase(enc, "B")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertTrue(a.desplegado_recibo)
        self.assertEqual(b.estado, "bloqueada")
        self.assertEqual(enc.fase_activa, "C")
        self.assertNotEqual(enc.estado, "terminado")

    def _barrer_con_cierre_tras_agotada(self, cierre: str):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real_git = ciclo._git
        inicio = datetime.now(timezone.utc)

        def sin_fetch(espacio, *args):
            if args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real_git(espacio, *args)

        with mock.patch.object(ciclo, "_git", side_effect=sin_fetch):
            self._barrer(reg, gh, vueltas=5, salto_s=40, inicio=inicio)

        reg.anotar(
            ident,
            {
                "kind": "orden",
                "data": {
                    "accion": "continuar", "clave": "retry-A",
                    "hora": (inicio + timedelta(seconds=170)).isoformat(),
                },
            },
            "orden-retry-A",
        )

        correr_real = despliegue._correr

        def correr(reg_worker, encargo, paso, script, copia, env, bash):
            salida = correr_real(
                reg_worker, encargo, paso, script, copia, env, bash
            )
            if paso == "desplegar":
                self.assertEqual(salida[0].returncode, 0)
                if cierre == "confirmado":
                    self._completar_b(reg, ident)
                else:
                    reg.anotar(
                        ident,
                        {
                            "kind": "bloqueo_fase",
                            "data": {
                                "fase": "B",
                                "evidencia": "B entregada con pendientes",
                                "causa": "relevo agotado",
                                "hora": ciclo.ahora_iso(),
                            },
                        },
                        "review-bloqueo-B-retry",
                    )
            return salida

        with mock.patch.object(despliegue, "_correr", side_effect=correr):
            self._barrer(
                reg, gh, vueltas=1,
                inicio=inicio + timedelta(seconds=200),
            )
        self._barrer(
            reg, gh, vueltas=4,
            inicio=inicio + timedelta(seconds=260),
        )
        return reg.fold(ident)

    def test_el_cierre_confirmado_espera_al_reintento_de_a(self):
        enc = self._barrer_con_cierre_tras_agotada("confirmado")
        a = self._fase(enc, "A")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertNotEqual(enc.estado, "terminado")
        self.assertEqual(_fase_disponible(enc.fases).id, "C")

    def test_el_cierre_con_pendientes_espera_al_reintento_de_a(self):
        enc = self._barrer_con_cierre_tras_agotada("bloqueado")
        a = self._fase(enc, "A")
        b = self._fase(enc, "B")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        self.assertEqual(b.estado, "bloqueada")
        self.assertEqual(_fase_disponible(enc.fases).id, "C")


class PlegadoDeEspera(_Banco):
    def test_una_espera_anterior_sin_intentos_se_pliega(self):
        reg, ident, espacio = self.encargo(self._fases(dependiente=True))
        self.assertEqual(
            reg.anotar(
                ident,
                {
                    "kind": "conciliacion_fase",
                    "data": {
                        "fase": "A", "paso": "espera",
                        "causa": "falta receta real",
                        "reanuda_en": "2099-01-01T00:00:00Z",
                        "hora": ciclo.ahora_iso(),
                    },
                },
                "espera-sin-intentos",
            ),
            "nuevo",
        )
        a = self._fase(reg.fold(ident), "A")
        self.assertEqual(a.despliegue_espera_en, "2099-01-01T00:00:00Z")
        self.assertEqual(a.despliegue_espera_intentos, 1)


class ConciliacionOcupada(_Banco):
    def test_la_receta_ocupada_no_se_relanza_en_cada_pasada(self):
        self.hacer_receta_ocupada()
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        self._barrer(reg, gh, vueltas=1)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        directorio = despliegue._directorio_de_fase(reg, enc, a)
        self.assertIsNotNone(directorio)
        libro = json.loads((directorio / "ocupado.json").read_text())
        self.assertEqual(libro["intentos"], 1)

        self._barrer(reg, gh, vueltas=3)

        enc = reg.fold(ident)
        libro = json.loads((directorio / "ocupado.json").read_text())
        self.assertEqual(libro["intentos"], 1)
        pasos = (self.destino / "pasos").read_text().splitlines()
        self.assertEqual(
            pasos.count(f"desplegar:{fusionado.merge_commit}"), 1
        )
        self.assertEqual(
            pasos.count(f"rollback:{fusionado.merge_commit}"), 1
        )
        self.assertEqual(enc.fase_activa, "B")
        self.assertNotEqual(enc.estado, "esperando_reintento")


class ConciliacionFallida(_Banco):
    def test_el_despliegue_fallido_no_queda_en_silencio(self):
        self.hacer_receta_fallida()
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        self._barrer(reg, gh, vueltas=2)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertEqual(
            len([
                imp for imp in enc.impedimentos
                if imp.clase.startswith("despliegue:")
            ]),
            1,
        )

        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "fase": "B", "evidencia": "B entregada con pendientes",
                    "causa": "relevo agotado", "hora": ciclo.ahora_iso(),
                },
            },
            "review-bloqueo-B",
        )
        self._barrer(reg, gh, vueltas=4)

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(
            len([
                imp for imp in enc.impedimentos
                if imp.clase.startswith("despliegue:")
            ]),
            1,
        )
        self.assertEqual(a.estado, "bloqueada")
        self.assertEqual(a.nivel_alcanzado, "integrado")

    def test_releer_el_recibo_fallido_no_marca_en_curso(self):
        self.hacer_receta_fallida()
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        self._barrer(reg, gh, vueltas=3)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertFalse(a.despliegue_en_curso)
        self.assertTrue(a.despliegue_impedido)

        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "fase": "B", "evidencia": "B entregada con pendientes",
                    "causa": "relevo agotado", "hora": ciclo.ahora_iso(),
                },
            },
            "review-bloqueo-B-releer",
        )
        self._barrer(reg, gh, vueltas=4)

        self.assertEqual(reg.fold(ident).estado, "terminado")


class ConciliacionOcupadaAlTope(_Banco):
    def test_el_tope_ocupado_no_queda_en_silencio(self):
        self.hacer_receta_ocupada()
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        with mock.patch.object(
            ciclo, "ESPERAS_OCUPADO_S", (0, 0, 0, 0, 0)
        ):
            self._barrer(reg, gh, vueltas=8)
        enc = reg.fold(ident)
        self.assertEqual(
            len([
                imp for imp in enc.impedimentos
                if imp.clase.startswith("despliegue:")
            ]),
            1,
        )

        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "fase": "B", "evidencia": "B entregada con pendientes",
                    "causa": "relevo agotado", "hora": ciclo.ahora_iso(),
                },
            },
            "review-bloqueo-B",
        )
        self._barrer(reg, gh, vueltas=4)

        enc = reg.fold(ident)
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(
            len([
                imp for imp in enc.impedimentos
                if imp.clase.startswith("despliegue:")
            ]),
            1,
        )
        a = self._fase(enc, "A")
        self.assertEqual(a.estado, "bloqueada")
        self.assertEqual(a.nivel_alcanzado, "integrado")

    def test_el_libro_no_tapa_un_recibo_fallido(self):
        self.hacer_receta_ocupada()
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        with mock.patch.object(ciclo, "ESPERAS_OCUPADO_S", (0, 0, 0, 0, 0)):
            self._barrer(reg, gh, vueltas=8)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        directorio = despliegue._directorio_de_fase(reg, enc, a)
        self.assertIsNotNone(directorio)
        recibo_path = directorio / "recibo.json"
        datos = json.loads(recibo_path.read_text())
        datos["estado"] = "fallido"
        datos["motivo"] = "fallo real de la receta"
        datos["pasos"] = [
            {
                "paso": "desplegar", "script": "d.sh", "codigo": 1,
                "stdout": "", "stderr": "fallo real de la receta",
                "enviado": True,
            },
            {
                "paso": "rollback", "script": "r.sh", "codigo": 0,
                "stdout": "", "stderr": "", "enviado": True,
            },
        ]
        recibo_path.write_text(json.dumps(datos))

        self._barrer(reg, gh, vueltas=1)

        enc = reg.fold(ident)
        causas = [
            imp.causa for imp in enc.impedimentos
            if imp.clase.startswith("despliegue:")
        ]
        self.assertTrue(
            any("fallo real de la receta" in c for c in causas), causas
        )

    def test_una_orden_nueva_reintenta_el_tope_ocupado(self):
        self.hacer_receta_ocupada()
        reg, ident, gh, fusionado = self._preparar(dependiente=True)
        with mock.patch.object(ciclo, "ESPERAS_OCUPADO_S", (0, 0, 0, 0, 0)):
            self._barrer(reg, gh, vueltas=8)
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        directorio = despliegue._directorio_de_fase(reg, enc, a)
        self.assertIsNotNone(directorio)
        libro = json.loads((directorio / "ocupado.json").read_text())
        intentos_antes = libro["intentos"]
        pasos_antes = len((self.destino / "pasos").read_text().splitlines())

        reg.anotar(
            ident,
            {
                "kind": "orden",
                "data": {
                    "accion": "continuar", "clave": "reintento-A",
                    "hora": ciclo.ahora_iso(),
                },
            },
            "orden:reintento-A",
        )
        self._barrer(reg, gh, vueltas=2)

        libro = json.loads((directorio / "ocupado.json").read_text())
        self.assertGreater(libro["intentos"], intentos_antes)
        pasos = (self.destino / "pasos").read_text().splitlines()
        self.assertGreater(len(pasos), pasos_antes)


class ConciliacionFetchTransitorio(_Banco):
    RECETA_EN_REPO = True

    def test_la_receta_que_no_arranca_conserva_la_causa_y_espera(self):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real = ciclo._git
        fallando = {"valor": True}

        def git_con_fallo(espacio, *args):
            if fallando["valor"] and args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real(espacio, *args)

        inicio = datetime.now(timezone.utc)
        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(reg, gh, vueltas=1, salto_s=10, inicio=inicio)
            self._barrer(
                reg, gh, vueltas=1, salto_s=10,
                inicio=inicio + timedelta(seconds=10),
            )

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertFalse((self.destino / "pasos").exists())
        causas = [
            imp.causa for imp in enc.impedimentos
            if imp.clase.startswith("despliegue:")
        ]
        self.assertTrue(any("falta receta real" in c for c in causas), causas)

        fallando["valor"] = False
        self._barrer(
            reg, gh, vueltas=2, salto_s=60,
            inicio=inicio + timedelta(seconds=40),
        )
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertTrue(a.desplegado_recibo)

    def test_la_espera_reintentable_no_abandona_el_reintento(self):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real = ciclo._git
        fallando = {"valor": True}

        def git_con_fallo(espacio, *args):
            if fallando["valor"] and args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real(espacio, *args)

        inicio = datetime.now(timezone.utc)
        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(reg, gh, vueltas=1, salto_s=10, inicio=inicio)

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertFalse((self.destino / "pasos").exists())
        self.assertTrue(any(
            "falta receta real" in imp.causa
            for imp in enc.impedimentos
            if imp.clase.startswith("despliegue:")
        ))

        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "fase": "B", "evidencia": "B entregada con pendientes",
                    "causa": "relevo agotado", "hora": ciclo.ahora_iso(),
                },
            },
            "review-bloqueo-B",
        )
        self.assertNotEqual(reg.fold(ident).estado, "terminado")

        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(
                reg, gh, vueltas=1, salto_s=10,
                inicio=inicio + timedelta(seconds=10),
            )
        self.assertFalse((self.destino / "pasos").exists())

        fallando["valor"] = False
        self._barrer(
            reg, gh, vueltas=2, salto_s=60,
            inicio=inicio + timedelta(seconds=40),
        )
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(enc.fase_activa, "C")

    def test_el_cierre_confirmado_no_abandona_la_espera(self):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real = ciclo._git
        fallando = {"valor": True}

        def git_con_fallo(espacio, *args):
            if fallando["valor"] and args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real(espacio, *args)

        inicio = datetime.now(timezone.utc)
        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(reg, gh, vueltas=1, salto_s=10, inicio=inicio)

        enc = reg.fold(ident)
        self.assertTrue(any(
            "falta receta real" in imp.causa
            for imp in enc.impedimentos
            if imp.clase.startswith("despliegue:")
        ))

        self._completar_b(reg, ident)
        self.assertNotEqual(reg.fold(ident).estado, "terminado")

        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(
                reg, gh, vueltas=1, salto_s=10,
                inicio=inicio + timedelta(seconds=10),
            )
        self.assertFalse((self.destino / "pasos").exists())

        fallando["valor"] = False
        self._barrer(
            reg, gh, vueltas=2, salto_s=60,
            inicio=inicio + timedelta(seconds=40),
        )
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(enc.fase_activa, "C")

    def test_la_espera_que_no_arranca_tiene_tope(self):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real = ciclo._git
        fallando = {"valor": True}

        def git_con_fallo(espacio, *args):
            if fallando["valor"] and args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real(espacio, *args)

        inicio = datetime.now(timezone.utc)
        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(reg, gh, vueltas=6, salto_s=60, inicio=inicio)

        enc = reg.fold(ident)
        causas = [
            imp.causa for imp in enc.impedimentos
            if imp.clase.startswith("despliegue:")
        ]
        self.assertTrue(any("no arrancó" in c for c in causas), causas)

        reg.anotar(
            ident,
            {
                "kind": "bloqueo_fase",
                "data": {
                    "fase": "B", "evidencia": "B entregada con pendientes",
                    "causa": "relevo agotado", "hora": ciclo.ahora_iso(),
                },
            },
            "review-bloqueo-B",
        )
        self._barrer(reg, gh, vueltas=2)

        self.assertEqual(reg.fold(ident).estado, "terminado")

    def test_el_tope_de_espera_no_reintenta_en_cada_barrido(self):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real_git, real_iniciar = ciclo._git, despliegue.iniciar
        inicio, intentos = datetime.now(timezone.utc), []

        def sin_fetch(espacio, *args):
            if args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real_git(espacio, *args)

        def iniciar(*args, **kwargs):
            resultado = real_iniciar(*args, **kwargs)
            intentos.append(resultado.get("estado"))
            return resultado

        with mock.patch.object(
            ciclo, "_git", side_effect=sin_fetch
        ), mock.patch.object(despliegue, "iniciar", side_effect=iniciar):
            self._barrer(reg, gh, vueltas=5, salto_s=40, inicio=inicio)
            self.assertEqual(len(intentos), 5, intentos)
            self._barrer(
                reg, gh, vueltas=3, salto_s=1,
                inicio=inicio + timedelta(seconds=161),
            )
            self.assertEqual(len(intentos), 5, intentos)

    def test_el_segundo_tope_no_reutiliza_el_evento_y_no_hace_fetch(self):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real_git, real_iniciar = ciclo._git, despliegue.iniciar
        inicio, intentos, fetches = datetime.now(timezone.utc), [], []

        def sin_fetch(espacio, *args):
            if args and args[0] == "fetch":
                fetches.append(args)
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real_git(espacio, *args)

        def iniciar(*args, **kwargs):
            resultado = real_iniciar(*args, **kwargs)
            intentos.append(resultado.get("estado"))
            return resultado

        with mock.patch.object(
            ciclo, "_git", side_effect=sin_fetch
        ), mock.patch.object(despliegue, "iniciar", side_effect=iniciar):
            self._barrer(reg, gh, vueltas=5, salto_s=40, inicio=inicio)
            self.assertEqual(len(intentos), 5, intentos)
            fetch_tope = len(fetches)
            self._barrer(
                reg, gh, vueltas=3, salto_s=1,
                inicio=inicio + timedelta(seconds=161),
            )
            self.assertEqual(len(intentos), 5, intentos)
            self.assertEqual(len(fetches), fetch_tope)

            reg.anotar(
                ident,
                {
                    "kind": "orden",
                    "data": {
                        "accion": "continuar", "clave": "retry-A",
                        "hora": (inicio + timedelta(seconds=170)).isoformat(),
                    },
                },
                "orden-retry-A-tope",
            )
            self._barrer(
                reg, gh, vueltas=5, salto_s=40,
                inicio=inicio + timedelta(seconds=200),
            )
            self.assertEqual(len(intentos), 10, intentos)
            fetch_tope2 = len(fetches)
            self._barrer(
                reg, gh, vueltas=3, salto_s=1,
                inicio=inicio + timedelta(seconds=361),
            )
            self.assertEqual(len(intentos), 10, intentos)
            self.assertEqual(len(fetches), fetch_tope2)

    def test_el_merge_commit_se_vuelve_a_buscar_al_desplegar(self):
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real = ciclo._git
        fallando = {"valor": True}

        def git_con_fallo(espacio, *args):
            if fallando["valor"] and args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real(espacio, *args)

        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(reg, gh, vueltas=1)

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertEqual(a.nivel_alcanzado, "integrado")
        self.assertFalse((self.destino / "pasos").exists())

        fallando["valor"] = False
        self._barrer(reg, gh, vueltas=4)

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.nivel_alcanzado, "desplegado")
        self.assertEqual(a.estado, "preparada")
        pasos = (self.destino / "pasos").read_text().splitlines()
        self.assertEqual(pasos, [
            f"desplegar:{fusionado.merge_commit}",
            f"checklist:{fusionado.merge_commit}",
        ])

    def test_la_receta_recuperada_persiste_su_identidad_ocupada(self):
        self.hacer_receta_ocupada()
        reg, ident, gh, fusionado = self._preparar(
            dependiente=True, receta_en_repo=True
        )
        real = ciclo._git
        fallando = {"valor": True}

        def git_con_fallo(espacio, *args):
            if fallando["valor"] and args and args[0] == "fetch":
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "red caída"
                )
            return real(espacio, *args)

        with mock.patch.object(ciclo, "_git", side_effect=git_con_fallo):
            self._barrer(reg, gh, vueltas=1)

        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.integrado_sha, fusionado.merge_commit)
        self.assertEqual(a.desplegar, "")
        self.assertFalse((self.destino / "pasos").exists())

        fallando["valor"] = False
        self._barrer(reg, gh, vueltas=4)

        pasos = (self.destino / "pasos").read_text().splitlines()
        self.assertEqual(
            pasos.count(f"desplegar:{fusionado.merge_commit}"), 1
        )
        self.assertEqual(
            pasos.count(f"rollback:{fusionado.merge_commit}"), 1
        )
        enc = reg.fold(ident)
        a = self._fase(enc, "A")
        self.assertEqual(a.desplegar, "d.sh")
        directorio = despliegue._directorio_de_fase(reg, enc, a)
        self.assertIsNotNone(directorio)
        libro = json.loads((directorio / "ocupado.json").read_text())
        self.assertEqual(libro["intentos"], 1)
