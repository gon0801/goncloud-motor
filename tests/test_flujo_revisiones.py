"""Recorridos del flujo de revisiones: aprobacion con observacion,
correccion acotada, instrumento sin rehacer, sin progreso y nivel pr."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import (
    _anotar_instrumento,
    _incorporar,
    _paso_entrega,
    ahora_iso,
    barrer,
    corregir,
    marcar_espera,
    marcar_pregunta,
    ordenar,
    recibir,
)
from motor.encargos.comprobacion import (
    firma_producto,
    hallazgos_deepseek,
    validar_juicio,
)
from motor.encargos.dominio import (
    AlcanceRevision,
    Hallazgo,
    TransicionIlegal,
    alcance_de,
    bloqueantes_abiertos,
    decision_proceso,
    hubo_avance,
    reducir,
    vista,
)
from motor.encargos.registro import Registro
from motor.flota.runtime import exigir_poteto
from motor.github import FakeGitHub, GhGitHub


class Recorrido:
    """Harness sin LLM: el test hace de agente entre incorporaciones."""

    def __init__(self, tmp: str, clave: str, texto: str, **kw):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        self.hora = ahora_iso()
        self.enc = recibir(self.reg, texto, clave, self.hora, **kw)["encargo"]
        self.espacio = self.base / "espacio" / self.enc
        self.espacio.mkdir(parents=True, exist_ok=True)
        self.d = self.base / "ejecucion" / self.enc
        self.d.mkdir(parents=True, exist_ok=True)
        self.resultado = self.d / "resultado.json"

    def fold(self):
        return self.reg.fold(self.enc)

    def incorporar(self):
        e = self.fold()
        n = e.generacion + 1
        self.reg.anotar(
            self.enc,
            {
                "kind": "reserva",
                "data": {"rol": e.rol or "responsable", "generacion": n},
            },
            f"t-reserva:{n}",
        )
        self.reg.anotar(
            self.enc, {"kind": "reserva_cerrada", "data": {}}, f"t-cerrada:{n}"
        )
        self.reg.anotar(
            self.enc,
            {
                "kind": "estrategia",
                "data": {
                    "rol": e.rol or "responsable",
                    "estrategia": e.estrategia,
                    "motivo": e.motivo,
                    "siguiente": e.siguiente,
                },
            },
            f"t-rol:{n}",
        )
        marca = self.d / "incorporado"
        marca.unlink(missing_ok=True)
        previa = (
            (self.d / "observaciones.json").read_text()
            if (self.d / "observaciones.json").is_file()
            else None
        )
        salida = _incorporar(self.reg, self.fold(), self.espacio, self.hora, self.d)
        if not marca.is_file() and (
            self.espacio / ".interno" / "comprobaciones.json"
        ).is_file():
            fin = time.time() + 15
            while time.time() < fin:
                p = self.d / "observaciones.json"
                if p.is_file() and p.read_text() != previa:
                    break
                time.sleep(0.05)
            salida = _incorporar(
                self.reg, self.fold(), self.espacio, self.hora, self.d
            )
        return salida

    def juicio(self, datos: dict) -> None:
        (self.d / "revision.json").write_text(
            json.dumps(
                {
                    "observaciones": str(self.d / "observaciones.json"),
                    "resultado": str(self.resultado),
                    "version": firma_producto(self.espacio),
                }
            )
        )
        datos.setdefault("version", firma_producto(self.espacio))
        self.resultado.write_text(json.dumps(datos))

    def eventos(self, kind: str) -> list[dict]:
        return [e for e in self.reg.eventos(self.enc) if e["kind"] == kind]


def _interno(r: Recorrido) -> Path:
    p = r.espacio / ".interno"
    p.mkdir(exist_ok=True)
    return p


def _lanzaria(e) -> bool:
    return decision_proceso(e, "ausente", None, False, True, e.hora)["lanzar"]


def _bloqueante(titulo: str, repro: str = "cat malo.txt") -> dict:
    return {
        "criterio": "comportamiento",
        "titulo": titulo,
        "reproduccion": repro,
        "ancla": "malo.txt:1",
    }


class _GhConDeepSeek(FakeGitHub):
    """DeepSeek pide cambios con los bloqueantes de `titulos`, en cada SHA."""

    titulos = ("Fuga de token en el log",)

    def comentarios_deepseek(self, repo, numero, sha=""):
        if not self.titulos:
            return [{"texto": "DeepSeek: APPROVED", "autor": "DeepSeek", "sha": sha}]
        hallazgos = [
            {"id": f"ds_{t}", "severity": "high", "title": t, "file": "saludo.txt"}
            for t in self.titulos
        ]
        texto = "DeepSeek: CHANGES_REQUESTED\nai-review:findings=" + json.dumps(hallazgos)
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}]


def _contiene(letra: str) -> str:
    """Check que pasa solo si saludo.txt contiene `letra`."""
    return (
        f'"{sys.executable}" -c "import sys; '
        f"sys.exit(0 if '{letra}' in open('saludo.txt').read() else 1)\""
    )


def _entrega_pr(caso,tmp: str, clave: str, gh_cls, config: dict | None = None):
    """Encargo de nivel pr sobre un repo bare real. `vuelta(contenido)` hace
    de implementador y de revisor que aprueba, y devuelve el encargo tras el
    paso de entrega contra el GitHub falso."""
    from motor.encargos.ciclo import _asegurar_repo

    raiz = Path(tmp)
    bare = raiz / "repo.git"
    semilla = raiz / "semilla"
    semilla.mkdir()
    (semilla / "base.txt").write_text("base\n")
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", "--quiet", str(bare)], check=True
    )
    for args in (
        ["init", "-b", "main", "--quiet"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@l", "commit", "--quiet", "-m", "base"],
        ["push", "--quiet", str(bare), "main"],
    ):
        subprocess.run(["git", "-C", str(semilla), *args], check=True)
    r = Recorrido(
        tmp, clave, "agrega saludo.txt con hola",
        entrega_nivel="pr", repo=str(bare), base_rama="main",
    )
    caso.addCleanup(r.reg.close)
    caso.assertEqual(_asegurar_repo(r.fold(), r.espacio), "")
    falso = raiz / "fake"
    falso.mkdir()
    if config:
        (falso / "repos.json").write_text(json.dumps({str(bare): config}))
    gh = gh_cls(falso)

    def vuelta(contenido: str):
        (r.espacio / "saludo.txt").write_text(contenido)
        r.incorporar()
        r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
        r.incorporar()
        return _paso_entrega(r.reg, r.fold(), gh, ahora_iso())

    vuelta.gh = gh
    return vuelta, r


class FlujoRevisiones(unittest.TestCase):
    def test_un_archivo_con_observacion_sin_vuelta_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "saludo-1", "Crea saludo.txt con el texto exacto: hola, motor")
            self.addCleanup(r.reg.close)
            (r.espacio / "saludo.txt").write_text("hola, motor")
            (_interno(r) / "contexto.md").write_text("creado el archivo")
            (_interno(r) / "evidencia.txt").write_text("saludo.txt contiene: hola, motor")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            self.assertEqual(
                [x for x, _ in e.producto.manifiesto], ["saludo.txt"]
            )
            self.assertFalse((r.espacio / "procedimiento.json").exists())
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei saludo.txt; su contenido es el texto exacto pedido",
                    "observaciones": ["sin salto final"],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "terminado")
            self.assertEqual(len(r.eventos("revision")), 1)
            v = vista(e)
            self.assertEqual(v["producto"]["archivos"], ["saludo.txt"])
            self.assertEqual(
                v["revision"]["observaciones"], ["sin salto final"]
            )
            self.assertEqual(v["entrega"]["nivel_alcanzado"], "terminado")

    def test_correccion_acotada_conserva_lo_aprobado(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "corr-1", "bueno.txt con A y malo.txt con B")
            self.addCleanup(r.reg.close)
            bueno = r.espacio / "bueno.txt"
            malo = r.espacio / "malo.txt"
            bueno.write_text("A")
            malo.write_text("X")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei ambos archivos",
                    "bloqueantes": [
                        {
                            "criterio": "comportamiento",
                            "titulo": "malo.txt no contiene B",
                            "reproduccion": "cat malo.txt -> X",
                            "ancla": "malo.txt:1",
                        }
                    ],
                    "aprobadas": ["bueno.txt"],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.rol, "responsable")
            abiertos = bloqueantes_abiertos(e)
            self.assertEqual(len(abiertos), 1)
            hid = abiertos[0].id
            malo.write_text("B")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            alcance = alcance_de(e)
            self.assertEqual(alcance.clase, "correccion")
            self.assertEqual(alcance.tocadas, ("malo.txt",))
            self.assertEqual(alcance.conservadas, ("bueno.txt",))
            self.assertEqual([h.id for h in alcance.abiertos], [hid])
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "cat malo.txt -> B; bueno.txt intacto y conservado",
                    "resueltos": [hid],
                    "aprobadas": ["malo.txt"],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(bueno.read_text(), "A")
            self.assertIn(("bueno.txt", e.aprobadas[0][1]), tuple(e.aprobadas))

    def test_juicio_roto_es_instrumento_y_no_rehace(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "inst-1", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("42")
            r.incorporar()
            firma = firma_producto(r.espacio)
            (r.d / "revision.json").write_text(
                json.dumps(
                    {
                        "observaciones": str(r.d / "observaciones.json"),
                        "resultado": str(r.resultado),
                        "version": firma,
                    }
                )
            )
            r.resultado.write_text("{esto no es json")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            self.assertEqual(r.eventos("revision"), [])
            fallos = r.eventos("instrumento_fallo")
            self.assertTrue(any(f["data"]["fuente"] == "juicio" for f in fallos))
            self.assertEqual(firma_producto(r.espacio), firma)
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei dato.txt; contiene 42",
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estado, "terminado")

    def test_argv_roto_e_invalido_son_instrumento(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "inst-2", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("42")
            interno = _interno(r)
            (interno / "comprobaciones.json").write_text(
                json.dumps(
                    {
                        "comprobaciones": [
                            {
                                "objetivo": "el dato es 42",
                                "argv": ["python3", "-c", "def roto(:"],
                            }
                        ]
                    }
                )
            )
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            firma = firma_producto(r.espacio)
            r.juicio(
                {
                    "veredicto": "no_concluyente",
                    "evidencia": "el -c declarado tiene un SyntaxError; el producto no se examino",
                    "instrumento": {"causa": "SyntaxError en el comando de comprobacion"},
                }
            )
            e = r.incorporar()
            self.assertEqual(e.rol, "responsable")
            self.assertEqual(e.estrategia, "reparar_instrumento")
            self.assertEqual(r.eventos("revision"), [])
            self.assertEqual(firma_producto(r.espacio), firma)
            (interno / "comprobaciones.json").write_text("{roto")
            e = r.incorporar()
            self.assertNotEqual(e.rol, "revisor")
            fallos = r.eventos("instrumento_fallo")
            self.assertTrue(
                any(f["data"]["fuente"] == "comprobacion" for f in fallos)
            )

    def test_sin_progreso_diagnostica_y_luego_pide_al_usuario(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "sp-1", "malo.txt con B")
            self.addCleanup(r.reg.close)
            malo = r.espacio / "malo.txt"
            malo.write_text("X")

            def vuelta_rechazo():
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "cambios",
                        "evidencia": "lei malo.txt",
                        "bloqueantes": [
                            {
                                "criterio": "comportamiento",
                                "titulo": "malo.txt no contiene B",
                                "reproduccion": "cat malo.txt -> X",
                                "ancla": "malo.txt:1",
                            }
                        ],
                    }
                )
                return r.incorporar()

            e = vuelta_rechazo()
            self.assertEqual(e.estrategia, "investigar")
            e = vuelta_rechazo()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            e = vuelta_rechazo()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(e.preguntas)
            self.assertEqual(malo.read_text(), "X")

    def test_cambiar_archivos_con_el_mismo_bloqueante_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sp-3", "malo.txt con B")
            self.addCleanup(r.reg.close)
            malo = r.espacio / "malo.txt"

            def vuelta(i: int):
                malo.write_text(f"intento-{i}")
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "cambios",
                        "evidencia": f"cat malo.txt -> intento-{i}",
                        "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                    }
                )
                return r.incorporar()

            e = vuelta(1)
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            e = vuelta(2)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertTrue(_lanzaria(e))
            e = vuelta(3)
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("malo.txt no contiene B", e.motivo)
            self.assertIn("Se intento", e.motivo)
            self.assertTrue(e.preguntas)
            self.assertFalse(_lanzaria(e))

    def test_bloqueantes_nuevos_sin_cerrar_ninguno_escalan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sp-4", "malo.txt con B")
            self.addCleanup(r.reg.close)
            malo = r.espacio / "malo.txt"

            def vuelta(i: int):
                malo.write_text(f"intento-{i}")
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "cambios",
                        "evidencia": f"cat malo.txt -> intento-{i}",
                        "bloqueantes": [_bloqueante(f"malo.txt falla en el paso {i}")],
                    }
                )
                return r.incorporar()

            e = vuelta(1)
            self.assertEqual(e.estrategia, "investigar")
            e = vuelta(2)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            e = vuelta(3)
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertFalse(_lanzaria(e))

    def test_revisor_que_no_concluye_dos_vueltas_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "nc-1", "dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("41")

            def vuelta(i: int):
                e = r.incorporar()
                self.assertEqual(e.rol, "revisor")
                r.juicio(
                    {
                        "veredicto": "no_concluyente",
                        "evidencia": f"no pude decidir, vuelta {i}",
                    }
                )
                return r.incorporar()

            e = vuelta(1)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertTrue(_lanzaria(e))
            e = vuelta(2)
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_aprobar_corta_la_racha_y_la_fase_nueva_empieza_en_cero(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "nc-3", "a.txt con A y b.txt con B")
            self.addCleanup(r.reg.close)
            interno = _interno(r)
            (interno / "fases.json").write_text(
                json.dumps(
                    {
                        "fases": [
                            {"id": "a", "resultado": "a.txt con A"},
                            {"id": "b", "resultado": "b.txt con B", "requiere": ["a"]},
                        ]
                    }
                )
            )
            (r.espacio / "a.txt").write_text("A")
            r.incorporar()
            r.juicio({"veredicto": "no_concluyente", "evidencia": "duda en a"})
            e = r.incorporar()
            self.assertEqual(e.fase_activa, "a")
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "cat a.txt -> A"})
            e = r.incorporar()
            self.assertEqual(e.fase_activa, "b")
            self.assertEqual(e.sin_progreso, 0)
            (r.espacio / "b.txt").write_text("B")
            r.incorporar()
            r.juicio({"veredicto": "no_concluyente", "evidencia": "primera duda en b"})
            e = r.incorporar()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertTrue(_lanzaria(e))

    def test_no_concluyente_con_el_primer_bloqueante_es_avance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "nc-4", "malo.txt con B")
            self.addCleanup(r.reg.close)
            malo = r.espacio / "malo.txt"

            def vuelta(i: int, bloqueantes: list):
                malo.write_text(f"intento-{i}")
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "no_concluyente",
                        "evidencia": f"cat malo.txt -> intento-{i}, falta un dato",
                        "bloqueantes": bloqueantes,
                    }
                )
                return r.incorporar()

            e = vuelta(1, [_bloqueante("malo.txt no contiene B")])
            self.assertTrue(bloqueantes_abiertos(e))
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertTrue(_lanzaria(e))
            e = vuelta(2, [])
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            e = vuelta(3, [])
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("malo.txt no contiene B", e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_no_concluyente_que_cierra_un_bloqueante_es_avance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "nc-2", "malo.txt con B")
            self.addCleanup(r.reg.close)
            malo = r.espacio / "malo.txt"
            malo.write_text("X")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "cat malo.txt -> X",
                    "bloqueantes": [_bloqueante("malo.txt no contiene B")],
                }
            )
            e = r.incorporar()
            hid = bloqueantes_abiertos(e)[0].id
            for i in range(3):
                malo.write_text(f"B{i}")
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "no_concluyente",
                        "evidencia": f"cat malo.txt -> B{i}, falta un dato",
                        "resueltos": [hid] if i == 0 else [],
                    }
                )
                e = r.incorporar()
                if i == 0:
                    self.assertEqual(bloqueantes_abiertos(e), ())
                    self.assertEqual(e.estrategia, "investigar")
                    self.assertNotEqual(e.estado, "necesita_usuario")
                    self.assertTrue(_lanzaria(e))
                elif i == 1:
                    self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
                else:
                    self.assertEqual(e.estado, "necesita_usuario")

    def test_checks_en_rojo_dos_vueltas_seguidas_escalan(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ci-1", FakeGitHub,
                {"requeridos": ["ci"], "checks": {"ci": "exit 1"}},
            )
            e = vuelta("uno")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            e = vuelta("dos")
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            e = vuelta("tres")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("checks requeridos en rojo: ci", e.motivo)
            self.assertIn("Se intento", e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_deepseek_con_el_mismo_bloqueante_dos_vueltas_seguidas_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-1", _GhConDeepSeek)
            e = vuelta("uno")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            e = vuelta("dos")
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            e = vuelta("tres")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("DeepSeek", e.motivo)
            self.assertIn("Se intento", e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_deepseek_que_cierra_un_bloqueante_y_trae_otro_es_avance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-2", _GhConDeepSeek)
            e = vuelta("uno")
            self.assertEqual(e.estrategia, "investigar")
            for i in range(4):
                vuelta.gh.titulos = (f"Falla {i} en el log",)
                e = vuelta(f"v{i}")
                self.assertEqual(e.estrategia, "investigar")
                self.assertEqual(e.sin_progreso, 0)
                self.assertNotEqual(e.estado, "necesita_usuario")
                self.assertTrue(_lanzaria(e))

    def test_check_que_vuelve_a_rojo_con_deepseek_abierto_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ci-3", _GhConDeepSeek,
                {"requeridos": ["ci"], "checks": {"ci": _contiene("A")}},
            )
            e = vuelta("A1")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            e = vuelta("A2")
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            e = vuelta("x3")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("checks requeridos en rojo: ci", e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_check_que_alterna_rojo_y_verde_no_es_avance_cada_vez(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ci-4", _GhConDeepSeek,
                {"requeridos": ["ci"], "checks": {"ci": _contiene("A")}},
            )
            e = vuelta("x1")
            self.assertEqual(e.estrategia, "investigar")
            e = vuelta("A2")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            e = vuelta("x3")
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            e = vuelta("A4")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertFalse(_lanzaria(e))

    def test_cerrar_un_check_en_rojo_es_avance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ci-2", FakeGitHub,
                {
                    "requeridos": ["a", "b"],
                    "checks": {"a": _contiene("A"), "b": _contiene("B")},
                },
            )
            e = vuelta("x")
            self.assertEqual(e.estrategia, "investigar")
            e = vuelta("A")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            self.assertNotEqual(e.estado, "necesita_usuario")
            e = vuelta("A2")
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)

    def test_rondas_con_progreso_sin_tope(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "sp-2", "malo.txt con B")
            self.addCleanup(r.reg.close)
            malo = r.espacio / "malo.txt"
            cerrados: list[str] = []
            for i in range(5):
                malo.write_text(f"intento-{i}")
                r.incorporar()
                r.juicio(
                    {
                        "veredicto": "cambios",
                        "evidencia": f"lei malo.txt vuelta {i}",
                        "resueltos": cerrados,
                        "bloqueantes": [
                            _bloqueante(
                                f"malo.txt falla en el paso {i}",
                                f"cat malo.txt -> intento-{i}",
                            )
                        ],
                    }
                )
                e = r.incorporar()
                self.assertNotEqual(e.estado, "necesita_usuario")
                self.assertEqual(e.sin_progreso, 0)
                self.assertTrue(_lanzaria(e))
                cerrados = [h.id for h in bloqueantes_abiertos(e, "independiente")]
            malo.write_text("B")
            r.incorporar()
            hid = bloqueantes_abiertos(r.fold())[0].id
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "cat malo.txt -> B",
                    "resueltos": [hid],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estado, "terminado")

    def test_nivel_pr_con_github_falso_hasta_deepseek(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            bare = raiz / "repo.git"
            subprocess.run(
                ["git", "init", "--bare", "-b", "main", "--quiet", str(bare)],
                check=True,
            )
            semilla = raiz / "semilla"
            subprocess.run(
                ["git", "clone", "--quiet", str(bare), str(semilla)], check=True
            )
            (semilla / "base.txt").write_text("base\n")
            for args in (
                ["add", "-A"],
                [
                    "-c", "user.name=t", "-c", "user.email=t@l",
                    "commit", "--quiet", "-m", "base",
                ],
                ["push", "--quiet", "origin", "main"],
            ):
                subprocess.run(["git", "-C", str(semilla), *args], check=True)
            r = Recorrido(
                tmp,
                "pr-1",
                "agrega saludo.txt con hola",
                entrega_nivel="pr",
                repo=str(bare),
                base_rama="main",
            )
            self.addCleanup(r.reg.close)
            from motor.encargos.ciclo import _asegurar_repo

            self.assertEqual(_asegurar_repo(r.fold(), r.espacio), "")
            (r.espacio / "saludo.txt").write_text("hola")
            (_interno(r) / "evidencia.txt").write_text("lei saludo.txt: hola")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei saludo.txt; contiene hola",
                }
            )
            e = r.incorporar()
            self.assertNotEqual(e.estado, "terminado")
            lanzamientos = []
            gh = FakeGitHub(raiz / "fake")

            def lanzador(enc, prompt, espacio, sesion):
                lanzamientos.append(enc.id)
                return None

            barrer(r.reg, ahora_iso(), time.time(), lanzador, gh=gh)
            e = r.fold()
            self.assertEqual(e.nivel_alcanzado, "pr_abierto")
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertTrue(e.pr_numero)
            self.assertTrue(e.pr_sha)
            self.assertEqual(lanzamientos, [])
            estado_fake = json.loads((raiz / "fake" / "github.json").read_text())
            pr = next(iter(estado_fake["prs"].values()))
            self.assertFalse(pr["borrador"])
            hora_pr = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora_pr + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            barrer(r.reg, tarde, time.time(), lanzador, gh=gh)
            e = r.fold()
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertFalse(
                any(
                    "DeepSeek no reviso" in f["data"].get("causa", "")
                    for f in r.eventos("instrumento_fallo")
                )
            )
            pr["comentarios_deepseek"] = [
                {"texto": "Revision sin problemas.", "sha": e.pr_sha}
            ]
            (raiz / "fake" / "github.json").write_text(json.dumps(estado_fake))
            barrer(r.reg, e.reanuda_en, time.time(), lanzador, gh=gh)
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(e.deepseek_sha, e.pr_sha)
            self.assertEqual(lanzamientos, [])
            v = vista(e)
            self.assertEqual(v["entrega"]["nivel_autorizado"], "pr")
            self.assertEqual(v["entrega"]["pr"]["numero"], e.pr_numero)

    def test_fold_historico_directa_con_nivel_externo_se_rechaza(self):
        evento = {
            "kind": "entrega",
            "data": {
                "id": "enc_viejo",
                "clave": "kv",
                "huella": "h",
                "texto": "t",
                "via": "directa",
                "entrega_nivel": "pr",
                "repo": "o/r",
                "base": "main",
                "hora": "2026-10-28T00:00:00Z",
            },
        }
        with self.assertRaisesRegex(
            TransicionIlegal, "la via directa no entrega niveles externos"
        ):
            reducir(None, evento)

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            payload = json.dumps(evento["data"], ensure_ascii=False, sort_keys=True)
            reg.db.execute(
                "INSERT INTO events (ts, encargo, kind, data, ukey) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    evento["data"]["hora"],
                    "enc_viejo",
                    "entrega",
                    payload,
                    "entrega:historica-directa-pr",
                ),
            )
            with self.assertRaisesRegex(
                TransicionIlegal, "la via directa no entrega niveles externos"
            ):
                reg.fold("enc_viejo")


class CorreccionesRevisionFlujo(unittest.TestCase):
    def test_bloqueante_no_desaparece_al_redeclararlo_sin_reproduccion(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "corr-bloqueo", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            dato = r.espacio / "dato.txt"
            dato.write_text("99")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei dato.txt; contiene 99 y el pedido exige 42",
                    "bloqueantes": [
                        {
                            "criterio": "comportamiento",
                            "titulo": "dato.txt no contiene 42",
                            "reproduccion": "cat dato.txt -> 99",
                            "ancla": "dato.txt:1",
                        }
                    ],
                }
            )
            e = r.incorporar()
            hid = bloqueantes_abiertos(e)[0].id
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei dato.txt; contiene 99",
                    "bloqueantes": [
                        {"id": hid, "titulo": "dato.txt no contiene 42"}
                    ],
                    "aprobadas": ["dato.txt"],
                }
            )
            e = r.incorporar()
            self.assertNotEqual(e.estado, "terminado")
            self.assertIn(hid, [h.id for h in bloqueantes_abiertos(e)])
            self.assertEqual(dato.read_text(), "99")

    def test_solo_observacion_de_estilo_aprueba_sin_devolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "corr-estilo", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("42")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei dato.txt y contiene 42",
                    "bloqueantes": [
                        {
                            "criterio": "estilo",
                            "titulo": "falta un parentesis final",
                            "reproduccion": "inspeccion visual",
                            "ancla": "dato.txt:1",
                        }
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(bloqueantes_abiertos(e), ())
            self.assertEqual(vista(e)["revision"]["veredicto"], "aprobado")
            self.assertIn(
                "falta un parentesis final",
                vista(e)["revision"]["observaciones"],
            )

    def test_ampliacion_causal_valida_conserva_el_bloqueante(self):
        abierto = Hallazgo(
            id="h_delete",
            gravedad="bloqueante",
            criterio="comportamiento",
            titulo="delete.py usa la raiz anterior",
            reproduccion="leer la raiz efectiva",
            ancla="delete.py:1",
        )
        alcance = AlcanceRevision(
            clase="correccion",
            version="v2",
            base_version="v1",
            abiertos=(abierto,),
            tocadas=("config.py",),
            conservadas=("delete.py",),
            manifiesto=(("config.py", "nuevo"), ("delete.py", "igual")),
        )
        juicio = validar_juicio(
            {
                "veredicto": "cambios",
                "version": "v2",
                "base_version": "v1",
                "evidencia": "config.py cambia la raiz que consume delete.py",
                "bloqueantes": [
                    {
                        "id": "h_delete",
                        "criterio": "comportamiento",
                        "titulo": "delete.py usa la raiz anterior",
                        "reproduccion": "ejecutar el borrado y observar la raiz",
                        "ancla": "delete.py:1",
                        "ampliacion": (
                            "config.py cambia la raiz y delete.py la consume; "
                            "delete.py no cambio, pero el efecto causal llega ahi"
                        ),
                    }
                ],
            },
            alcance,
        )
        self.assertEqual(juicio.veredicto, "cambios")
        self.assertEqual(
            [h.id for h in juicio.hallazgos if h.gravedad == "bloqueante"],
            ["h_delete"],
        )

    def test_deepseek_interpreta_senales_y_hallazgos_estructurados(self):
        texto = (
            "DeepSeek: CHANGES_REQUESTED\n"
            "[P1] Fuga de token en el log. Repro: arrancar y leer el log."
        )
        hallazgos = hallazgos_deepseek([{"texto": texto}], "abc")
        self.assertEqual(hallazgos[0].gravedad, "bloqueante")

        body = (
            "ai-review \u203a findings\n"
            '<!-- ai-review:findings=[{"id":"F-7","severity":"High",'
            '"file":"seguridad.py","line":8,"state":"open",'
            '"title":"Token expuesto en el log"}] -->'
        )
        hallazgos = hallazgos_deepseek([{"texto": body}], "abc")
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0].gravedad, "bloqueante")
        self.assertEqual(hallazgos[0].criterio, "seguridad")
        self.assertEqual(hallazgos[0].ancla, "seguridad.py:8")

        body_real = (
            '<!-- ai-review:findings=[{"id":"F-8","severity":"Critical",'
            '"files":["seguridad.py","otro.py"],"line":9,"state":"open",'
            '"title":"Credencial publicada en el log"}] -->'
        )
        hallazgos = hallazgos_deepseek([{"texto": body_real}], "abc")
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0].gravedad, "bloqueante")
        self.assertEqual(hallazgos[0].ancla, "seguridad.py:9")

        body_schema3 = (
            '<!-- ai-review:findings={"schema":3,"completion":"complete",'
            '"findings":[{"id":"F-9","severity":"High",'
            '"file":"seguridad.py","line":10,"state":"open",'
            '"title":"Token publicado"}]} -->'
        )
        hallazgos = hallazgos_deepseek([{"texto": body_schema3}], "abc")
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0].gravedad, "bloqueante")
        self.assertEqual(hallazgos[0].ancla, "seguridad.py:10")

    def test_comentarios_deepseek_solo_acepta_la_integracion_real(self):
        sha = "a" * 40
        filas = [
            {
                "body": (
                    "DeepSeek: revision sin problemas. "
                    f"<!-- ai-review:sha={sha} -->"
                ),
                "user": {"login": "untrusted-contributor"},
            },
            {
                "body": (
                    "<!-- ai-review:sticky -->\n"
                    f"<!-- ai-review:sha={sha} -->\n"
                    "Revision de DeepSeek sin problemas."
                ),
                "user": {"login": "github-actions[bot]"},
            },
        ]
        gh = GhGitHub(env={})

        def responder(*args):
            return json.dumps(filas) if "/issues/" in args[1] else "[]"

        gh._gh = responder
        comentarios = gh.comentarios_deepseek("o/r", 1, sha)
        self.assertEqual(len(comentarios), 1)
        self.assertEqual(comentarios[0]["autor"], "github-actions[bot]")

    def test_tres_fallos_de_instrumento_no_escalan_por_conteo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "corr-inst", "Crea dato.txt")
            self.addCleanup(r.reg.close)
            for i in range(3):
                _anotar_instrumento(
                    r.reg,
                    r.fold(),
                    "juicio",
                    "Error: respuesta no valida",
                    "reintentar el mismo paso",
                    r.hora,
                    f"repetido-{i}",
                )
            e = r.fold()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertFalse(
                any(ev["data"].get("escalar") for ev in r.eventos("sin_progreso"))
            )

    def test_cuota_no_borra_la_sesion_ni_su_contexto(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "corr-cuota", "Conservar trabajo")
            self.addCleanup(r.reg.close)
            r.reg.anotar(
                r.enc,
                {
                    "kind": "intento_cerrado",
                    "data": {"sesion_runtime": "sesion-valida"},
                },
                "sesion-inicial",
            )
            intento = r.base / "intento-cuota"
            intento.mkdir()
            cache = (
                r.base
                / "runtime"
                / r.enc
                / "sesion-responsable"
                / ".codex"
            )
            cache.mkdir(parents=True)
            (cache / "historial").write_text("contexto valido")
            (intento / "arranque.json").write_text(
                json.dumps({"sesion_modo": "reanudada", "agente": "codex"})
            )
            (intento / "outcome.json").write_text(
                json.dumps(
                    {
                        "fallo_runtime": (
                            "429 rate limit exceeded Retry-After: 3600"
                        )
                    }
                )
            )
            e = _incorporar(
                r.reg, r.fold(), r.espacio, ahora_iso(), intento
            )
            self.assertEqual(e.sesion_responsable, "sesion-valida")
            self.assertTrue((cache / "historial").is_file())
            self.assertTrue(r.eventos("proveedor_fallo"))
            self.assertFalse(r.eventos("sesion_perdida"))

    def test_producto_cambiado_tras_aprobar_no_se_publica(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            bare = raiz / "repo.git"
            subprocess.run(
                ["git", "init", "--bare", "-b", "main", "--quiet", str(bare)],
                check=True,
            )
            semilla = raiz / "semilla"
            subprocess.run(
                ["git", "clone", "--quiet", str(bare), str(semilla)], check=True
            )
            (semilla / "base.txt").write_text("base\n")
            for args in (
                ["add", "-A"],
                [
                    "-c", "user.name=t", "-c", "user.email=t@l",
                    "commit", "--quiet", "-m", "base",
                ],
                ["push", "--quiet", "origin", "main"],
            ):
                subprocess.run(["git", "-C", str(semilla), *args], check=True)
            r = Recorrido(
                tmp,
                "corr-stale",
                "agrega saludo.txt con hola",
                entrega_nivel="pr",
                repo=str(bare),
                base_rama="main",
            )
            self.addCleanup(r.reg.close)
            from motor.encargos.ciclo import _asegurar_repo

            self.assertEqual(_asegurar_repo(r.fold(), r.espacio), "")
            saludo = r.espacio / "saludo.txt"
            saludo.write_text("hola")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei saludo.txt; contiene hola",
                }
            )
            r.incorporar()
            saludo.write_text("NO REVISADO")
            lanzamientos = []
            gh = FakeGitHub(raiz / "fake")

            def lanzador(enc, prompt, espacio, sesion):
                lanzamientos.append(enc.id)
                return None

            barrer(r.reg, ahora_iso(), time.time(), lanzador, gh=gh)
            e = r.fold()
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.pr_numero, 0)
            estado_fake = json.loads((raiz / "fake" / "github.json").read_text())
            self.assertEqual(estado_fake["prs"], {})

    def test_inspeccion_pendiente_pasa_por_el_puente_y_no_repite(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "insp-1", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("42")
            r.incorporar()
            lanzamientos = []

            def lanzador(enc, prompt, espacio, sesion):
                lanzamientos.append(
                    (enc.rol, Path(prompt).read_text(encoding="utf-8"))
                )
                return None

            with mock.patch(
                "motor.encargos.aislamiento.sondear",
                return_value=SondaEntorno(True),
            ):
                for _ in range(2):
                    r.juicio(
                        {
                            "veredicto": "no_concluyente",
                            "evidencia": "falta acceso a la fuente externa",
                            "inspeccion_faltante": "consultar metricas de cuenta",
                        }
                    )
                    r.incorporar()
                    barrer(r.reg, ahora_iso(), time.time(), lanzador)
            e = r.fold()
            self.assertEqual(lanzamientos, [])
            self.assertEqual(len(r.eventos("revision")), 1)
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertEqual(
                e.inspeccion_pendiente, "consultar metricas de cuenta"
            )
            self.assertEqual(len(e.solicitudes), 1)

            from motor.encargos.necesidades import responder

            responder(
                r.reg, r.enc, e.fases[0].inspeccion_solicitud, "si",
                "propietario", "insp-1", ahora_iso(),
            )
            barrer(r.reg, ahora_iso(), time.time(), lanzador)
            self.assertEqual([rol for rol, _prompt in lanzamientos], ["revisor"])
            self.assertIn("estado: respuesta", lanzamientos[0][1])

    def test_poteto_local_llega_con_las_skills_hermanas(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            skills = raiz / "home" / ".agents" / "skills"
            (skills / "poteto-mode").mkdir(parents=True)
            (skills / "poteto-mode" / "SKILL.md").write_text("poteto")
            for nombre in (
                "how",
                "unslop",
                "principle-model-the-domain",
            ):
                (skills / nombre).mkdir()
                (skills / nombre / "SKILL.md").write_text(nombre)
            with mock.patch.dict(os.environ, {"HOME": str(raiz / "home")}):
                skill, digest = exigir_poteto(
                    None, raiz / "base", raiz / "runtime"
                )
            self.assertTrue(skill.is_file())
            self.assertTrue(digest)
            for nombre in ("how", "unslop", "principle-model-the-domain"):
                self.assertTrue(
                    (skill.parent.parent / nombre / "SKILL.md").is_file()
                )


DOCS = Path(__file__).resolve().parent.parent / "docs"


def _texto_doc(nombre: str) -> str:
    return " ".join((DOCS / nombre).read_text(encoding="utf-8").split())


class DocumentosDelFreno(unittest.TestCase):
    def test_el_diseno_no_afirma_que_el_instrumento_repetido_escala(self):
        diseno = _texto_doc("diseno-flujo-revisiones.md")
        self.assertNotIn("tres veces sin progreso escala", diseno)
        self.assertIn("no escala por conteo", diseno)
        self.assertIn("no escala por conteo", _texto_doc("flujo-revisiones.md"))

    def test_ningun_doc_dice_que_cambiar_la_firma_es_avance(self):
        for nombre in ("diseno-flujo-revisiones.md", "flujo-revisiones.md"):
            texto = _texto_doc(nombre)
            self.assertNotIn("progreso = cambia la firma", texto, nombre)
            self.assertNotIn("Hay progreso si la firma cambia", texto, nombre)
            self.assertIn("Cambiar archivos no", texto, nombre)


class AvanceGlobalDelFreno(unittest.TestCase):
    def _eventos(self, tmp: str, clave: str):
        reg = Registro(Path(tmp) / "base")
        self.addCleanup(reg.close)
        enc = recibir(reg, "pedido", clave, ahora_iso())["encargo"]
        contador = [0]

        def evento(kind: str, datos: dict):
            contador[0] += 1
            e = reg.fold(enc)
            reg.anotar(
                enc,
                {
                    "kind": kind,
                    "data": {
                        "fase": e.fase_activa,
                        "intento": e.intento,
                        "hora": ahora_iso(),
                        **datos,
                    },
                },
                f"avance:{contador[0]}",
            )
            return reg.fold(enc)

        return evento

    @staticmethod
    def _revision(origen: str, ids: list[str]) -> dict:
        return {
            "revision_id": "rev_" + origen + "_".join(ids),
            "version": "v1",
            "base_version": "",
            "alcance": "completa",
            "veredicto": "cambios",
            "revisor": "x",
            "origen": origen,
            "cubre": [],
            "hallazgos": [
                {
                    "id": i,
                    "gravedad": "bloqueante",
                    "criterio": "comportamiento",
                    "titulo": f"falla {i}",
                    "reproduccion": "cat x",
                    "ancla": "x:1",
                    "origen": origen,
                    "estado": "abierto",
                }
                for i in ids
            ],
            "aprobadas": [],
            "evidencia": "e",
            "efectos_pendientes": [],
            "inspeccion_faltante": "",
        }

    def test_check_rojo_de_un_commit_anterior_sigue_siendo_bloqueo(self):
        with tempfile.TemporaryDirectory() as tmp:
            evento = self._eventos(tmp, "avance-check")
            previo = evento(
                "entrega_bloqueada",
                {"sha": "A", "causa": "ci rojo", "fallas": ["ci"]},
            )
            e = evento("revision", self._revision("independiente", ["R1"]))
            self.assertFalse(hubo_avance(previo, e))
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            verde = evento("entrega_externa", {"paso": "checks_verdes", "sha": "B"})
            self.assertTrue(hubo_avance(e, verde))
            self.assertEqual(verde.fase_actual.fallas_checks, ())

    def test_revision_independiente_no_cierra_bloqueantes_de_deepseek(self):
        with tempfile.TemporaryDirectory() as tmp:
            evento = self._eventos(tmp, "avance-ds")
            previo = evento("revision", self._revision("deepseek", ["D1"]))
            e = evento("revision", self._revision("independiente", ["R1"]))
            self.assertFalse(hubo_avance(previo, e))
            self.assertEqual(
                [h.id for h in bloqueantes_abiertos(e, "deepseek")], ["D1"]
            )


class CorreccionEnCursoFlujo(unittest.TestCase):
    """Una corrección se acepta con el encargo en cualquier estado no cerrado."""

    def _recorrido(self, tmp: str, clave: str = "corr-1") -> Recorrido:
        r = Recorrido(tmp, clave, "Crea dato.txt con 42")
        self.addCleanup(r.reg.close)
        return r

    def _agente_corriendo(self, r: Recorrido) -> None:
        r.reg.anotar(
            r.enc,
            {
                "kind": "reserva",
                "data": {
                    "rol": "responsable",
                    "generacion": 1,
                    "conductor": "tok",
                    "hora": r.hora,
                },
            },
            "t-reserva",
        )
        r.reg.anotar(
            r.enc,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": "tok",
                    "pid": os.getpid(),
                    "inicio": "t",
                    "rol": "responsable",
                    "hora": r.hora,
                },
            },
            "t-intento",
        )

    def _abrir_y_cerrar_intento(self, r: Recorrido, marca: str) -> None:
        r.reg.anotar(
            r.enc,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": marca,
                    "pid": os.getpid(),
                    "inicio": "t",
                    "rol": "responsable",
                    "hora": r.hora,
                },
            },
            f"t-abierto-{marca}",
        )
        r.reg.anotar(
            r.enc,
            {"kind": "intento_cerrado", "data": {"hora": r.hora}},
            f"t-cerrado-{marca}",
        )

    def _lanzador(self, prompts: list):
        def lanzador(enc, prompt, espacio, sesion):
            prompts.append((enc.rol, Path(prompt).read_text(encoding="utf-8")))
            return None

        return lanzador

    def _barrer(
        self, r: Recorrido, lanzador, arbol: str = "ausente", hora: str = ""
    ) -> None:
        with mock.patch(
            "motor.encargos.aislamiento.sondear",
            return_value=SondaEntorno(True),
        ), mock.patch(
            "motor.flota.runtime.exigir_poteto",
            return_value=(Path(r.base) / "poteto.md", "digest"),
        ), mock.patch("motor.encargos.ciclo.observar", return_value=arbol):
            barrer(r.reg, hora or ahora_iso(), time.time(), lanzador)

    def test_con_un_agente_corriendo_queda_registrada_y_llega_al_siguiente_intento(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            self._agente_corriendo(r)
            antes = r.fold()
            self.assertEqual(antes.estado, "ejecutando")
            self.assertIsNotNone(antes.proc)
            prompts: list = []
            lanzador = self._lanzador(prompts)

            salida = corregir(r.reg, r.enc, "usa UTF-8 en los avisos", "c1", ahora_iso())
            self.assertFalse(salida.get("rechazada"))
            self.assertFalse(salida.get("conflicto"))
            e = r.fold()
            self.assertEqual(e.ajustes, ("usa UTF-8 en los avisos",))
            self.assertEqual(e.estado, "ejecutando")
            self.assertEqual(e.proc, antes.proc)
            self.assertEqual(e.hora, antes.hora)

            with mock.patch("motor.encargos.ciclo.cerrar") as cerrar:
                self._barrer(r, lanzador, arbol="vivo")
            cerrar.assert_not_called()
            self.assertEqual(prompts, [])
            self.assertEqual(r.fold().proc, antes.proc)

            self._barrer(r, lanzador, arbol="ausente")
            self.assertEqual(len(prompts), 1)
            self.assertIn("usa UTF-8 en los avisos", prompts[0][1])

    def _revisor_con_juicio_aprobado(self, r: Recorrido) -> None:
        (r.espacio / "dato.txt").write_text("42")
        (_interno(r) / "evidencia.txt").write_text("dato.txt contiene 42")
        r.incorporar()
        self.assertEqual(r.fold().rol, "revisor")
        r.juicio({"veredicto": "aprobado", "evidencia": "lei dato.txt: 42"})

    def test_una_correccion_que_llega_en_el_ultimo_intento_no_se_pierde(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            self._revisor_con_juicio_aprobado(r)
            salida = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertFalse(salida.get("rechazada"))
            e = r.incorporar()
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.rol, "responsable")
            self.assertEqual(e.ajustes, ("el dato debe ser 43",))
            prompts: list = []
            self._barrer(r, self._lanzador(prompts))
            self.assertEqual([rol for rol, _ in prompts], ["responsable"])
            self.assertIn("el dato debe ser 43", prompts[0][1])
            self.assertNotEqual(r.fold().estado, "terminado")

    def test_el_cierre_sigue_cuando_un_intento_ya_recibio_la_correccion(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            self._revisor_con_juicio_aprobado(r)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertNotEqual(r.incorporar().estado, "terminado")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "reserva",
                    "data": {
                        "rol": "responsable",
                        "generacion": 90,
                        "ajustes": 1,
                        "hora": r.hora,
                    },
                },
                "t-reserva-recibida",
            )
            self._abrir_y_cerrar_intento(r, "t-recibida")
            self.assertEqual(r.fold().ajustes_vistos, 1)
            from motor.encargos.ciclo import _intentar_cierre

            cerrado = _intentar_cierre(
                r.reg, r.fold(), r.espacio, r.d, ahora_iso()
            )
            self.assertEqual(cerrado.estado, "terminado")

    def test_una_correccion_que_entra_durante_el_cierre_no_se_pierde(self):
        from motor.encargos import cierre as modulo_cierre

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            (r.espacio / "dato.txt").write_text("42")
            (_interno(r) / "evidencia.txt").write_text("dato.txt contiene 42")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei dato.txt: 42"})
            real = modulo_cierre.componer

            def componer_con_carrera(enc, pedidos, evidencia):
                corregir(r.reg, r.enc, "el dato debe ser 43", "c-carrera", ahora_iso())
                return real(enc, pedidos, evidencia)

            with mock.patch(
                "motor.encargos.cierre.componer", side_effect=componer_con_carrera
            ):
                e = r.incorporar()
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.ajustes, ("el dato debe ser 43",))
            self.assertEqual(e.rol, "responsable")
            prompts: list = []
            self._barrer(r, self._lanzador(prompts))
            self.assertIn("el dato debe ser 43", prompts[0][1])

    def test_la_carrera_con_el_cierre_tambien_se_rechaza_si_ningun_intento_puede_recibirla(self):
        from motor.encargos import cierre as modulo_cierre
        from motor.encargos.ciclo import _intentar_cierre
        from motor.integraciones import directores

        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "carrera-sin-intento", "Crea dato.txt con 42", canal="hermes")
            self.addCleanup(r.reg.close)
            self._revisor_con_juicio_aprobado(r)
            real = modulo_cierre.componer

            def componer_con_carrera(enc, pedidos, evidencia):
                corregir(r.reg, r.enc, "el dato debe ser 43", "c-carrera", ahora_iso())
                return real(enc, pedidos, evidencia)

            with mock.patch(
                "motor.encargos.ciclo.puede_recibir_intento", return_value=False
            ), mock.patch(
                "motor.encargos.dominio.puede_recibir_intento", return_value=False
            ):
                with mock.patch(
                    "motor.encargos.cierre.componer", side_effect=componer_con_carrera
                ):
                    e = r.incorporar()
                self.assertNotEqual(e.estado, "terminado")
                self.assertEqual(e.ajustes, ("el dato debe ser 43",))
                self.assertEqual(e.ajustes_vistos, 0)
                self.assertTrue(
                    any("el dato debe ser 43" in n for n in e.procedimiento)
                )
                cerrado = _intentar_cierre(r.reg, e, r.espacio, r.d, ahora_iso())
            self.assertEqual(cerrado.estado, "terminado")
            self.assertIn("el dato debe ser 43", cerrado.evidencia[-1])
            directores.drenar(r.reg)
            cuerpos = [
                p.read_text(encoding="utf-8")
                for p in (Path(r.base) / "canal" / "hermes").iterdir()
            ]
            self.assertTrue(
                any(
                    "ningún intento pudo recibir" in c and "el dato debe ser 43" in c
                    for c in cuerpos
                ),
                cuerpos,
            )

    def test_una_correccion_entre_el_chequeo_inicial_y_la_foto_del_cierre_vuelve_al_implementador(self):
        import inspect

        from motor.encargos import ciclo

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            self._revisor_con_juicio_aprobado(r)
            real = ciclo._pedidos_efecto

            def pedidos_con_carrera(espacio):
                if any(f.function == "_intentar_cierre" for f in inspect.stack()):
                    corregir(r.reg, r.enc, "el dato debe ser 43", "c-hueco", ahora_iso())
                return real(espacio)

            with mock.patch(
                "motor.encargos.ciclo._pedidos_efecto", side_effect=pedidos_con_carrera
            ):
                e = r.incorporar()
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.ajustes, ("el dato debe ser 43",))
            self.assertEqual(e.estrategia, "investigar")
            self.assertIn("incorporar las correcciones", e.siguiente)

    def test_la_nota_sin_intento_no_da_la_correccion_por_vista(self):
        from dataclasses import replace

        from motor.encargos.ciclo import _devolver_correcciones
        from motor.encargos.dominio import Fase

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            fases = (Fase(id="f1", resultado="x", estado="preparada", evidencia="ok"),)
            for _ in range(2):
                preparadas = replace(r.fold(), fases=fases, fase_activa="f1")
                self.assertFalse(_devolver_correcciones(r.reg, preparadas, ahora_iso()))
            e = r.fold()
            self.assertEqual(e.ajustes_vistos, 0)
            self.assertEqual(
                len([n for n in e.procedimiento if "el dato debe ser 43" in n]), 1
            )
            self.assertTrue(_devolver_correcciones(r.reg, e, ahora_iso()))
            self.assertIn("incorporar las correcciones", r.fold().siguiente)

    def test_la_correccion_sin_intento_llega_al_propietario_en_el_aviso_de_cierre(self):
        from motor.integraciones import directores

        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "aviso-1", "Crea dato.txt con 42", canal="hermes")
            self.addCleanup(r.reg.close)
            self._revisor_con_juicio_aprobado(r)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            with mock.patch(
                "motor.encargos.ciclo.puede_recibir_intento", return_value=False
            ):
                e = r.incorporar()
            self.assertEqual(e.estado, "terminado")
            self.assertIn("el dato debe ser 43", e.evidencia[-1])
            directores.drenar(r.reg)
            cuerpos = [
                p.read_text(encoding="utf-8")
                for p in (Path(r.base) / "canal" / "hermes").iterdir()
            ]
            self.assertTrue(
                any(
                    "ningún intento pudo recibir" in c and "el dato debe ser 43" in c
                    for c in cuerpos
                ),
                cuerpos,
            )

    def test_un_cierre_que_la_version_anterior_acepto_con_una_correccion_nueva_se_sigue_leyendo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            self._revisor_con_juicio_aprobado(r)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            e = r.fold()
            cierre = {
                "clase": "confirmado",
                "evidencia": "lei dato.txt: 42",
                "fase": e.fase_activa,
                "intento": e.intento,
                "hora": r.hora,
                "ajustes_vistos": 0,
            }
            self.assertEqual(
                r.reg.anotar(r.enc, {"kind": "comprobacion", "data": cierre}, "t-cierre-nuevo"),
                "ilegal",
            )
            r.reg.db.execute(
                "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
                (r.hora, r.enc, "comprobacion", json.dumps(cierre), "t-cierre-legado"),
            )
            self.assertEqual(r.fold().estado, "terminado")
            self.assertEqual(r.fold().ajustes, ("el dato debe ser 43",))

    def _reserva_y_apertura(self, r: Recorrido, reserva: dict, marca: str) -> None:
        r.reg.anotar(
            r.enc, {"kind": "reserva", "data": {**reserva, "hora": r.hora}},
            f"t-reserva-{marca}",
        )
        self._abrir_y_cerrar_intento(r, marca)

    def test_una_reserva_de_la_version_anterior_vio_los_ajustes_previos(self):
        from motor.encargos.dominio import correcciones_sin_ver

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self._reserva_y_apertura(
                r, {"rol": "responsable", "generacion": 1}, "legado"
            )
            e = r.fold()
            self.assertEqual(e.ajustes_vistos, 1)
            self.assertFalse(correcciones_sin_ver(e))
            corregir(r.reg, r.enc, "y con unidad", "c2", ahora_iso())
            self.assertTrue(correcciones_sin_ver(r.fold()))

    def test_la_concesion_del_conductor_no_da_por_vistos_los_ajustes(self):
        from motor.encargos.dominio import correcciones_sin_ver

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self._reserva_y_apertura(
                r,
                {"conductor": "tok", "host": "h", "generacion": 1},
                "conductor",
            )
            e = r.fold()
            self.assertEqual(e.ajustes_vistos, 0)
            self.assertTrue(correcciones_sin_ver(e))

    def test_una_reserva_con_el_conteo_lo_respeta(self):
        from motor.encargos.dominio import correcciones_sin_ver

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self._reserva_y_apertura(
                r, {"rol": "responsable", "generacion": 1, "ajustes": 0}, "nuevo"
            )
            e = r.fold()
            self.assertEqual(e.ajustes_vistos, 0)
            self.assertTrue(correcciones_sin_ver(e))

    def test_una_nota_sin_intento_de_la_version_anterior_cuenta_como_anotada_y_el_cierre_termina(self):
        from motor.encargos.ciclo import _NOTA_SIN_INTENTO

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            self._revisor_con_juicio_aprobado(r)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            r.reg.anotar(
                r.enc,
                {
                    "kind": "procedimiento",
                    "data": {
                        "nota": _NOTA_SIN_INTENTO + " el dato debe ser 43",
                        "ajustes_vistos": 1,
                        "hora": r.hora,
                    },
                },
                f"correcciones-sin-intento:{r.enc}:1",
            )
            e = r.fold()
            self.assertEqual(e.ajustes_anotados, 1)
            self.assertEqual(e.ajustes_vistos, 0)
            with mock.patch(
                "motor.encargos.ciclo.puede_recibir_intento", return_value=False
            ), mock.patch(
                "motor.encargos.dominio.puede_recibir_intento", return_value=False
            ):
                e = r.incorporar()
            self.assertEqual(e.estado, "terminado")
            self.assertIn("el dato debe ser 43", e.evidencia[-1])

    def test_la_nota_sin_intento_con_el_prefijo_anterior_llega_al_aviso_de_cierre(self):
        from motor.integraciones import directores

        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "aviso-legado", "Crea dato.txt con 42", canal="hermes")
            self.addCleanup(r.reg.close)
            self._revisor_con_juicio_aprobado(r)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            r.reg.anotar(
                r.enc,
                {
                    "kind": "procedimiento",
                    "data": {
                        "nota": "correcciones que ningun intento pudo recibir: "
                        "el dato debe ser 43",
                        "ajustes_vistos": 1,
                        "hora": r.hora,
                    },
                },
                f"correcciones-sin-intento:{r.enc}:1",
            )
            with mock.patch(
                "motor.encargos.ciclo.puede_recibir_intento", return_value=False
            ), mock.patch(
                "motor.encargos.dominio.puede_recibir_intento", return_value=False
            ):
                e = r.incorporar()
            self.assertEqual(e.estado, "terminado")
            self.assertIn("el dato debe ser 43", e.evidencia[-1])
            directores.drenar(r.reg)
            cuerpos = [
                p.read_text(encoding="utf-8")
                for p in (Path(r.base) / "canal" / "hermes").iterdir()
            ]
            self.assertTrue(
                any(
                    "ningun intento pudo recibir" in c and "el dato debe ser 43" in c
                    for c in cuerpos
                ),
                cuerpos,
            )

    def test_en_la_via_directa_la_correccion_se_rechaza_con_su_motivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "directa-1", "Crea dato.txt con 42", via="directa")
            self.addCleanup(r.reg.close)
            salida = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(salida["rechazada"])
            self.assertIn("vía directa", salida["motivo"])
            self.assertEqual(r.fold().ajustes, ())

    def _promover(self, r: Recorrido) -> None:
        resultado = r.reg.anotar(
            r.enc,
            {"kind": "via", "data": {"via": "durable", "hora": r.hora, "clave": "p1"}},
            "t-promover",
        )
        self.assertEqual(resultado, "nuevo")
        self.assertEqual(r.fold().via, "durable")

    def test_un_rechazo_de_la_via_directa_no_bloquea_la_misma_clave_cuando_pasa_a_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "directa-2", "Crea dato.txt con 42", via="directa")
            self.addCleanup(r.reg.close)
            primera = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(primera["rechazada"])
            repetida = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(repetida["rechazada"])
            self.assertTrue(repetida["retransmision"])
            self._promover(r)
            aceptada = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertFalse(aceptada.get("rechazada"))
            self.assertFalse(aceptada.get("conflicto"))
            self.assertEqual(r.fold().ajustes, ("el dato debe ser 43",))
            otra = corregir(r.reg, r.enc, "usa UTF-8", "c2", ahora_iso())
            self.assertFalse(otra.get("rechazada"))
            self.assertEqual(r.fold().ajustes, ("el dato debe ser 43", "usa UTF-8"))
            de_nuevo = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(de_nuevo["retransmision"])
            self.assertEqual(len(r.fold().ajustes), 2)

    def test_un_rechazo_guardado_con_la_clave_por_la_version_anterior_no_la_ocupa(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            r.reg.append(
                r.enc,
                "recibo",
                {
                    "accion": "corregir",
                    "texto": "el dato debe ser 43",
                    "recibo": "rec_viejo",
                    "resultado": "rechazada",
                    "motivo": "el encargo va por la vía directa",
                    "hora": r.hora,
                },
                "ajuste:c1",
                r.hora,
            )
            aceptada = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertFalse(aceptada.get("rechazada"))
            self.assertFalse(aceptada.get("conflicto"))
            self.assertEqual(r.fold().ajustes, ("el dato debe ser 43",))
            repetida = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(repetida["retransmision"])
            self.assertEqual(r.fold().ajustes, ("el dato debe ser 43",))

    def test_si_ningun_intento_puede_lanzarse_la_correccion_no_bloquea_el_cierre(self):
        from dataclasses import replace

        from motor.encargos.ciclo import _devolver_correcciones
        from motor.encargos.dominio import Fase, puede_recibir_intento

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            preparadas = replace(
                r.fold(),
                fases=(Fase(id="f1", resultado="x", estado="preparada", evidencia="ok"),),
                fase_activa="f1",
            )
            self.assertFalse(puede_recibir_intento(preparadas))
            self.assertFalse(_devolver_correcciones(r.reg, preparadas, ahora_iso()))
            e = r.fold()
            self.assertEqual(e.ajustes_vistos, 0)
            self.assertEqual(e.ajustes_anotados, 1)
            self.assertTrue(
                any("el dato debe ser 43" in nota for nota in e.procedimiento)
            )
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(
                [x for x in r.eventos("estrategia") if "correccion" in str(x)], []
            )

    def test_el_intento_que_se_lanza_anota_cuantas_correcciones_lleva_su_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            corregir(r.reg, r.enc, "usa UTF-8", "c1", ahora_iso())
            prompts: list = []

            def lanzador(enc, prompt, espacio, sesion):
                prompts.append(Path(prompt).read_text(encoding="utf-8"))
                return ["agente-falso"]

            with mock.patch(
                "motor.encargos.aislamiento.sondear",
                return_value=SondaEntorno(True),
            ), mock.patch(
                "motor.flota.runtime.exigir_poteto",
                return_value=(Path(r.base) / "poteto.md", "digest"),
            ), mock.patch(
                "motor.encargos.ciclo.preparar_administrado"
            ), mock.patch(
                "motor.encargos.ciclo.lanzar_administrado", return_value={}
            ):
                barrer(r.reg, ahora_iso(), time.time(), lanzador)
            self.assertIn("usa UTF-8", prompts[0])
            reservas = [
                e for e in r.eventos("reserva") if e["data"].get("candidato")
            ]
            self.assertEqual([e["data"]["ajustes"] for e in reservas], [1])
            self.assertEqual(r.fold().ajustes_reservados, 1)
            self.assertEqual(r.fold().ajustes_vistos, 0)

    def test_un_arranque_fallido_no_cuenta_la_correccion_como_vista_y_el_cierre_no_termina(self):
        from motor.encargos.dominio import correcciones_sin_ver
        from motor.encargos.ciclo import _intentar_cierre

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            self._revisor_con_juicio_aprobado(r)
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertNotEqual(r.incorporar().estado, "terminado")
            self.assertEqual(r.fold().rol, "responsable")

            comandos = [["agente-falso"]]

            def lanzador(enc, prompt, espacio, sesion):
                return comandos.pop() if comandos else None

            with mock.patch(
                "motor.encargos.aislamiento.sondear",
                return_value=SondaEntorno(True),
            ), mock.patch(
                "motor.flota.runtime.exigir_poteto",
                return_value=(Path(r.base) / "poteto.md", "digest"),
            ), mock.patch(
                "motor.encargos.ciclo.preparar_administrado"
            ), mock.patch(
                "motor.encargos.ciclo.lanzar_administrado",
                side_effect=OSError("no arranca"),
            ), mock.patch(
                "motor.encargos.ciclo.observar", return_value="ausente"
            ), mock.patch(
                "motor.encargos.ciclo.recuperar_arranque",
                return_value=("ausente", None),
            ):
                with self.assertRaises(OSError):
                    barrer(r.reg, ahora_iso(), time.time(), lanzador)
                barrer(r.reg, ahora_iso(), time.time(), lanzador)
            e = r.fold()
            self.assertEqual(
                [x["data"]["ajustes"] for x in r.eventos("reserva") if "ajustes" in x["data"]],
                [1],
            )
            self.assertFalse(e.reserva)
            self.assertIsNone(e.proc)
            self.assertEqual(e.ajustes_vistos, 0)
            self.assertTrue(correcciones_sin_ver(e))

            cerrado = _intentar_cierre(r.reg, e, r.espacio, r.d, ahora_iso())
            self.assertNotEqual(cerrado.estado, "terminado")

    def test_ajustes_vistos_es_el_conteo_de_la_reserva_y_solo_al_abrir_el_intento(self):
        from motor.encargos.dominio import correcciones_sin_ver

        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            corregir(r.reg, r.enc, "primera", "c1", ahora_iso())
            r.reg.anotar(
                r.enc,
                {
                    "kind": "reserva",
                    "data": {
                        "rol": "responsable",
                        "generacion": 1,
                        "ajustes": 1,
                        "candidato": "tok",
                        "hora": r.hora,
                    },
                },
                "t-reserva-exacta",
            )
            self.assertEqual(r.fold().ajustes_vistos, 0)
            corregir(r.reg, r.enc, "segunda, despues del prompt", "c2", ahora_iso())
            r.reg.anotar(
                r.enc,
                {
                    "kind": "intento_abierto",
                    "data": {
                        "id": "tok",
                        "pid": os.getpid(),
                        "inicio": "t",
                        "rol": "responsable",
                        "hora": r.hora,
                    },
                },
                "t-abierto-exacto",
            )
            e = r.fold()
            self.assertEqual(len(e.ajustes), 2)
            self.assertEqual(e.ajustes_vistos, 1)
            self.assertTrue(correcciones_sin_ver(e))

    def test_una_correccion_con_la_aprobacion_vigente_no_abre_el_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            bare = raiz / "repo.git"
            subprocess.run(
                ["git", "init", "--bare", "-b", "main", "--quiet", str(bare)],
                check=True,
            )
            semilla = raiz / "semilla"
            subprocess.run(
                ["git", "clone", "--quiet", str(bare), str(semilla)], check=True
            )
            (semilla / "base.txt").write_text("base\n")
            for args in (
                ["add", "-A"],
                [
                    "-c", "user.name=t", "-c", "user.email=t@l",
                    "commit", "--quiet", "-m", "base",
                ],
                ["push", "--quiet", "origin", "main"],
            ):
                subprocess.run(["git", "-C", str(semilla), *args], check=True)
            r = Recorrido(
                tmp, "pr-corr-1", "agrega saludo.txt con hola",
                entrega_nivel="pr", repo=str(bare), base_rama="main",
            )
            self.addCleanup(r.reg.close)
            from motor.encargos.ciclo import _asegurar_repo

            self.assertEqual(_asegurar_repo(r.fold(), r.espacio), "")
            (r.espacio / "saludo.txt").write_text("hola")
            (_interno(r) / "evidencia.txt").write_text("lei saludo.txt: hola")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt; contiene hola"})
            e = r.incorporar()
            self.assertNotEqual(e.estado, "terminado")
            corregir(r.reg, r.enc, "el saludo debe ir en mayusculas", "c1", ahora_iso())
            gh = FakeGitHub(raiz / "fake")
            prompts: list = []
            with mock.patch(
                "motor.encargos.aislamiento.sondear",
                return_value=SondaEntorno(True),
            ), mock.patch(
                "motor.flota.runtime.exigir_poteto",
                return_value=(raiz / "poteto.md", "digest"),
            ):
                barrer(r.reg, ahora_iso(), time.time(), self._lanzador(prompts), gh=gh)
            e = r.fold()
            self.assertEqual(e.pr_numero, 0)
            self.assertEqual(e.nivel_alcanzado, "ninguno")
            estado_fake = json.loads((raiz / "fake" / "github.json").read_text())
            self.assertEqual(estado_fake["prs"], {})
            self.assertEqual([rol for rol, _ in prompts], ["responsable"])
            self.assertIn("el saludo debe ir en mayusculas", prompts[0][1])

    def test_la_correccion_la_recibe_el_revisor_tambien(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            (r.espacio / "dato.txt").write_text("42")
            (_interno(r) / "evidencia.txt").write_text("dato.txt contiene 42")
            r.incorporar()
            self.assertEqual(r.fold().rol, "revisor")
            corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            prompts: list = []
            self._barrer(r, self._lanzador(prompts))
            self.assertEqual([rol for rol, _ in prompts], ["revisor"])
            self.assertIn("el dato debe ser 43", prompts[0][1])

    def test_en_esperando_reintento_se_acepta_y_el_trabajo_sigue(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            hora = ahora_iso()
            reanuda = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            marcar_espera(r.reg, r.enc, "cuota agotada", reanuda, hora)
            self.assertEqual(r.fold().estado, "esperando_reintento")
            salida = corregir(r.reg, r.enc, "prioriza el modo Windows", "c1", hora)
            self.assertFalse(salida.get("rechazada"))
            e = r.fold()
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertEqual(e.reanuda_en, reanuda)
            self.assertEqual(e.ajustes, ("prioriza el modo Windows",))
            prompts: list = []
            self._barrer(r, self._lanzador(prompts))
            self.assertEqual(prompts, [])
            self._barrer(r, self._lanzador(prompts), hora=reanuda)
            self.assertEqual(len(prompts), 1)
            self.assertIn("prioriza el modo Windows", prompts[0][1])

    def test_en_necesita_usuario_se_acepta_y_reabre_el_trabajo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            hora = ahora_iso()
            marcar_pregunta(r.reg, r.enc, "que formato prefieres", True, hora)
            self.assertEqual(r.fold().estado, "necesita_usuario")
            prompts: list = []
            self._barrer(r, self._lanzador(prompts))
            self.assertEqual(prompts, [])
            salida = corregir(r.reg, r.enc, "usa el formato largo", "c1", hora)
            self.assertFalse(salida.get("rechazada"))
            e = r.fold()
            self.assertEqual(e.estado, "recibido")
            self.assertTrue(e.intentos_nuevos)
            self._barrer(r, self._lanzador(prompts))
            self.assertEqual(len(prompts), 1)
            self.assertIn("usa el formato largo", prompts[0][1])

    def test_detenido_sigue_detenido_hasta_continuar(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            ordenar(r.reg, r.enc, "parar", "p1", ahora_iso())
            r.reg.anotar(
                r.enc,
                {"kind": "arbol", "data": {"observacion": "ausente", "hora": r.hora}},
                "t-arbol",
            )
            self.assertEqual(r.fold().estado, "detenido")
            corregir(r.reg, r.enc, "ajuste con el trabajo detenido", "c1", ahora_iso())
            self.assertEqual(r.fold().estado, "detenido")
            ordenar(r.reg, r.enc, "continuar", "s1", ahora_iso())
            e = r.fold()
            self.assertEqual(e.estado, "recibido")
            self.assertEqual(e.ajustes, ("ajuste con el trabajo detenido",))

    def test_terminado_rechaza_con_su_motivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            (r.espacio / "dato.txt").write_text("42")
            (_interno(r) / "evidencia.txt").write_text("dato.txt contiene 42")
            r.incorporar()
            r.juicio({"veredicto": "aprobado", "evidencia": "lei dato.txt: 42"})
            self.assertEqual(r.incorporar().estado, "terminado")
            salida = corregir(r.reg, r.enc, "cambia el dato", "c1", ahora_iso())
            self.assertTrue(salida["rechazada"])
            self.assertIn("terminó", salida["motivo"])
            self.assertEqual(r.fold().ajustes, ())
            retransmitida = corregir(r.reg, r.enc, "cambia el dato", "c1", ahora_iso())
            self.assertTrue(retransmitida["rechazada"])
            self.assertTrue(retransmitida["retransmision"])

    def test_cancelado_rechaza_con_su_motivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            ordenar(r.reg, r.enc, "cancelar", "x1", ahora_iso())
            r.reg.anotar(
                r.enc,
                {"kind": "arbol", "data": {"observacion": "ausente", "hora": r.hora}},
                "t-arbol",
            )
            self.assertEqual(r.fold().estado, "cancelado")
            salida = corregir(r.reg, r.enc, "ya no importa", "c1", ahora_iso())
            self.assertTrue(salida["rechazada"])
            self.assertIn("cancelado", salida["motivo"])
            self.assertEqual(r.fold().ajustes, ())

    def test_cancelando_rechaza_aunque_el_arbol_siga_vivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            ordenar(r.reg, r.enc, "cancelar", "x1", ahora_iso())
            self.assertEqual(r.fold().estado, "deteniendo")
            salida = corregir(r.reg, r.enc, "ya no importa", "c1", ahora_iso())
            self.assertTrue(salida["rechazada"])

    def test_la_misma_clave_no_duplica_y_otro_texto_es_conflicto(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            primera = corregir(r.reg, r.enc, "texto uno", "c1", ahora_iso())
            repetida = corregir(r.reg, r.enc, "texto uno", "c1", ahora_iso())
            otra = corregir(r.reg, r.enc, "texto dos", "c1", ahora_iso())
            self.assertEqual(repetida["recibo"], primera["recibo"])
            self.assertTrue(repetida["retransmision"])
            self.assertTrue(otra["conflicto"])
            self.assertEqual(r.fold().ajustes, ("texto uno",))

    def test_un_rechazo_tambien_detecta_la_clave_repetida_con_otro_texto(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            ordenar(r.reg, r.enc, "cancelar", "x1", ahora_iso())
            primera = corregir(r.reg, r.enc, "texto uno", "c1", ahora_iso())
            self.assertTrue(primera["rechazada"])
            self.assertNotIn("conflicto", primera)
            repetida = corregir(r.reg, r.enc, "texto uno", "c1", ahora_iso())
            self.assertTrue(repetida["rechazada"])
            self.assertTrue(repetida["retransmision"])
            self.assertNotIn("conflicto", repetida)
            otra = corregir(r.reg, r.enc, "texto dos", "c1", ahora_iso())
            self.assertTrue(otra["conflicto"])
            self.assertNotIn("retransmision", otra)
            self.assertEqual(r.fold().ajustes, ())

    def test_la_clave_rechazada_sigue_siendo_de_su_texto_aunque_la_causa_cambie(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "directa-3", "Crea dato.txt con 42", via="directa")
            self.addCleanup(r.reg.close)
            primera = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(primera["rechazada"])
            otra = corregir(r.reg, r.enc, "usa UTF-8", "c1", ahora_iso())
            self.assertTrue(otra["conflicto"])
            self.assertNotIn("rechazada", otra)
            self._promover(r)
            otra = corregir(r.reg, r.enc, "usa UTF-8", "c1", ahora_iso())
            self.assertTrue(otra["conflicto"])
            self.assertEqual(r.fold().ajustes, ())
            aceptada = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertFalse(aceptada.get("conflicto"))
            self.assertEqual(r.fold().ajustes, ("el dato debe ser 43",))

    def test_la_clave_rechazada_para_un_encargo_es_conflicto_para_otro_encargo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "directa-4", "Crea dato.txt con 42", via="directa")
            self.addCleanup(r.reg.close)
            otro = recibir(r.reg, "Crea otro.txt con 7", "otro-1", r.hora)["encargo"]
            primera = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(primera["rechazada"])
            ajena = corregir(r.reg, otro, "el dato debe ser 43", "c1", ahora_iso())
            self.assertTrue(ajena["conflicto"])
            self.assertNotIn("rechazada", ajena)
            self.assertEqual(r.reg.fold(otro).ajustes, ())
            self._promover(r)
            aceptada = corregir(r.reg, r.enc, "el dato debe ser 43", "c1", ahora_iso())
            self.assertFalse(aceptada.get("conflicto"))
            self.assertEqual(r.fold().ajustes, ("el dato debe ser 43",))

    def test_un_rechazo_de_la_version_anterior_tambien_detecta_otro_texto(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            r.reg.append(
                r.enc,
                "recibo",
                {
                    "accion": "corregir",
                    "texto": "el dato debe ser 43",
                    "recibo": "rec_viejo",
                    "resultado": "rechazada",
                    "motivo": "el encargo va por la vía directa",
                    "hora": r.hora,
                },
                "ajuste:c1",
                r.hora,
            )
            otra = corregir(r.reg, r.enc, "usa UTF-8", "c1", ahora_iso())
            self.assertTrue(otra["conflicto"])
            self.assertEqual(r.fold().ajustes, ())

    def test_solo_levanta_las_causas_que_el_host_no_puede_comprobar(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._recorrido(tmp)
            for n, (clase, verificable) in enumerate(
                (("entorno", True), ("juicio:r1", False))
            ):
                r.reg.anotar(
                    r.enc,
                    {
                        "kind": "impedimento",
                        "data": {
                            "clase": clase,
                            "causa": f"falla {clase}",
                            "condicion": f"que {clase} funcione",
                            "verificable": verificable,
                            "hora": r.hora,
                        },
                    },
                    f"t-imp-{n}",
                )
            self.assertEqual(len(r.fold().impedimentos), 2)
            corregir(r.reg, r.enc, "el juicio estaba mal", "c1", ahora_iso())
            self.assertEqual([i.clase for i in r.fold().impedimentos], ["entorno"])


class MedianosDeepseekFlujo(unittest.TestCase):
    """Un Medium abierto de DeepSeek bloquea como un High; un Low solo se ve."""

    def _comentario(self, *hallazgos: dict) -> str:
        return (
            "ai-review › findings\n"
            "<!-- ai-review:findings="
            + json.dumps({"schema": 3, "findings": list(hallazgos)})
            + " -->"
        )

    def _hallazgo(self, ident: str, severidad: str, estado: str = "open") -> dict:
        return {
            "id": ident,
            "title": f"Titulo de {ident}",
            "severity": severidad,
            "status": {"kind": estado},
            "primary_anchor": {"kind": "legacy", "path": "saludo.txt", "line": 3},
        }

    def _con_pr_abierto(self, tmp: str):
        raiz = Path(tmp)
        bare = raiz / "repo.git"
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", "--quiet", str(bare)], check=True
        )
        semilla = raiz / "semilla"
        subprocess.run(["git", "clone", "--quiet", str(bare), str(semilla)], check=True)
        (semilla / "base.txt").write_text("base\n")
        for args in (
            ["add", "-A"],
            ["-c", "user.name=t", "-c", "user.email=t@l", "commit", "--quiet", "-m", "base"],
            ["push", "--quiet", "origin", "main"],
        ):
            subprocess.run(["git", "-C", str(semilla), *args], check=True)
        r = Recorrido(
            tmp, "ds-1", "agrega saludo.txt con hola",
            entrega_nivel="pr", repo=str(bare), base_rama="main",
        )
        self.addCleanup(r.reg.close)
        from motor.encargos.ciclo import _asegurar_repo

        self.assertEqual(_asegurar_repo(r.fold(), r.espacio), "")
        (r.espacio / "saludo.txt").write_text("hola")
        (_interno(r) / "evidencia.txt").write_text("lei saludo.txt: hola")
        r.incorporar()
        r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt; contiene hola"})
        r.incorporar()
        gh = FakeGitHub(raiz / "fake")
        barrer(r.reg, ahora_iso(), time.time(), lambda *a: None, gh=gh)
        e = r.fold()
        self.assertEqual(e.nivel_alcanzado, "pr_abierto")
        estado_fake = json.loads((raiz / "fake" / "github.json").read_text())
        return r, gh, raiz, estado_fake, e

    def _leer(self, r, gh, raiz, estado_fake, e, *hallazgos: dict, prompts=None):
        pr = next(iter(estado_fake["prs"].values()))
        pr["comentarios_deepseek"] = [
            {"texto": self._comentario(*hallazgos), "sha": e.pr_sha}
        ]
        (raiz / "fake" / "github.json").write_text(json.dumps(estado_fake))

        def lanzador(enc, prompt, espacio, sesion):
            if prompts is not None:
                prompts.append((enc.rol, Path(prompt).read_text(encoding="utf-8")))
            return None

        with mock.patch(
            "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
        ), mock.patch(
            "motor.flota.runtime.exigir_poteto",
            return_value=(raiz / "poteto.md", "digest"),
        ):
            for _ in range(2):
                barrer(r.reg, e.reanuda_en or ahora_iso(), time.time(), lanzador, gh=gh)
        return r.fold()

    def test_un_medium_abierto_devuelve_el_trabajo_con_titulo_archivo_y_linea(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, gh, raiz, estado_fake, e = self._con_pr_abierto(tmp)
            prompts: list = []
            e = self._leer(
                r, gh, raiz, estado_fake, e, self._hallazgo("F1", "Medium"),
                prompts=prompts,
            )
            self.assertNotEqual(e.estado, "terminado")
            abiertos = bloqueantes_abiertos(e, "deepseek")
            self.assertEqual([h.id for h in abiertos], ["F1"])
            self.assertEqual(abiertos[0].titulo, "Titulo de F1")
            self.assertEqual(abiertos[0].ancla, "saludo.txt:3")
            self.assertEqual([rol for rol, _ in prompts], ["responsable"])
            self.assertIn("Titulo de F1", prompts[0][1])
            self.assertIn("saludo.txt:3", prompts[0][1])

    def test_el_mismo_medium_resuelto_deja_avanzar(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, gh, raiz, estado_fake, e = self._con_pr_abierto(tmp)
            e = self._leer(
                r, gh, raiz, estado_fake, e, self._hallazgo("F1", "Medium", "resolved")
            )
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())

    def test_un_low_no_devuelve_el_trabajo_y_sigue_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, gh, raiz, estado_fake, e = self._con_pr_abierto(tmp)
            prompts: list = []
            e = self._leer(
                r, gh, raiz, estado_fake, e, self._hallazgo("F2", "Low"),
                prompts=prompts,
            )
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(prompts, [])
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())
            self.assertIn("Titulo de F2", vista(e)["revision"]["observaciones"])

    def test_el_medium_abierto_se_cierra_cuando_deepseek_lo_marca_resuelto(self):
        gravedad = hallazgos_deepseek(
            [{"texto": self._comentario(self._hallazgo("F1", "Medium"))}], "abc"
        )[0].gravedad
        self.assertEqual(gravedad, "bloqueante")
        self.assertEqual(
            hallazgos_deepseek(
                [{"texto": self._comentario(self._hallazgo("F1", "Medium", "fixed"))}],
                "abc",
            ),
            (),
        )
        low = hallazgos_deepseek(
            [{"texto": self._comentario(self._hallazgo("F2", "Low"))}], "abc"
        )
        self.assertEqual([h.gravedad for h in low], ["observacion"])


if __name__ == "__main__":
    unittest.main()
