"""Reproducción acotada de los casos reales del registro del 6-nov.

Cada caso se reconstruye con eventos y juicios mínimos escritos aquí: los
mismos estados y conteos que observó el registro (ids y títulos reales de las
revisiones citadas), sin volcar ningún evento del registro vivo. La prueba
afirma el comportamiento esperado después de este encargo; corrida contra la
base previa (279835f) la misma reconstrucción reproduce el bloqueo observado.
El par rojo/verde por caso, con los SHA antes y después, queda en
.interno/evidencia.txt.

Casos:
- enc_fb7358452cdfe01b, revisiones 12231 y 12324: nueve bloqueantes; la
  primera revisión reporta uno y la segunda cero. Antes el estado conservaba
  los nueve y la escalera escalaba; después los omitidos quedan resueltos
  con su revisión como evidencia y no escala.
- enc_b4f3117f5a0e7531, revisión 12673: once abiertos; la revisión de
  corrección re-reporta tres por su id y añade uno nuevo. Antes el estado
  conservaba doce; después quedan solo los cuatro reportados y los ocho
  omitidos quedan resueltos.
- enc_daf8e41dd3a3fac6, disputa de DeepSeek F1: el hallazgo de DeepSeek
  señala una ruta que la restricción del encargo prohíbe; hoy se entrega con
  la nota de fuera de alcance, sin escalada ni pregunta.
- enc_07844b614147c266, cierre en B tras el tope de DeepSeek: su
  reproducción es la caracterización existente
  (tests.test_deepseek_tarde_y_continuar, test_dos_fases_sin_revision_tras_
  el_tope_integra_y_arranca_la_siguiente), verde sobre la base y sobre HEAD.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import _incorporar, ahora_iso, recibir
from motor.encargos.comprobacion import firma_producto
from motor.encargos.dominio import bloqueantes_abiertos
from motor.encargos.registro import Registro
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import (
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)

RESTRICCION_DAF8E = (
    "Cambio acotado a la rama encargar de motor/encargos/cli.py y su prueba"
)
ANCLA_F1_DAF8E = "motor/integraciones/cli.py:176"
TITULO_F1_DAF8E = (
    "El descarte de --texto con --archivo sigue en motor director encargar"
)


def setUpModule():
    sonda_sin_aislamiento_del_host()


class Recorrido:
    """Harness sin LLM: la prueba hace de agente entre incorporaciones."""

    def __init__(self, tmp: str, clave: str, texto: str):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        self.hora = ahora_iso()
        self.enc = recibir(self.reg, texto, clave, self.hora)["encargo"]
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
            {"kind": "reserva", "data": {"rol": e.rol or "responsable", "generacion": n}},
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
        return _incorporar(self.reg, self.fold(), self.espacio, self.hora, self.d)

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


def _bloqueante(hid: str, titulo: str, ancla: str = "malo.txt:1") -> dict:
    return {
        "id": hid,
        "criterio": "comportamiento",
        "titulo": titulo,
        "reproduccion": "cat malo.txt",
        "ancla": ancla,
    }


def _escaladas(r: Recorrido) -> list[dict]:
    return [ev for ev in r.eventos("sin_progreso") if ev["data"].get("escalar")]


def _resueltos_en_ultima_revision(r: Recorrido) -> dict[str, str]:
    revision = r.eventos("revision")[-1]
    return {
        h["id"]: h.get("estado")
        for h in revision["data"]["hallazgos"]
        if h.get("estado") == "resuelto"
    }


IDS_FB735 = {
    "h_dfa0b301": "Falta la receta ejecutable de despliegue exigida en B.1-B.4",
    "h_dfa0b305": "Los cortes exigidos después de cambiar estado no están reproducidos",
    "h_8f01a901": "El candado remoto admite una segunda corrida dentro de la primera",
    "h_8f01a902": "Las migraciones de producción corren sin la transacción única",
    "h_8f01a903": "El rebuild remoto no selecciona el proyecto Compose de Orbit",
    "h_8f01a904": "El diario guarda el valor del archivo anterior, no el vigente",
    "h_8f01a905": "Con SHA_PREVIO vacío se lee el índice Git y se omite la convergencia",
    "h_8f01a906": "Eliminar un interruptor no lo apaga y el checklist certifica",
    "h_8f01a907": "La verificación previa a live recibe una plataforma truncada",
}


class LaRevision12231Y12324DelCasoFb735(unittest.TestCase):
    """enc_fb7358452cdfe01b, fase motor-despliegue.

    Nueve bloqueantes abiertos en alcance corrección (revisión 12231: cubre
    los nueve, reporta uno; revisión 12324: reporta cero y el revisor escribe
    que el último quedó resuelto). Antes del encargo el estado conservaba los
    nueve y la escalera escalaba a relevo y a pregunta; después los omitidos
    quedan resueltos con su revisión como evidencia y no escala.
    """

    def test_los_omitidos_quedan_resueltos_con_su_revision_y_no_escalera(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "caso-fb735", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [
                        _bloqueante(hid, titulo)
                        for hid, titulo in IDS_FB735.items()
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual(
                [h.id for h in bloqueantes_abiertos(e)], list(IDS_FB735)
            )
            # Revisión 12231: alcance corrección que cubre los nueve,
            # reporta solo h_8f01a904.
            (r.espacio / "malo.txt").write_text("Y")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": (
                        "ocho quedaron resueltos; h_8f01a904 sigue: "
                        "el diario registra vacío si falla la lectura del valor vigente"
                    ),
                    "bloqueantes": [
                        _bloqueante(
                            "h_8f01a904",
                            IDS_FB735["h_8f01a904"],
                            "tools/despliegue/desplegar.sh:244",
                        )
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual(
                [h.id for h in bloqueantes_abiertos(e)], ["h_8f01a904"]
            )
            resueltos = _resueltos_en_ultima_revision(r)
            esperados_12231 = set(IDS_FB735) - {"h_8f01a904"}
            self.assertEqual(set(resueltos), esperados_12231)
            revision = r.eventos("revision")[-1]
            self.assertTrue(revision["data"].get("revision_id"))
            # Revisión 12324: reporta cero de los nueve y el revisor escribe
            # que el último quedó resuelto.
            (r.espacio / "malo.txt").write_text("B")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": (
                        "cat malo.txt -> B; h_8f01a904 quedó resuelto con "
                        "la lectura del valor vigente corregida"
                    ),
                }
            )
            e = r.incorporar()
            self.assertEqual(bloqueantes_abiertos(e), ())
            self.assertEqual(
                _resueltos_en_ultima_revision(r), {"h_8f01a904": "resuelto"}
            )
            self.assertEqual(_escaladas(r), [])
            self.assertEqual(r.eventos("cierre_forzado"), [])


IDS_12673_REPORTADOS = {
    "h_2cef8ff3": (
        "B.1: el plazo único sigue sin cumplirse y el respaldo puede empezar "
        "tras vencer",
        "tools/despliegue/desplegar.sh:354",
    ),
    "h_4a1c5462": (
        "B.1/B.5(o): el ensayo convierte errores de consulta de las guardas "
        "en éxito",
        "tools/despliegue/desplegar.sh:58",
    ),
    "h_dd6bf84b": (
        "B.5: las migraciones reales de los bloques siguientes quedan "
        "preaplicadas",
        "tests/test_despliegue_motor.py:319",
    ),
}
NUEVO_12673 = (
    "h_c533891c",
    "B.1: un fallo de SSH al leer el diario permite sobrescribir una reversa",
    "tools/despliegue/lib.sh:48",
)


class LaRevision12673DelCasoB4f3(unittest.TestCase):
    """enc_b4f3117f5a0e7531.

    Once bloqueantes abiertos; la revisión 12673 (corrección) re-reporta tres
    por su id y añade uno nuevo. Antes del encargo el estado conservaba doce
    (los once y el nuevo) y la escalera escalaba; después los ocho omitidos
    quedan resueltos con la revisión como evidencia y quedan abiertos solo
    los cuatro reportados.
    """

    def test_los_omitidos_quedan_resueltos_y_solo_los_reportados_abiertos(self):
        previos = {
            f"h_previo_{i}": (f"bloqueante previo {i} del ensayo", "malo.txt:1")
            for i in range(8)
        }
        todos = dict(IDS_12673_REPORTADOS)
        todos.update(previos)
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "caso-b4f3", "malo.txt con B")
            self.addCleanup(r.reg.close)
            (r.espacio / "malo.txt").write_text("X")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "lei malo.txt",
                    "bloqueantes": [
                        _bloqueante(hid, titulo, ancla)
                        for hid, (titulo, ancla) in todos.items()
                    ],
                }
            )
            e = r.incorporar()
            self.assertEqual(
                [h.id for h in bloqueantes_abiertos(e)], list(todos)
            )
            # Revisión 12673: corrección que re-reporta tres por su id y
            # añade uno nuevo.
            (r.espacio / "malo.txt").write_text("Y")
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            reportados = [
                _bloqueante(hid, titulo, ancla)
                for hid, (titulo, ancla) in IDS_12673_REPORTADOS.items()
            ]
            reportados.append(_bloqueante(*NUEVO_12673))
            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": (
                        "los tres reportados siguen; los demás del ensayo "
                        "quedaron resueltos"
                    ),
                    "bloqueantes": reportados,
                }
            )
            e = r.incorporar()
            abiertos = [h.id for h in bloqueantes_abiertos(e)]
            self.assertEqual(
                abiertos,
                list(IDS_12673_REPORTADOS) + [NUEVO_12673[0]],
            )
            resueltos = _resueltos_en_ultima_revision(r)
            self.assertEqual(set(resueltos), set(previos))
            revision = r.eventos("revision")[-1]
            self.assertTrue(revision["data"].get("revision_id"))
            self.assertEqual(_escaladas(r), [])
            self.assertEqual(r.eventos("cierre_forzado"), [])


class _DeepSeekF1Daf8e(FakeGitHub):
    """DeepSeek re-reporta el F1 del caso daf8e en la ruta prohibida."""

    def comentarios_deepseek(self, repo, numero, sha=""):
        hallazgos = [
            {
                "id": "F1",
                "severity": "medium",
                "status": "open",
                "title": TITULO_F1_DAF8E,
                "file": ANCLA_F1_DAF8E.rsplit(":", 1)[0],
                "line": 176,
            }
        ]
        texto = (
            "DeepSeek: CHANGES_REQUESTED\nai-review:findings="
            + json.dumps(hallazgos)
        )
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}]


class ElF1DeDeepSeekDelCasoDaf8e(unittest.TestCase):
    """enc_daf8e41dd3a3fac6.

    La restricción real del encargo limita el cambio a la rama encargar de
    motor/encargos/cli.py y DeepSeek señaló F1 en motor/integraciones/
    cli.py:176. En el registro el hallazgo bloqueó, la disputa del
    responsable no llegó a decisión y el relevo agotado terminó en pregunta
    (parada y continuada por el director). La base previa ya clasifica el
    hallazgo como fuera de alcance: la reconstrucción afirma que se entrega
    con la nota, sin escalada ni pregunta.
    """

    def test_el_hallazgo_prohibido_se_entrega_con_nota_sin_escalada(self):
        with tempfile.TemporaryDirectory() as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "caso-daf8e", _DeepSeekF1Daf8e,
                texto="arregla el descarte de --texto con --archivo",
                restricciones=(RESTRICCION_DAF8E,),
            )
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())
            self.assertTrue(e.fuera_de_alcance, e.fuera_de_alcance)
            self.assertEqual(e.fuera_de_alcance[0][0], "F1")
            escaladas = [
                ev for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "sin_progreso" and ev["data"].get("escalar")
            ]
            self.assertEqual(escaladas, [])
            self.assertEqual(
                [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "pregunta"],
                [],
            )
