"""Una comprobacion en rojo del supervisor es trabajo pendiente del responsable.

El 9-nov el encargo `enc_0dc2b585542ac181` entrego `producto_listo`, una de sus
comprobaciones declaradas salio `rc 1` y el supervisor dejo una estrategia
`investigar` para el responsable. No hubo `reserva` ni `intento_abierto`: el
paso de entrega devolvio el trabajo como si fuera una espera y la quietud cerro
la fase con un pendiente. Estas pruebas afirman que la siguiente decision abre
un intento del responsable con la salida de la comprobacion que fallo.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso, barrer
from tests.dobles import github_con_hallazgos
from tests.test_flujo_revisiones import _entrega_pr

# Valores reales del evento 67593 de `enc_0dc2b585542ac181`: la comprobacion
# que salio en rojo y el final de su stderr.
ARGV_EN_ROJO = ["python3", "-m", "unittest", "tests.test_politica_en_tabla"]
OBJETIVO_EN_ROJO = (
    "comportamiento de la politica en sus limites, la sesion renovada y el relevo"
)
STDERR_EN_ROJO = (
    "_a_los_31_omite\n"
    "AssertionError: False is not true : ('sin revisión de DeepSeek para el "
    "SHA 95317457d9bc tras 30 min; la fase sigue como si no hubiera hallazgos',)\n"
    "Ran 26 tests in 5.370s\n"
    "FAILED (failures=1)\n"
)
MOTIVO_REAL = (
    "la comprobacion del supervisor esta en rojo: "
    f"{OBJETIVO_EN_ROJO}: rc 1"
)
SIGUIENTE_REAL = (
    "corregir la comprobacion en rojo antes de publicar; lo aprobado que no "
    "cambio se conserva"
)


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


def _lanzador(roles: list[str], prompts: list[str]):
    def lanzar(enc, prompt, espacio, sesion):
        roles.append(enc.rol)
        prompts.append(Path(prompt).read_text(encoding="utf-8"))
        return ["agente-falso"]

    return lanzar


class RelanzaTrasComprobacionRoja(unittest.TestCase):
    def _aprobado(self, tmp: str, clave: str):
        vuelta, r = _entrega_pr(self, tmp, clave, github_con_hallazgos)
        (r.espacio / "saludo.txt").write_text("hola\n", encoding="utf-8")
        r.incorporar()
        r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
        r.incorporar()
        e = r.fold()
        self.assertIsNotNone(e.revision_vigente)
        self.assertEqual(e.revision_vigente.veredicto, "aprobado")
        return vuelta, r, e

    def _declarar(self, r) -> list[dict]:
        comandos = [{"objetivo": OBJETIVO_EN_ROJO, "argv": ARGV_EN_ROJO}]
        interno = r.espacio / ".interno"
        interno.mkdir(exist_ok=True)
        (interno / "comprobaciones.json").write_text(
            json.dumps({"comprobaciones": comandos}), encoding="utf-8"
        )
        return comandos

    def _observar(
        self,
        r,
        e,
        declaradas: list[dict],
        rc: int,
        stderr: str = "",
        stdout: str = "",
    ) -> None:
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
                            "rc": rc,
                            "stdout": stdout,
                            "stderr": stderr,
                        }
                    ],
                    "hora": ahora_iso(),
                },
            },
            f"t-observacion:{r.enc}:{e.producto.firma}:{rc}",
        )

    def _reservas(self, r, rol: str = "responsable") -> list[dict]:
        return [
            ev
            for ev in r.reg.eventos(r.enc)
            if ev["kind"] == "reserva" and ev["data"].get("rol") == rol
        ]

    def test_una_comprobacion_en_rojo_abre_un_intento_del_responsable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._aprobado(tmp, "roja-abre")
            declaradas = self._declarar(r)
            self._observar(r, e, declaradas, rc=1, stderr=STDERR_EN_ROJO)

            roles: list[str] = []
            prompts: list[str] = []
            _barrido(r, vuelta.gh, _lanzador(roles, prompts))

            self.assertIn(
                "responsable",
                roles,
                "una comprobacion del supervisor en rojo abre un intento del "
                "responsable",
            )
            self.assertTrue(
                self._reservas(r),
                "el intento del responsable deja su reserva en el registro",
            )
            texto = "\n".join(prompts)
            self.assertIn(
                " ".join(ARGV_EN_ROJO),
                texto,
                "el contexto del responsable lleva el argv de la comprobacion",
            )
            self.assertIn("rc 1", texto)
            self.assertIn(
                "Ran 26 tests in 5.370s",
                texto,
                "el contexto del responsable lleva el final del stderr",
            )

    def test_una_observacion_en_verde_deja_publicar_como_hoy(self):  # no-roja-en-base: caracterización de que una observación verde no devuelve el producto
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._aprobado(tmp, "verde-publica")
            declaradas = self._declarar(r)
            self._observar(r, e, declaradas, rc=0, stdout="OK\n")

            roles: list[str] = []
            prompts: list[str] = []
            _barrido(r, vuelta.gh, _lanzador(roles, prompts))

            eventos = r.reg.eventos(r.enc)
            self.assertFalse(
                any(
                    ev["kind"] == "estrategia"
                    and "rojo" in ev["data"].get("motivo", "")
                    for ev in eventos
                ),
                "una observacion en verde no devuelve el producto al responsable",
            )
            self.assertTrue(
                any(
                    ev["kind"] == "entrega_externa"
                    and ev["data"].get("paso") in ("commit", "push")
                    for ev in eventos
                ),
                "con la observacion en verde la publicacion sigue como hoy",
            )

    def test_un_ajuste_nuevo_relanza_como_hoy(self):  # no-roja-en-base: caracterización de que un ajuste nuevo devuelve el trabajo al responsable
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, _e = self._aprobado(tmp, "ajuste-relanza")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "ajuste",
                    "data": {
                        "texto": "corrige el limite antes de publicar",
                        "hora": ahora_iso(),
                    },
                },
                "t-ajuste-nuevo",
            )

            roles: list[str] = []
            prompts: list[str] = []
            _barrido(r, vuelta.gh, _lanzador(roles, prompts))

            self.assertIn(
                "responsable",
                roles,
                "un ajuste nuevo relanza al responsable como hoy",
            )
            self.assertTrue(self._reservas(r))

    def test_replay_del_prefijo_real_hasta_67595_abre_intento(self):
        """Reconstruye los eventos decisivos de `enc_0dc2b585542ac181` hasta
        67595: producto `e12f5e899f5d`, revisión aprobada, la observación del
        supervisor con la comprobación en rojo y la estrategia `investigar`
        que ya había quedado escrita sin intento. La siguiente decisión abre
        un intento del responsable; el prefijo completo se plegó en
        `.interno/evidencia.txt` con el mismo resultado."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._aprobado(tmp, "replay-67595")
            declaradas = self._declarar(r)
            self._observar(r, e, declaradas, rc=1, stderr=STDERR_EN_ROJO)
            # La misma ukey con la que el prefijo real ya escribio la
            # estrategia corta en el evento 67595. El detalle no puede quedar
            # sin registrar porque esta clave ya exista.
            r.reg.anotar(
                r.enc,
                {
                    "kind": "estrategia",
                    "data": {
                        "estrategia": "investigar",
                        "rol": "responsable",
                        "motivo": MOTIVO_REAL,
                        "siguiente": SIGUIENTE_REAL,
                        "hora": ahora_iso(),
                    },
                },
                f"devolucion:{r.enc}:{e.generacion}:observacion-roja-"
                f"{e.producto.firma[:12]}",
            )

            roles: list[str] = []
            prompts: list[str] = []
            _barrido(r, vuelta.gh, _lanzador(roles, prompts))

            self.assertIn(
                "responsable",
                roles,
                "el replay del prefijo real hasta 67595 abre un intento del "
                "responsable, no espera",
            )
            self.assertTrue(self._reservas(r))
            self.assertIn("tests.test_politica_en_tabla", "\n".join(prompts))


if __name__ == "__main__":
    unittest.main()
