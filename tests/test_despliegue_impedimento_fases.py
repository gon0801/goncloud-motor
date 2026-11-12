"""El impedimento de un despliegue frena solo su fase y se resuelve con producción.

Puntos 1 y 2 del ajuste de David (12-nov): un despliegue fallido de una fase no
congela las fases independientes, y un despliegue aplicado que ya contiene el
SHA de la fase pendiente resuelve la causa con evidencia real.
"""

from dataclasses import replace
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import _verificar_impedimento
from motor.encargos.dominio import (
    Encargo,
    EntregaAutorizada,
    Fase,
    Impedimento,
    fuente_de_impedimento,
    impedimento_bloquea_trabajo,
)
from motor.encargos.registro import Registro

HORA = "2026-11-12T08:00:00+00:00"
CAUSA = (
    "despliegue sin verificar (salida 1); rollback salida 0; "
    "revisar el recibo antes de un nuevo despliegue"
)
CONDICION = "que el despliegue pueda completarse o se retire del pedido"
RECETA = {"desplegar": "d.sh", "checklist": "c.sh", "rollback": "r.sh"}


def _encargo(fases: tuple[Fase, ...], **kw) -> Encargo:
    base = {
        "estado": "recibido",
        "fase_activa": fases[-1].id if fases else "",
        "entrega": EntregaAutorizada(nivel="desplegado", repo="ejemplo/repo"),
        "intentos_nuevos": True,
    }
    base.update(kw)
    return Encargo(
        id="enc_prueba",
        clave="prueba",
        huella="huella",
        director="directo",
        texto="pedido de prueba",
        adjuntos=(),
        restricciones=(),
        canal="",
        fases=fases,
        **base,
    )


def _impedimento_despliegue() -> Impedimento:
    return Impedimento(
        clase="despliegue:1c6aec93fba6", causa=CAUSA, condicion=CONDICION,
    )


def _recibo_aplicado(base: Path, enc: Encargo, fase: Fase) -> Path:
    identidad = {
        "encargo": enc.id, "fase": fase.id, "repo": enc.entrega.repo,
        "sha": fase.integrado_sha, "receta": RECETA,
    }
    clave = hashlib.sha256(json.dumps(identidad, sort_keys=True).encode()).hexdigest()
    directorio = base / "despliegues" / clave
    directorio.mkdir(parents=True)
    recibo = directorio / "recibo.json"
    recibo.write_text(json.dumps({
        **identidad, "estado": "aplicado", "hora": HORA, "pasos": [
            {"paso": "desplegar", "script": RECETA["desplegar"], "codigo": 0,
             "stdout": "", "stderr": "", "enviado": True},
            {"paso": "checklist", "script": RECETA["checklist"], "codigo": 0,
             "stdout": "", "stderr": "", "enviado": True},
        ],
    }), encoding="utf-8")
    return recibo


def git(espacio: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(espacio), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


class ImpedimentoDespliegue(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.espacio = self.raiz / "repo"
        self.espacio.mkdir()
        git(self.espacio, "init", "-q", "-b", "main")

        def commit(mensaje):
            (self.espacio / "archivos").mkdir(exist_ok=True)
            (self.espacio / "archivos" / mensaje).write_text(mensaje, encoding="utf-8")
            git(self.espacio, "add", ".")
            git(self.espacio, "-c", "user.name=t", "-c", "user.email=t@local",
                "commit", "-qm", mensaje)
            return git(self.espacio, "rev-parse", "HEAD")

        self.commit = commit
        self.reg = Registro(self.raiz / "registro")
        self.addCleanup(self.reg.close)

    def test_el_impedimento_de_despliegue_no_frena_el_trabajo_de_otras_fases(self):
        # h_5f597cda: la causa de despliegue es de fase, no de instrumento.
        self.assertEqual(
            fuente_de_impedimento("despliegue:1c6aec93fba6"), "despliegue",
        )
        fases = (Fase(
            id="N", resultado="N", estado="bloqueada",
            bloqueo_causa=CAUSA, evidencia="recibo fallido",
        ),)
        enc = _encargo(fases=fases, impedimentos=(_impedimento_despliegue(),))
        self.assertFalse(impedimento_bloquea_trabajo(enc))
        # Una causa de instrumento sí frena: es la que impide producir.
        enc = replace(enc, impedimentos=(Impedimento(
            clase="comprobaciones.json", causa="falta", condicion="vuelva a validar",
        ),))
        self.assertTrue(impedimento_bloquea_trabajo(enc))

    def test_un_despliegue_que_cubre_el_sha_de_la_fase_resuelve_el_impedimento(self):
        # h_c3d8b5ae: el SHA desplegado contiene el SHA de la fase fallida.
        sha_n = self.commit("fase N")
        sha_o = self.commit("fase O, hija del SHA de N")
        fase_n = Fase(
            id="N", resultado="N", estado="bloqueada",
            requiere_despliegue=True, integrado_sha=sha_n,
            bloqueo_causa=CAUSA, evidencia="recibo fallido",
        )
        fase_o = Fase(
            id="O", resultado="O", estado="trabajo",
            requiere_despliegue=True, integrado_sha=sha_o, **RECETA,
        )
        enc = _encargo(fases=(fase_n, fase_o), fase_activa="O")
        recibo = _recibo_aplicado(self.reg.base, enc, fase_o)
        fase_o = replace(fase_o, desplegado_recibo=str(recibo))
        enc = replace(enc, fases=(fase_n, fase_o), impedimentos=(_impedimento_despliegue(),))
        self.assertIs(
            _verificar_impedimento("despliegue:1c6aec93fba6", enc, self.espacio,
                                   self.reg.base, self.reg),
            True,
        )

    def test_un_despliegue_en_otra_rama_no_resuelve_el_impedimento(self):
        sha_n = self.commit("fase N")
        git(self.espacio, "checkout", "-q", "--orphan", "otra")
        (self.espacio / "archivos").mkdir(exist_ok=True)
        (self.espacio / "archivos" / "ajena").write_text("ajena", encoding="utf-8")
        git(self.espacio, "add", ".")
        git(self.espacio, "-c", "user.name=t", "-c", "user.email=t@local",
            "commit", "-qm", "ajena")
        sha_x = git(self.espacio, "rev-parse", "HEAD")
        git(self.espacio, "checkout", "-q", "main")
        fase_n = Fase(
            id="N", resultado="N", estado="bloqueada",
            requiere_despliegue=True, integrado_sha=sha_n,
            bloqueo_causa=CAUSA, evidencia="recibo fallido",
        )
        fase_o = Fase(
            id="O", resultado="O", estado="trabajo",
            requiere_despliegue=True, integrado_sha=sha_x, **RECETA,
        )
        enc = _encargo(fases=(fase_n, fase_o), fase_activa="O")
        recibo = _recibo_aplicado(self.reg.base, enc, fase_o)
        fase_o = replace(fase_o, desplegado_recibo=str(recibo))
        enc = replace(enc, fases=(fase_n, fase_o), impedimentos=(_impedimento_despliegue(),))
        self.assertIs(
            _verificar_impedimento("despliegue:1c6aec93fba6", enc, self.espacio,
                                   self.reg.base, self.reg),
            False,
        )

    def test_el_reintento_que_despliega_la_propia_fase_resuelve_el_impedimento(self):
        sha_n = self.commit("fase N")
        fase_n = Fase(
            id="N", resultado="N", estado="trabajo",
            requiere_despliegue=True, integrado_sha=sha_n, **RECETA,
        )
        enc = _encargo(fases=(fase_n,), fase_activa="N")
        enc = replace(enc, impedimentos=(_impedimento_despliegue(),))
        self.assertIs(
            _verificar_impedimento("despliegue:1c6aec93fba6", enc, self.espacio,
                                   self.reg.base, self.reg),
            False,
        )
        recibo = _recibo_aplicado(self.reg.base, enc, fase_n)
        fase_n = replace(fase_n, desplegado_recibo=str(recibo))
        enc = replace(enc, fases=(fase_n,))
        self.assertIs(
            _verificar_impedimento("despliegue:1c6aec93fba6", enc, self.espacio,
                                   self.reg.base, self.reg),
            True,
        )
