"""La compuerta de extremo a extremo: el banco con el agente de ensayo.

El banco recibe el encargo con `ciclo.recibir` y avanza solo con
`ciclo.barrer` y el lanzador de producción (`cli.lanzar_de_ruta` con la ruta
"ensayo", lo que el supervisor recibe de `--agente ensayo`). La prueba controla
únicamente el reloj lógico y el GitHub falso: no inyecta aprobaciones,
integraciones ni despliegues, no usa `Recorrido.juicio`, `_incorporar`,
`_paso_entrega` ni `_paso_despliegue`, y no escribe eventos internos. El agente
de ensayo es `ops/agente_ensayo.py`; la semilla del repo trae el `trabajo.sh`
que lo llama y el `escenario.json` de la variante. El agente lee el
prompt y la revisión por el protocolo normal, escribe producto,
`.interno/fases.json`, comprobaciones declaradas y el juicio en el resultado
del revisor. El PR, el merge, la comprobación declarada y el despliegue los
hace el motor con su trabajador real sobre el git local. Es la compuerta de
cualquier cambio futuro a revisiones, entrega o fases (ver
docs/flujo-revisiones.md). No prueba Hermes, `gh` real, `ai-review`, SSH ni
Orbit.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo, despliegue
from motor.encargos.agentes import (
    _bash_de_ensayo,
    _disponible,
    _lanzador,
    _modelos_para,
    _sesion_modo,
)
from motor.encargos.ciclo import barrer, recibir
from motor.encargos.cli import lanzar_de_ruta, marcar_ruta
from motor.encargos.dominio import vista
from motor.encargos.registro import Registro
from motor.encargos.rutas import elegir
from motor.github import ChecksVerdict, FakeGitHub, PoliticaBloquea
from ops.agente_ensayo import Comprobacion, Escenario, _escribir
from tests import git_env_limpio
from tests.entorno import deja_breakaway, requiere_windows

RAIZ = Path(__file__).resolve().parent.parent
PY = Path(sys.executable).resolve().as_posix()
AGENTE = RAIZ / "ops" / "agente_ensayo.py"
SEPARA_TRABAJADOR = os.name != "nt" or deja_breakaway()


def _iso(hora: datetime) -> str:
    return hora.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- selección

class LaSeleccionDelEnsayo(unittest.TestCase):
    """La CLI puede seleccionar el agente de ensayo: modelo no vacío,
    disponibilidad con bash del PATH y lanzador multiplataforma."""

    def test_modelos_para_ensayo_no_esta_vacio(self):
        for rol in ("responsable", "revisor"):
            modelos = _modelos_para("ensayo", rol, None)
            self.assertTrue(modelos, f"sin modelo para el rol {rol}")
            self.assertTrue(all(modelos), f"modelo vacío para el rol {rol}")

    def test_las_identidades_de_ensayo_son_distintas_por_rol(self):
        # El lanzador de producción excluye para el revisor las identidades
        # con las que corrió el responsable: con una sola identidad el
        # revisor quedaría "sin modelo de revisión distinto" y el encargo
        # no avanzaría.
        self.assertNotEqual(
            _modelos_para("ensayo", "responsable", None),
            _modelos_para("ensayo", "revisor", None),
        )

    def test_rutas_elegir_selecciona_el_ensayo(self):
        modelos = _modelos_para("ensayo", "responsable", None)
        enc = SimpleNamespace(agentes=("ensayo",), pausas=())
        elegido = elegir(
            enc, "2026-11-07T12:00:00Z", {"ensayo": modelos}, rol="responsable"
        )
        self.assertEqual(elegido, ("ensayo", modelos[0]))

    def test_disponible_ensayo_con_bash_en_el_path(self):
        if shutil.which("bash") is None:
            self.skipTest("este host no tiene bash en el PATH")
        self.assertTrue(_disponible("ensayo"))

    def test_en_windows_el_ensayo_esta_disponible_con_el_bash_de_git(self):
        with mock.patch("motor.encargos.agentes.os.name", "nt"), \
                mock.patch(
                    "motor.encargos.aislamiento._bash_de_git",
                    return_value="C:\\Program Files\\Git\\bin\\bash.exe",
                ), \
                mock.patch("motor.encargos.agentes.os.path.isfile", return_value=True):
            self.assertTrue(_disponible("ensayo"))

    def test_en_windows_el_bash_de_wsl_no_cuenta_como_ensayo(self):
        # El bash.exe del PATH de Windows resuelve primero el de WSL
        # (System32), que no ve las rutas C:/... del espacio: no es un bash
        # que sirva al agente de ensayo.
        with mock.patch("motor.encargos.agentes.os.name", "nt"), \
                mock.patch(
                    "motor.encargos.aislamiento._bash_de_git",
                    return_value="C:\\Windows\\System32\\bash.exe",
                ), \
                mock.patch("motor.encargos.agentes.os.path.isfile", return_value=True):
            self.assertFalse(_disponible("ensayo"))

    def test_en_windows_sin_bash_de_git_instalado_no_hay_ensayo(self):
        with mock.patch("motor.encargos.agentes.os.name", "nt"), \
                mock.patch(
                    "motor.encargos.aislamiento._bash_de_git",
                    return_value="C:\\Program Files\\Git\\bin\\bash.exe",
                ), \
                mock.patch("motor.encargos.agentes.os.path.isfile", return_value=False):
            self.assertFalse(_disponible("ensayo"))

    def test_en_windows_el_lanzador_usa_el_bash_de_git(self):
        prompt = Path("/p.md")
        espacio = Path("/e")
        with mock.patch("motor.encargos.agentes.os.name", "nt"), \
                mock.patch(
                    "motor.encargos.aislamiento._bash_de_git",
                    return_value="C:\\Program Files\\Git\\bin\\bash.exe",
                ), \
                mock.patch("motor.encargos.agentes.os.path.isfile", return_value=True):
            argv = _lanzador("ensayo")(SimpleNamespace(), prompt, espacio, "s")
        self.assertEqual(argv[0], "C:\\Program Files\\Git\\bin\\bash.exe")
        self.assertNotIn("\\", argv[1])
        self.assertTrue(argv[1].endswith("trabajo.sh"))

    def test_el_lanzador_no_depende_de_bin_bash(self):
        argv = _lanzador("ensayo")(SimpleNamespace(), Path("/p.md"), Path("/e"), "s")
        # El bash es el que _bash_de_ensayo resuelve en este host (bash del
        # PATH en POSIX, bash de Git en Windows), nunca /bin/bash.
        self.assertEqual(argv[0], _bash_de_ensayo() or "bash")
        self.assertNotIn("\\", argv[1])
        self.assertTrue(argv[1].endswith("trabajo.sh"))

    def test_el_sesion_modo_del_ensayo_es_nueva(self):
        enc = SimpleNamespace(
            modelos=(),
            viva_revisor=False,
            viva_responsable=False,
            sesion_revisor="",
            sesion_responsable="",
        )
        self.assertEqual(_sesion_modo(enc, "ensayo", "responsable"), "nueva")

    def test_el_programa_del_agente_fija_salto_de_linea_en_sus_escrituras(self):
        # En Windows write_text sin newline traduce "\n" a CRLF: la receta que
        # el agente escribe la ejecuta bash y cada "\r" de fin de línea entra
        # en la redirección (F1). El programa del agente fija el salto en toda
        # escritura.
        arbol = ast.parse(AGENTE.read_text(encoding="utf-8"))
        escrituras = [
            n
            for n in ast.walk(arbol)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "write_text"
        ]
        self.assertTrue(escrituras, "sin escrituras write_text en el agente")
        for n in escrituras:
            claves = {k.arg for k in n.keywords}
            self.assertIn(
                "newline", claves, f"write_text sin newline en la linea {n.lineno}"
            )

    @requiere_windows
    def test_en_windows_el_agente_escribe_la_receta_con_lf(self):
        escenario = _escenario_tres_fases(
            True, Path(tempfile.gettempdir()) / "dest-compuerta"
        )
        espacio = Path(tempfile.mkdtemp())
        interno = espacio / ".interno"
        interno.mkdir()
        _escribir(escenario, interno, espacio, "uno")
        for nombre in ("d.sh", "c.sh", "r.sh", ".motor.toml"):
            datos = (espacio / nombre).read_bytes()
            self.assertNotIn(b"\r", datos, f"{nombre} con CR")
            self.assertIn(b"\n", datos, f"{nombre} sin salto de linea")


class _DeepSeekAprueba(FakeGitHub):
    """DeepSeek aprueba cada SHA que le lee el motor."""

    def comentarios_deepseek(self, repo, numero, sha=""):
        return [{"texto": "DeepSeek: APPROVED", "autor": "DeepSeek", "sha": sha}]


class _OtraPersonaIntegraDuranteLaCI(_DeepSeekAprueba):
    """La CI requerida sigue en marcha cuando el motor intenta integrar y, en
    esa espera, otra persona integra el PR desde GitHub."""

    def __init__(self, d):
        super().__init__(d)
        self.en_espera: set[int] = set()
        self.integro_otra_persona = False

    def merge(self, repo, numero, sha, metodo, mensaje):
        if numero not in self.en_espera:
            self.en_espera.add(numero)
            raise PoliticaBloquea("checks requeridos no están en verde: ['ci']")
        return super().merge(repo, numero, sha, metodo, mensaje)

    def checks_requeridos(self, repo, numero, sha):
        return ChecksVerdict(sha, False, False, ("ci",), (), en_marcha=("ci",))

    def ver(self, repo, numero):
        vista = super().ver(repo, numero)
        if numero in self.en_espera and vista.estado == "abierto":
            self.merge(repo, numero, vista.head, "squash", "otra persona")
            self.integro_otra_persona = True
            vista = super().ver(repo, numero)
        return vista


class _LaBaseYaTieneLaFaseDos(_DeepSeekAprueba):
    """Antes de abrir el PR de la fase dos, otra persona integra su rama en la
    base del remoto y GitHub responde "No commits between"."""

    def abrir_pr(self, repo, rama, base, titulo, cuerpo):
        if rama.endswith("-dos"):
            with tempfile.TemporaryDirectory() as t:
                copia = Path(t) / "copia"
                _git(Path(t), "clone", "-q", "-b", base, repo, str(copia))
                _git(copia, "fetch", "-q", "origin", rama)
                _git(
                    copia, "-c", "user.name=o", "-c", "user.email=o@local",
                    "merge", "-q", "--no-edit", "FETCH_HEAD",
                )
                _git(copia, "push", "-q", "origin", base)
            raise RuntimeError(f"pull request create failed: No commits between {base} and {rama}")
        return super().abrir_pr(repo, rama, base, titulo, cuerpo)


# --------------------------------------------------------------------------- agente de ensayo

_TRABAJO_SH = """#!/bin/bash
exec "@PY@" "@AGENTE@" "$(dirname "$0")/escenario.json"
"""

_GITATTRS = "* -text\n"


def _git(raiz: Path, *args: str) -> str:
    hecho = subprocess.run(
        ["git", "-C", str(raiz), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=git_env_limpio(),
    )
    if hecho.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} salio {hecho.returncode} en {raiz}: "
            + (hecho.stderr.strip() or hecho.stdout.strip())[:400]
        )
    return hecho.stdout.strip()


# --------------------------------------------------------------------------- banco

class Agotado(AssertionError):
    """El banco no llegó al destino dentro de sus topes; el mensaje trae la vista."""


def _cerrar_contenido(creado) -> None:
    try:
        creado.proceso.wait(timeout=15)
    except Exception:  # noqa: BLE001  el cleanup no tapa el resultado de la prueba
        pass
    if creado.proceso.poll() is None:
        plataforma.matar_grupo(creado.proceso)
        try:
            creado.proceso.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _trabajador_contenido(caso) -> None:
    """En un host sin breakaway (el runner hospedado de Windows) el trabajador
    se crea contenido en su Job. Es infraestructura de lanzamiento, no una
    inyección: el proceso es el de producción y el supervisor lo observa igual."""

    def lanzar(cmd, cwd, env, log):
        with log.open("ab") as out:
            creado = plataforma.crear_proceso(
                cmd,
                plataforma.Propiedad.COMANDO_CONTENIDO,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        caso.addCleanup(_cerrar_contenido, creado)
        marca = {
            "pid": creado.proceso.pid,
            "boot_id": plataforma.boot_id(),
            "inicio": plataforma.hora_inicio(creado.proceso.pid),
            "job": creado.job,
            "separacion": creado.separacion,
        }
        creado.liberar()
        return marca

    parche = mock.patch.object(plataforma, "lanzar_separado", side_effect=lanzar)
    parche.start()
    caso.addCleanup(parche.stop)


class Banco:
    """Un encargo real recibido y barrido con el lanzador de producción.

    La prueba solo controla el reloj (la hora lógica salta a cada espera
    vencida) y el GitHub falso. Todo lo demás —turnos del agente de ensayo,
    comprobador, PR, merge y despliegue con su trabajador— lo hace el motor
    con efectos locales reales.
    """

    def __init__(
        self,
        tmp: Path,
        *,
        nivel: str = "desplegado",
        config: dict | None = None,
        gh_cls=FakeGitHub,
        escenario: Escenario,
        pedido: str = "banco compuerta: deja el producto de cada fase con su despliegue local",
    ):
        self.tmp = tmp
        self.destino = tmp / "destino"
        self.destino.mkdir()
        self._semilla(tmp, escenario)
        fake = tmp / "fake"
        fake.mkdir()
        if config:
            (fake / "repos.json").write_text(
                json.dumps({str(tmp / "repo.git"): config}), encoding="utf-8"
            )
        self.gh = gh_cls(fake)
        self.reg = Registro(tmp / "base")
        self.base = self.reg.base
        (self.base / "maquina.toml").write_text(
            'nombre="banco compuerta"\nconf="."\ntiene=["ssh-goncloud"]\n',
            encoding="utf-8",
        )
        poteto = self.base / "politica" / "poteto" / "skills" / "poteto-mode"
        poteto.mkdir(parents=True)
        (poteto / "SKILL.md").write_text("# poteto-mode (banco)\n", encoding="utf-8")
        self.hora = datetime.now(timezone.utc).replace(tzinfo=None)
        salida = recibir(
            self.reg,
            pedido,
            "compuerta-" + tmp.name[-8:],
            _iso(self.hora),
            entrega_nivel=nivel,
            repo=str(tmp / "repo.git"),
            base_rama="main",
            gh=self.gh,
        )
        self.ident = salida["encargo"]
        self.espacio = self.base / "espacio" / self.ident

    def _semilla(self, tmp: Path, escenario: Escenario) -> None:
        semilla = tmp / "semilla"
        semilla.mkdir()
        (semilla / ".gitattributes").write_text(_GITATTRS, encoding="utf-8")
        (semilla / "base.txt").write_text("base\n", encoding="utf-8")
        (semilla / "trabajo.sh").write_text(
            _TRABAJO_SH.replace("@PY@", PY).replace("@AGENTE@", AGENTE.as_posix()),
            encoding="utf-8",
            newline="\n",
        )
        (semilla / "escenario.json").write_text(
            json.dumps(escenario, ensure_ascii=False, indent=1),
            encoding="utf-8",
            newline="\n",
        )
        _git(semilla, "init", "-q", "-b", "main")
        _git(semilla, "add", "-A")
        _git(
            semilla, "-c", "user.name=t", "-c", "user.email=t@local",
            "commit", "-q", "-m", "semilla",
        )
        bare = tmp / "repo.git"
        # Con main como rama inicial: sin esto el HEAD del bare depende de
        # init.defaultBranch del host (los runners no la traen) y el sondeo
        # de recibir ve "repo no legible" en lugar de la regla de merge.
        _git(semilla, "init", "--bare", "-q", "-b", "main", str(bare))
        _git(semilla, "push", "-q", str(bare), "main")
        _git(bare, "config", "core.longpaths", "true")

    def fold(self):
        return self.reg.fold(self.ident)

    def eventos(self, kind: str) -> list[dict]:
        return [e for e in self.reg.eventos(self.ident) if e["kind"] == kind]

    def pasos_entrega(self, paso: str) -> list[dict]:
        return [
            e["data"]
            for e in self.eventos("entrega_externa")
            if e["data"].get("paso") == paso
        ]

    RUTA = ("ensayo",)

    def paso(self) -> None:
        enc = self.fold()
        if (
            enc.estado == "esperando_reintento"
            and enc.reanuda_en
            and enc.reanuda_en > _iso(self.hora)
        ):
            vencida = datetime.fromisoformat(
                enc.reanuda_en.replace("Z", "+00:00")
            ).replace(tzinfo=None)
            self.hora += timedelta(seconds=(vencida - self.hora).total_seconds() + 1.0)
        else:
            self.hora += timedelta(seconds=61)
        hora = _iso(self.hora)
        # El supervisor persiste su ruta en los encargos antes de barrer, con
        # la misma función de la CLI.
        marcar_ruta(self.reg, self.RUTA, hora)
        barrer(
            self.reg,
            hora,
            self.hora.replace(tzinfo=timezone.utc).timestamp(),
            lanzar_de_ruta(self.reg, self.RUTA),
            umbral_s=30.0,
            gh=self.gh,
        )

    def correr(
        self,
        hasta,
        tope_pasos: int = 400,
        tope_s: float = 480.0,
    ) -> None:
        fin_real = time.monotonic() + tope_s
        for _ in range(tope_pasos):
            if hasta():
                return
            self.paso()
            if time.monotonic() > fin_real:
                break
            time.sleep(0.12)
        raise Agotado(
            "el banco no llegó al destino; estado="
            + json.dumps(vista(self.fold()), ensure_ascii=False, default=str)[:4000]
        )

    def recibos_despliegue(self) -> list[dict]:
        recibos = []
        for ruta in (self.base / "despliegues").rglob("recibo.json"):
            try:
                recibos.append(json.loads(ruta.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return recibos


class CompuertaReal(unittest.TestCase):
    """Base común: cada prueba arma su banco desechable."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="compuerta-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        if not SEPARA_TRABAJADOR:
            _trabajador_contenido(self)

    def banco(self, escenario: Escenario, **kw) -> Banco:
        banco = Banco(self.tmp, escenario=escenario, **kw)
        self.addCleanup(banco.reg.close)
        return banco

    def _comprobaciones(self, fases: dict[str, str]) -> dict[str, list[Comprobacion]]:
        return {
            fase: [{
                "objetivo": "saludo.txt con la palabra de la fase",
                "argv": [
                    PY, "-c",
                    "import sys; sys.exit(0 if sys.argv[1] in "
                    "open('saludo.txt', encoding='utf-8').read() else 1)",
                    palabra,
                ],
                "tope": 60,
            }]
            for fase, palabra in fases.items()
        }

    def _semilla_receta(self) -> None:
        # La receta local inocua vive en la semilla del repo: ya estaba en la
        # base antes del primer turno; el motor no la crea.
        destino = (self.tmp / "destino").as_posix()
        semilla = self.tmp / "semilla"
        (semilla / ".motor.toml").write_text(
            '[deploy]\ndesplegar="d.sh"\nchecklist="c.sh"\nrollback="r.sh"\n'
            'requiere=["ssh-goncloud"]\n',
            encoding="utf-8",
        )
        for nombre, paso in (
            ("d.sh", "desplegar"), ("c.sh", "checklist"), ("r.sh", "rollback")
        ):
            (semilla / nombre).write_text(
                f'printf "{paso}:%s\\n" "$MOTOR_SHA" >> "{destino}/pasos"\n',
                encoding="utf-8",
                newline="\n",
            )
        _git(semilla, "add", "-A")
        _git(
            semilla, "-c", "user.name=t", "-c", "user.email=t@local",
            "commit", "-q", "-m", "receta",
        )
        _git(semilla, "push", "-q", str(self.tmp / "repo.git"), "main")

    def _plazos_en_hora_logica(self, banco: Banco) -> None:
        # Los plazos de `_reanuda_en` salen del reloj de pared, que el banco no
        # controla: con la hora lógica adelantada, una espera de cinco minutos
        # ya nacería vencida y no se vería.
        parche = mock.patch.object(
            ciclo, "_reanuda_en",
            side_effect=lambda s: _iso(banco.hora + timedelta(seconds=s)),
        )
        parche.start()
        self.addCleanup(parche.stop)

    def _prs(self, banco: Banco) -> dict:
        return json.loads(
            (banco.tmp / "fake" / "github.json").read_text(encoding="utf-8")
        )["prs"]


def _escenario_tres_fases(receta_creada: bool, destino: Path) -> Escenario:
    """Tres fases con despliegue local inocuo. Con `receta_creada`, la fase
    uno escribe en su producto el .motor.toml y la receta que las siguientes
    usan; sin ella, la receta ya está en la semilla (lo aporta la prueba)."""
    producto_uno = {"saludo.txt": "hola\\n"}
    if receta_creada:
        producto_uno[".motor.toml"] = (
            '[deploy]\ndesplegar="d.sh"\nchecklist="c.sh"\nrollback="r.sh"\n'
            'requiere=["ssh-goncloud"]\n'
        )
        for nombre, paso in (
            ("d.sh", "desplegar"), ("c.sh", "checklist"), ("r.sh", "rollback")
        ):
            producto_uno[nombre] = (
                f'printf "{paso}:%s\\n" "$MOTOR_SHA" >> "{destino.as_posix()}/pasos"\n'
            )
    return {
        "fases": [
            {"id": "uno", "resultado": "saludo.txt con hola", "requiere_despliegue": True},
            {"id": "dos", "resultado": "saludo.txt con hola y mundo",
             "requiere": ["uno"], "requiere_despliegue": True},
            {"id": "tres", "resultado": "saludo.txt con hola, mundo y motor",
             "requiere": ["dos"], "requiere_despliegue": True},
        ],
        "producto": {
            "uno": producto_uno,
            "dos": {"saludo.txt": "hola\\nmundo\\n"},
            "tres": {"saludo.txt": "hola\\nmundo\\nmotor\\n"},
        },
    }


class ElCaminoFeliz(CompuertaReal):
    """3 fases con entrega desplegado: PR, revisión, merge en el git local y
    recibo de despliegue real por fase; sin cierre_forzado ni órdenes manuales."""

    def test_tres_fases_llegan_a_desplegado_sin_inyecciones(self):
        banco = self.banco(
            _escenario_tres_fases(False, self.tmp / "destino"),
            gh_cls=_DeepSeekAprueba,
        )
        self._semilla_receta()
        banco.correr(
            lambda: banco.fold().estado == "terminado"
            and all(f.estado == "preparada" for f in banco.fold().fases)
        )
        enc = banco.fold()
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual([f.estado for f in enc.fases], ["preparada"] * 3)
        self.assertEqual(
            sorted(p["fase"] for p in banco.pasos_entrega("desplegado")),
            ["dos", "tres", "uno"],
        )
        self.assertEqual(len(banco.pasos_entrega("integrado")), 3)
        self.assertEqual(banco.eventos("cierre_forzado"), [])
        self.assertEqual(banco.eventos("orden"), [])
        self.assertEqual(
            len([
                r for r in banco.recibos_despliegue()
                if r.get("estado") == "aplicado"
            ]),
            3,
        )
        pasos = (banco.destino / "pasos").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [p.split(":")[0] for p in pasos], ["desplegar", "checklist"] * 3
        )
        self.assertEqual(enc.nivel_alcanzado, "desplegado")
        self.assertEqual(
            [p["estado"] for p in self._prs(banco).values()], ["mergeado"] * 3
        )


class LasVariantesDeLaCompuerta(CompuertaReal):
    """Cada variante en su prueba, sin inyecciones."""

    def test_la_fase_que_crea_la_receta_que_usa_la_siguiente_despliega(self):
        # La receta no está en la base: la fase uno la crea en su producto y
        # la fase dos despliega con ella. La falta inicial de receta no
        # demuestra incapacidad.
        banco = self.banco(
            _escenario_tres_fases(True, self.tmp / "destino"), gh_cls=_DeepSeekAprueba
        )
        banco.correr(
            lambda: banco.fold().estado == "terminado"
            and all(f.estado == "preparada" for f in banco.fold().fases)
        )
        self.assertEqual(banco.fold().estado, "terminado")
        self.assertEqual(
            sorted(p["fase"] for p in banco.pasos_entrega("desplegado")),
            ["dos", "tres", "uno"],
        )
        self.assertEqual(len(banco.recibos_despliegue()), 3)

    def test_la_fase_sin_despliegue_integra_y_el_encargo_llega_a_desplegado(self):
        escenario = _escenario_tres_fases(False, self.tmp / "destino")
        escenario["fases"][1]["requiere_despliegue"] = False
        escenario["comprobaciones"] = self._comprobaciones(
            {"uno": "hola", "dos": "mundo", "tres": "motor"}
        )
        banco = self.banco(escenario, gh_cls=_DeepSeekAprueba)
        self._semilla_receta()
        banco.correr(
            lambda: banco.fold().estado == "terminado"
            and all(f.estado == "preparada" for f in banco.fold().fases)
        )
        enc = banco.fold()
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(enc.nivel_alcanzado, "desplegado")
        self.assertEqual(
            sorted(p["fase"] for p in banco.pasos_entrega("desplegado")),
            ["tres", "uno"],
        )
        self.assertEqual(
            sorted(p["fase"] for p in banco.pasos_entrega("integrado")),
            ["dos", "tres", "uno"],
        )

    def test_el_revisor_que_omite_el_bloqueante_ya_corregido_no_frena_el_encargo(self):
        # Ronda 1: la fase uno sale mal y el revisor la devuelve. Ronda 2: el
        # producto ya está corregido y el revisor aprueba SIN nombrar el
        # bloqueante en resueltos: una revisión concluyente deja resuelto lo
        # que cubre y no vuelve a reportar (causa 1), y el encargo llega a la
        # fase tres sin escalera.
        escenario = _escenario_tres_fases(False, self.tmp / "destino")
        escenario["exigido"] = dict(escenario["producto"])
        escenario["producto"]["uno"] = {"saludo.txt": "holaa\\n"}
        escenario["arreglos"] = {"no cumple el pedido": {"saludo.txt": "hola\\n"}}
        escenario["resuelve"] = False
        escenario["comprobaciones"] = self._comprobaciones(
            {"uno": "hola", "dos": "mundo", "tres": "motor"}
        )
        banco = self.banco(escenario, gh_cls=_DeepSeekAprueba)
        self._semilla_receta()
        banco.correr(
            lambda: banco.fold().estado == "terminado"
            and all(f.estado == "preparada" for f in banco.fold().fases)
        )
        enc = banco.fold()
        self.assertEqual([f.estado for f in enc.fases], ["preparada"] * 3)
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(banco.eventos("sin_progreso"), [])
        revisiones_uno = [
            e for e in banco.eventos("revision")
            if e["data"].get("fase") == "uno"
            and e["data"].get("origen") == "independiente"
        ]
        self.assertEqual(len(revisiones_uno), 2)
        self.assertEqual(revisiones_uno[0]["data"]["veredicto"], "cambios")
        self.assertEqual(revisiones_uno[1]["data"]["veredicto"], "aprobado")
        self.assertEqual(revisiones_uno[1]["data"]["alcance"], "correccion")
        self.assertNotIn("resueltos", revisiones_uno[1]["data"])
        resueltos_omision = [
            h for h in revisiones_uno[-1]["data"]["hallazgos"]
            if h.get("estado") == "resuelto"
        ]
        self.assertTrue(
            resueltos_omision,
            "la ronda 2 no dejó el bloqueante corregido como resuelto por omisión",
        )

    def test_sin_deepseek_la_fase_se_integra_y_despliega_con_nota(self):
        # Sin comentarios de DeepSeek (el falso no trae ninguno): las esperas
        # reales del motor avanzan el reloj lógico hasta el tope, sin aprobar
        # nada; la fase se integra y despliega con la nota sin-revisión y
        # llega la fase tres.
        escenario = _escenario_tres_fases(False, self.tmp / "destino")
        escenario["comprobaciones"] = self._comprobaciones(
            {"uno": "hola", "dos": "mundo", "tres": "motor"}
        )
        banco = self.banco(escenario)
        self._semilla_receta()
        banco.correr(
            lambda: banco.fold().estado == "terminado"
            and all(f.estado == "preparada" for f in banco.fold().fases),
            tope_pasos=700,
        )
        enc = banco.fold()
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual([f.estado for f in enc.fases], ["preparada"] * 3)
        self.assertEqual(len(banco.pasos_entrega("desplegado")), 3)
        omisiones = banco.pasos_entrega("deepseek_sin_revision")
        self.assertEqual(sorted(o["fase"] for o in omisiones), ["dos", "tres", "uno"])
        notas = [e["data"] for e in banco.eventos("nota_pr")]
        self.assertTrue(
            any("sin revisión de DeepSeek" in n.get("texto", "") for n in notas)
        )

    def test_un_bloqueante_real_bloquea_la_fase_y_el_encargo_termina_parcial(self):
        escenario = _escenario_tres_fases(False, self.tmp / "destino")
        escenario["exigido"] = dict(escenario["producto"])
        escenario["exigido"]["dos"] = {"saludo.txt": "hola\\nmundo\\nextra\\n"}
        escenario["titulo_bloqueo"] = "la fase dos no suma el extra pedido"
        banco = self.banco(escenario, gh_cls=_DeepSeekAprueba)
        self._semilla_receta()
        banco.correr(lambda: banco.fold().estado == "terminado", tope_pasos=700)
        enc = banco.fold()
        self.assertEqual(enc.estado, "terminado")
        estados = {f.id: f.estado for f in enc.fases}
        self.assertEqual(estados["uno"], "preparada")
        self.assertEqual(estados["dos"], "bloqueada")
        self.assertEqual(estados["tres"], "pendiente")
        bloqueos = banco.eventos("bloqueo_fase")
        self.assertEqual(len(bloqueos), 1)
        datos = bloqueos[0]["data"]
        self.assertTrue(datos.get("causa"))
        dos = next(f for f in enc.fases if f.id == "dos")
        self.assertTrue(dos.bloqueo_causa)
        self.assertTrue(dos.pendientes, "la fase bloqueada quedó sin sus pendientes")
        self.assertEqual(banco.eventos("cierre_forzado"), [])
        self.assertEqual(
            sorted(p["fase"] for p in banco.pasos_entrega("desplegado")), ["uno"]
        )
        con_pendientes = [
            p for p in self._prs(banco).values() if "Pendientes abiertos" in p.get("cuerpo", "")
        ]
        self.assertTrue(con_pendientes, "el PR de la fase bloqueada no trae sus pendientes")

    def test_una_fase_independiente_corre_aunque_otra_esta_bloqueada(self):
        escenario = _escenario_tres_fases(False, self.tmp / "destino")
        escenario["fases"][2]["requiere"] = ["uno"]
        escenario["exigido"] = dict(escenario["producto"])
        escenario["exigido"]["dos"] = {"saludo.txt": "hola\\nmundo\\nextra\\n"}
        escenario["titulo_bloqueo"] = "la fase dos no suma el extra pedido"
        escenario["comprobaciones"] = self._comprobaciones(
            {"uno": "hola", "dos": "mundo", "tres": "motor"}
        )
        banco = self.banco(escenario, gh_cls=_DeepSeekAprueba)
        self._semilla_receta()
        banco.correr(lambda: banco.fold().estado == "terminado", tope_pasos=700)
        enc = banco.fold()
        self.assertEqual(enc.estado, "terminado")
        estados = {f.id: f.estado for f in enc.fases}
        self.assertEqual(estados["uno"], "preparada")
        self.assertEqual(estados["dos"], "bloqueada")
        self.assertEqual(estados["tres"], "preparada")
        self.assertEqual(
            sorted(p["fase"] for p in banco.pasos_entrega("desplegado")),
            ["tres", "uno"],
        )
        self.assertEqual(banco.eventos("cierre_forzado"), [])

    def test_merge_prohibido_baja_el_nivel_efectivo_antes_del_primer_agente(self):
        banco = self.banco(
            _escenario_tres_fases(False, self.tmp / "destino"),
            config={"revisiones_requeridas": 1},
            gh_cls=_DeepSeekAprueba,
        )
        enc = banco.fold()
        self.assertEqual(enc.nivel_efectivo, "pr")
        self.assertEqual(enc.estado, "recibido")
        banco.correr(lambda: banco.fold().estado == "terminado")
        enc = banco.fold()
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(enc.nivel_efectivo, "pr")
        self.assertEqual(enc.nivel_alcanzado, "pr")
        self.assertEqual(banco.pasos_entrega("integrado"), [])
        self.assertEqual(banco.pasos_entrega("desplegado"), [])
        # Cada fase cierra en pr con su propio PR abierto: el merge y el
        # despliegue quedan prohibidos de punta a punta.
        prs = self._prs(banco)
        self.assertEqual(len(prs), 3)
        self.assertEqual([p["estado"] for p in prs.values()], ["abierto"] * 3)
        motivos = [e["data"].get("motivo") or "" for e in banco.eventos("nivel_efectivo")]
        self.assertTrue(motivos, "sin registro del nivel efectivo")
        self.assertTrue(any("aprobación" in m for m in motivos))

    def test_regresion_de_una_fase_hasta_pr_tambien_sin_deepseek(self):
        escenario = {
            "fases": [{"id": "uno", "resultado": "saludo.txt con hola"}],
            "producto": {"uno": {"saludo.txt": "hola\\n"}},
            "comprobaciones": self._comprobaciones({"uno": "hola"}),
        }
        banco = self.banco(escenario, nivel="pr")
        banco.correr(lambda: banco.fold().estado == "terminado", tope_pasos=600)
        enc = banco.fold()
        self.assertEqual(enc.estado, "terminado")
        self.assertEqual(enc.nivel_alcanzado, "pr")
        self.assertTrue(enc.pr_numero)
        self.assertEqual(banco.pasos_entrega("integrado"), [])
        self.assertEqual(banco.pasos_entrega("desplegado"), [])
        self.assertEqual([p["estado"] for p in self._prs(banco).values()], ["abierto"])
        notas = [e["data"] for e in banco.eventos("nota_pr")]
        self.assertTrue(
            any("sin revisión de DeepSeek" in n.get("texto", "") for n in notas)
        )


class ElPlazoTrasLaReceta(CompuertaReal):
    """La receta que termina se concilia en el plazo corto que dejó al
    lanzarse, no cinco minutos después."""

    def _hasta_que_termina_la_receta(self, receta_falla: bool) -> tuple[Banco, datetime]:
        escenario = {
            "fases": [{"id": "uno", "resultado": "saludo.txt con hola",
                       "requiere_despliegue": True}],
            "producto": {"uno": {"saludo.txt": "hola\\n"}},
        }
        banco = self.banco(escenario, gh_cls=_DeepSeekAprueba)
        self._semilla_receta()
        if receta_falla:
            semilla = self.tmp / "semilla"
            (semilla / "d.sh").write_text("exit 1\n", encoding="utf-8", newline="\n")
            _git(semilla, "add", "-A")
            _git(
                semilla, "-c", "user.name=t", "-c", "user.email=t@local",
                "commit", "-q", "-m", "receta que falla",
            )
            _git(semilla, "push", "-q", str(self.tmp / "repo.git"), "main")
        self._plazos_en_hora_logica(banco)
        banco.correr(lambda: bool(banco.pasos_entrega("integrado")))
        integrado = banco.hora
        fin = time.monotonic() + 60
        while (
            not any(r.get("estado") in ("aplicado", "fallido")
                    for r in banco.recibos_despliegue())
            or despliegue.observacion(banco.reg, banco.fold()) == "vivo"
        ):
            if time.monotonic() > fin:
                self.fail("la receta no terminó: " + json.dumps(banco.recibos_despliegue()))
            time.sleep(0.05)
        return banco, integrado

    def test_la_receta_aplicada_llega_a_desplegado_en_el_siguiente_plazo_corto(self):
        banco, integrado = self._hasta_que_termina_la_receta(receta_falla=False)
        banco.paso()
        self.assertTrue(
            banco.pasos_entrega("desplegado"),
            "la receta aplicada no se concilió en el barrido siguiente",
        )
        self.assertLess(
            banco.hora - integrado, timedelta(minutes=1),
            "el motor esperó de más tras la receta aplicada",
        )
        self.assertEqual(banco.fold().estado, "terminado")
        revisores = [
            e for e in banco.eventos("intento_abierto") if e["data"].get("rol") == "revisor"
        ]
        self.assertEqual(len(revisores), 1, "se relanzó al revisor de una aprobación vigente")

    def test_la_receta_fallida_entrega_la_fase_con_pendientes_sin_espera_larga(self):
        banco, integrado = self._hasta_que_termina_la_receta(receta_falla=True)
        banco.correr(lambda: bool(banco.eventos("bloqueo_fase")), tope_pasos=10)
        self.assertLess(
            banco.hora - integrado, timedelta(minutes=3),
            "la fase con la receta fallida esperó de más para entregarse con pendientes",
        )
        self.assertEqual(banco.pasos_entrega("desplegado"), [])


def _segundos(desde: str, hasta: str) -> float:
    return (
        datetime.fromisoformat(hasta.replace("Z", "+00:00"))
        - datetime.fromisoformat(desde.replace("Z", "+00:00"))
    ).total_seconds()


class ElPasoTrasIntegrarDiceSuResultado(CompuertaReal):
    """Tras integrar, el paso de entrega dice su resultado y su plazo; el
    barrido no los adivina con la espera de cinco minutos."""

    def test_la_fase_sin_declarar_su_despliegue_espera_con_el_plazo_largo(self):
        escenario = {
            "fases": [{"id": "uno", "resultado": "saludo.txt con hola"}],
            "producto": {"uno": {"saludo.txt": "hola\\n"}},
        }
        banco = self.banco(escenario, gh_cls=_DeepSeekAprueba)
        self._plazos_en_hora_logica(banco)
        banco.correr(lambda: bool(banco.pasos_entrega("integrado")))
        inicio, previos = banco.hora, len(banco.reg.eventos(banco.ident))
        intentos = len(banco.eventos("intento_abierto"))
        while banco.hora - inicio < timedelta(hours=1):
            banco.paso()
        nuevos = banco.reg.eventos(banco.ident)[previos:]
        esperas = [e["data"] for e in nuevos if e["kind"] == "espera"]
        self.assertTrue(esperas, "la fase sin declarar no quedó en espera")
        for espera in esperas:
            self.assertIn("falta declarar", espera["motivo"])
            self.assertGreaterEqual(
                _segundos(espera["hora"], espera["reanuda_en"]), 300,
                "la espera que nadie resuelve sola se vuelve a mirar cada 30 s",
            )
        self.assertLessEqual(len(nuevos), 24, "el registro creció de más en una hora")
        self.assertEqual(len(banco.eventos("intento_abierto")), intentos)

    def test_la_receta_arranca_en_el_barrido_siguiente_al_merge_de_otra_persona(self):
        escenario = {
            "fases": [{"id": "uno", "resultado": "saludo.txt con hola",
                       "requiere_despliegue": True}],
            "producto": {"uno": {"saludo.txt": "hola\\n"}},
        }
        banco = self.banco(escenario, gh_cls=_OtraPersonaIntegraDuranteLaCI)
        self._semilla_receta()
        self._plazos_en_hora_logica(banco)
        banco.correr(lambda: bool(banco.pasos_entrega("integrado")))
        self.assertTrue(banco.gh.integro_otra_persona)
        integrado = banco.hora
        banco.correr(lambda: bool(banco.recibos_despliegue()), tope_pasos=10)
        self.assertLess(
            banco.hora - integrado, timedelta(minutes=2),
            "la receta esperó cinco minutos tras el merge de otra persona",
        )

    def test_la_fase_que_la_base_ya_contiene_se_cierra_en_el_mismo_paso(self):  # no-roja-en-base: la corrida del banco muestra que la base y 00ceec2 ya cierran la fase aquí; la prueba fija ese resultado ante el hallazgo de DeepSeek
        banco = self.banco(
            _escenario_tres_fases(False, self.tmp / "destino"),
            nivel="integrado", gh_cls=_LaBaseYaTieneLaFaseDos,
        )
        self._plazos_en_hora_logica(banco)
        banco.correr(
            lambda: any(f.id == "dos" and f.estado == "preparada" for f in banco.fold().fases)
        )
        integrado, = [p for p in banco.pasos_entrega("integrado") if p["fase"] == "dos"]
        self.assertIn("ya contenida", integrado["evidencia"])
        self.assertEqual(
            integrado["hora"], _iso(banco.hora),
            "la fase integrada con otra todavía pendiente no se cerró en su paso",
        )
        self.assertEqual(
            [e["data"].get("fase") for e in banco.eventos("intento_abierto")
             if e["data"].get("rol") == "revisor"],
            ["uno", "dos"],
        )
        self.assertEqual(banco.fold().fases[2].estado, "pendiente")
