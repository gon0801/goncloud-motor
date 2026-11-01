"""Primer uso real de `ci.correr` (1-nov, enc_1b8916e8a0bac627): una corrida de la
batería tarda unos 18 minutos, más que un turno del agente.

- ci_base_1 terminó bien (`estado: ok`, log de 486 KB) en la carpeta del intento
  que la lanzó, pero el encargo ya iba en otro intento: la solicitud siguió
  `pendiente` y la petición del buzón ya estaba archivada.
- ci_base_2 se mató porque la sesión del responsable se marcó `colgada`:
  `ciclo.barrer` cierra las herramientas del turno y la corrida de CI no
  dependía de ese turno.

Cada prueba reproduce uno de esos casos con las mismas piezas del motor; la
herramienta de CI real se sustituye por un resultado escrito en su carpeta."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import necesidades as nec
from motor.encargos.ciclo import ahora_iso, ordenar
from tests.test_respuestas_buzon import _Flujo

LOG_CI = "jaula-linux\tPrueba\t2026-11-01T10:00:00Z Ran 1900 tests in 1080s\nOK\n"

CI_PEDIDO = {
    "veredicto": "no_concluyente",
    "evidencia": "falta la línea base de la batería en Linux",
    "inspeccion_faltante": "el log del job jaula-linux",
    "inspeccion": {
        "operacion": "ci.correr",
        "recurso": "ci-linux",
        "parametros": {"job": "jaula-linux"},
    },
}


class _CiAsincrono(_Flujo):
    def montar(self, clave: str):
        """Un revisor que pidió ci.correr; la herramienta sigue corriendo en el
        intento-1 y el barrido la deja `pendiente`."""
        r = self.recorrido(clave)
        (r.base / "capacidades.json").write_text(
            json.dumps(
                {
                    "recursos": {
                        "ci-linux": {
                            "tipo": "ci",
                            "autorizacion": "",
                            "repo": "o/r",
                            "workflow": "pruebas.yml",
                            "base": "main",
                        }
                    }
                }
            )
        )
        self.abrir_turno(r)
        r.juicio(dict(CI_PEDIDO))
        r.incorporar()
        self.cerrar_turno(r)
        ident = r.fold().fases[0].inspeccion_solicitud
        self.intento(r, "intento-1-a")
        corriendo = {"estado": "pendiente", "motivo": "el CI sigue corriendo"}
        with mock.patch.object(nec, "_ejecutar", return_value=corriendo):
            self.barrer(r)
        self.assertEqual(self.sol(r, ident).estado, "pendiente")
        return r, ident

    def intento(self, r, nombre: str) -> Path:
        carpeta = r.d / nombre
        carpeta.mkdir(exist_ok=True)
        (r.d / "actual").write_text(nombre)
        return carpeta

    def sol(self, r, ident: str):
        return next(s for s in r.fold().solicitudes if s.id == ident)

    def peticion_archivada(self, r, ident: str) -> None:
        peticiones = r.espacio / ".interno" / "buzon" / "peticiones"
        (peticiones / f"{ident}.json").replace(peticiones / f"{ident}.hecha")

    def termina(self, intento: Path, ident: str, estado: str = "ok") -> Path:
        """La herramienta de CI termina y deja su resultado en su carpeta."""
        carpeta = intento / "herramientas" / ident
        log = carpeta / "remoto" / "jaula-linux-abc1234-run41274694053.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(LOG_CI, encoding="utf-8")
        resultado = {
            "estado": estado,
            "datos": {
                "job": "jaula-linux",
                "run": "41274694053",
                "sha": "abc1234",
                "conclusion": "success",
                "log_nombre": log.name,
                "log_host": str(log),
                "log": str(log),
            },
            "evidencia": {"tipo": "ci", "run": "41274694053", "job": "jaula-linux"},
        }
        if estado != "ok":
            resultado = {"estado": estado, "motivo": "la herramienta falló"}
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "resultado.json").write_text(json.dumps(resultado))
        return carpeta


class ElResultadoDeUnIntentoAnteriorLlega(_CiAsincrono):
    def test_el_resultado_escrito_en_la_carpeta_de_un_intento_anterior_llega_a_la_solicitud(self):
        r, ident = self.montar("tarde-1")
        self.peticion_archivada(r, ident)
        self.intento(r, "intento-2-b")
        self.termina(r.d / "intento-1-a", ident)

        self.barrer(r)

        sol = self.sol(r, ident)
        self.assertEqual(sol.estado, "resuelta")
        doc = self.buzon(r, ident)
        self.assertEqual(doc["estado"], "resuelta")
        self.assertEqual(doc["datos"]["run"], "41274694053")
        publico = Path(doc["datos"]["log"])
        self.assertTrue(publico.is_relative_to(r.espacio))
        self.assertEqual(publico.read_text(encoding="utf-8"), LOG_CI)

    def test_el_resultado_llega_sin_que_nadie_vuelva_a_pedirlo(self):
        r, ident = self.montar("tarde-2")
        self.peticion_archivada(r, ident)
        self.intento(r, "intento-2-b")
        self.termina(r.d / "intento-1-a", ident)

        with mock.patch.object(nec, "_ejecutar", side_effect=AssertionError("no se repite")):
            self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "resuelta")

    def test_la_solicitud_recuerda_donde_quedo_su_resultado(self):
        r, ident = self.montar("tarde-3")

        carpeta = self.sol(r, ident).carpeta

        self.assertEqual(Path(carpeta), r.d / "intento-1-a" / "herramientas" / ident)

    def test_una_peticion_que_sigue_en_el_buzon_tambien_se_resuelve(self):
        r, ident = self.montar("tarde-4")
        self.intento(r, "intento-2-b")
        self.termina(r.d / "intento-1-a", ident)

        self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "resuelta")
        self.assertEqual(self.buzon(r, ident)["estado"], "resuelta")

    def test_una_corrida_en_curso_no_se_da_por_tardia_al_cambiar_el_intento(self):
        r, ident = self.montar("tarde-5")
        self.intento(r, "intento-2-b")
        marca = r.d / "intento-1-a" / "herramientas" / f"{ident}.json"
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(json.dumps({"pid": 1, "inicio": "t0", "solicitud": ident}))

        with mock.patch.object(nec, "_herramienta_viva", return_value=True):
            self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "pendiente")
        self.assertEqual(self.buzon(r, ident)["estado"], "pendiente")

    def test_una_herramienta_muerta_sin_resultado_no_deja_la_solicitud_pendiente(self):
        r, ident = self.montar("tarde-6")
        self.peticion_archivada(r, ident)
        self.intento(r, "intento-2-b")
        marca = r.d / "intento-1-a" / "herramientas" / f"{ident}.json"
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(json.dumps({"pid": 1, "inicio": "t0", "solicitud": ident}))

        with mock.patch.object(nec, "_herramienta_viva", return_value=False):
            self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "error")
        self.assertEqual(self.buzon(r, ident)["estado"], "error")


class UnaHerramientaMatadaNoDejaLaSolicitudPendiente(_CiAsincrono):
    """`cerrar_herramientas` mata la herramienta y archiva su marca como
    `<id>.hecha`: sin resultado.json, la solicitud no puede quedarse esperando."""

    def matar_con_cerrar_herramientas(self, r, ident: str) -> None:
        herramienta = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
        )
        self.addCleanup(herramienta.kill)
        self.addCleanup(herramienta.wait)
        marca = r.d / "intento-1-a" / "herramientas" / f"{ident}.json"
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(
            json.dumps(
                {"pid": herramienta.pid, "inicio": "t0", "solicitud": ident, "operacion": "sqlite.tablas"}
            )
        )

        def matar(pid, inicio):
            herramienta.kill()
            herramienta.wait()
            return True

        with mock.patch("motor.plataforma.hora_inicio", return_value="t0"), mock.patch(
            "motor.plataforma.pid_vivo", side_effect=lambda pid: herramienta.poll() is None
        ), mock.patch("motor.plataforma.matar_proc", side_effect=matar):
            self.assertEqual(nec.cerrar_herramientas(r.d / "intento-1-a"), 1)
        self.assertEqual(
            sorted(p.name for p in marca.parent.glob("*.*")), [f"{ident}.hecha"]
        )

    def test_la_del_responsable_que_cierra_el_barrido_queda_en_error(self):
        r, ident = self.montar("matada-1")
        self.matar_con_cerrar_herramientas(r, ident)
        self.intento(r, "intento-2-b")

        with mock.patch.object(nec, "_ejecutar", side_effect=AssertionError("no se relanza")):
            self.barrer(r)
            self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "error")
        self.assertEqual(self.buzon(r, ident)["estado"], "error")

    def test_con_la_peticion_ya_archivada_tambien(self):
        r, ident = self.montar("matada-2")
        self.peticion_archivada(r, ident)
        self.matar_con_cerrar_herramientas(r, ident)
        self.intento(r, "intento-2-b")

        self.barrer(r)
        self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "error")
        self.assertEqual(self.buzon(r, ident)["estado"], "error")


class ElResultadoConElEncargoDetenido(_CiAsincrono):
    """Decisión: un resultado `ok` que llega después de la resolución `detenida`
    se registra como `resuelta` y espera en el buzón a que el propietario
    continúe. Uno que no es `ok` deja la solicitud `detenida`: la causa ya está
    dicha y no se vuelve a anotar."""

    def detener(self, r, ident: str) -> None:
        ordenar(r.reg, r.enc, "parar", "parar-1", ahora_iso())
        self.barrer(r)
        self.assertEqual(self.sol(r, ident).estado, "detenida")
        self.assertEqual(r.fold().estado, "detenido")

    def test_lo_que_termina_con_el_encargo_detenido_queda_registrado_y_llega_al_continuar(self):
        r, ident = self.montar("parado-1")
        self.detener(r, ident)
        self.termina(r.d / "intento-1-a", ident)

        self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "resuelta")
        self.assertEqual(r.fold().estado, "detenido")
        ordenar(r.reg, r.enc, "continuar", "seguir-1", ahora_iso())
        self.barrer(r)
        doc = self.buzon(r, ident)
        self.assertEqual(doc["estado"], "resuelta")
        self.assertEqual(doc["datos"]["run"], "41274694053")
        self.assertEqual(self.sol(r, ident).estado, "resuelta")

    def test_un_error_despues_de_la_detencion_no_reescribe_la_solicitud(self):
        r, ident = self.montar("parado-2")
        self.detener(r, ident)
        self.termina(r.d / "intento-1-a", ident, estado="error")

        self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "detenida")


if __name__ == "__main__":
    unittest.main()
