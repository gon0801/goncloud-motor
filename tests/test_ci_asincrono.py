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
    autorizacion = ""
    workflow = "pruebas.yml"

    def montar(self, clave: str, lanzada: bool = True):
        """Un revisor que pidió ci.correr; la herramienta sigue corriendo en el
        intento-1 y el barrido la deja `pendiente`. Con `lanzada=False` no hay
        barrido: la solicitud queda como la dejaba el código que aún no
        recordaba su carpeta."""
        r = self.recorrido(clave)
        (r.base / "capacidades.json").write_text(
            json.dumps(
                {
                    "recursos": {
                        "ci-linux": {
                            "tipo": "ci",
                            "autorizacion": self.autorizacion,
                            "repo": "o/r",
                            "workflow": self.workflow,
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
        if not lanzada:
            return r, ident
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

    def test_una_solicitud_registrada_sin_carpeta_la_busca_en_los_intentos(self):
        r, ident = self.montar("tarde-sin-carpeta", lanzada=False)
        self.peticion_archivada(r, ident)
        self.intento(r, "intento-2-b")
        self.termina(r.d / "intento-1-a", ident)
        self.assertEqual(self.sol(r, ident).carpeta, "")

        with mock.patch.object(nec, "_ejecutar", side_effect=AssertionError("no se repite")):
            self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "resuelta")
        doc = self.buzon(r, ident)
        self.assertEqual(doc["estado"], "resuelta")
        self.assertEqual(doc["datos"]["run"], "41274694053")
        publico = Path(doc["datos"]["log"])
        self.assertTrue(publico.is_relative_to(r.espacio))
        self.assertEqual(publico.read_text(encoding="utf-8"), LOG_CI)

    def marca_de(self, r, ident: str, **campos) -> Path:
        marca = r.d / "intento-1-a" / "herramientas" / f"{ident}.json"
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(
            json.dumps({"pid": 1, "inicio": "t0", "solicitud": ident, **campos})
        )
        return marca

    def pedida_aparte(self, r, ident: str) -> None:
        """Una segunda solicitud, que no es la inspección ligada, hecha en el
        intento vigente: al cambiar el intento deja de ser vigente."""
        enc = r.fold()
        peticiones = r.espacio / ".interno" / "buzon" / "peticiones"
        (peticiones / f"{ident}.json").write_text(
            json.dumps(
                {
                    "id": ident,
                    "encargo": enc.id,
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "generacion": enc.generacion,
                    "conductor": "",
                    "operacion": "ci.correr",
                    "recurso": "ci-linux",
                    "parametros": {"job": "otro-job"},
                    "motivo": "otra línea base",
                }
            )
        )
        corriendo = {"estado": "pendiente", "motivo": "el CI sigue corriendo"}
        with mock.patch.object(nec, "_ejecutar", return_value=corriendo):
            self.barrer(r)
        self.assertEqual(self.sol(r, ident).estado, "pendiente")

    def test_una_corrida_en_curso_no_se_da_por_tardia_al_cambiar_el_intento(self):
        r, _ligada = self.montar("tarde-5")
        ident = "ci_aparte"
        self.pedida_aparte(r, ident)
        self.abrir_turno(r)
        self.cerrar_turno(r)
        self.marca_de(r, ident)
        enc = r.fold()
        self.assertFalse(nec._vigente(enc, self.sol(r, ident)), "el intento ya cambió")
        pedidas: list[str] = []

        def ejecutar(registro, enc, sol, *resto):
            pedidas.append(sol.id)
            return {"estado": "pendiente", "motivo": "el CI sigue corriendo"}

        with mock.patch.object(nec, "_ejecutar", side_effect=ejecutar), mock.patch(
            "motor.plataforma.pid_vivo", return_value=True
        ), mock.patch("motor.plataforma.hora_inicio", return_value="t0"):
            self.barrer(r)

        self.assertNotIn(ident, pedidas)
        self.assertEqual(self.sol(r, ident).estado, "pendiente")
        doc = self.buzon(r, ident)
        self.assertEqual(doc["estado"], "pendiente")
        self.assertIn("sigue en curso", doc["motivo"])

    def test_una_herramienta_muerta_sin_resultado_no_deja_la_solicitud_pendiente(self):
        r, ident = self.montar("tarde-6")
        self.peticion_archivada(r, ident)
        self.intento(r, "intento-2-b")
        self.marca_de(r, ident)

        with mock.patch("motor.plataforma.pid_vivo", return_value=False):
            self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "error")
        self.assertEqual(self.buzon(r, ident)["estado"], "error")

    def test_un_pid_reusado_cuenta_como_herramienta_muerta(self):
        r, ident = self.montar("tarde-7")
        self.peticion_archivada(r, ident)
        self.intento(r, "intento-2-b")
        self.marca_de(r, ident)

        with mock.patch("motor.plataforma.pid_vivo", return_value=True), mock.patch(
            "motor.plataforma.hora_inicio", return_value="otro-proceso"
        ):
            self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "error")

    def test_si_no_se_puede_comprobar_que_murio_la_solicitud_espera(self):
        for clave, campos, hora_inicio in (
            ("tarde-8", {"inicio": ""}, "t0"),
            ("tarde-9", {}, ""),
            ("tarde-10", {"pid": 0}, "t0"),
        ):
            with self.subTest(clave):
                r, ident = self.montar(clave)
                self.peticion_archivada(r, ident)
                self.intento(r, "intento-2-b")
                self.marca_de(r, ident, **campos)

                with mock.patch("motor.plataforma.pid_vivo", return_value=True), mock.patch(
                    "motor.plataforma.hora_inicio", return_value=hora_inicio
                ), mock.patch.object(nec, "_ejecutar", side_effect=AssertionError("no se relanza")):
                    self.barrer(r)

                self.assertEqual(self.sol(r, ident).estado, "pendiente")


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


class _CorridaViva(_CiAsincrono):
    """Procesos de verdad como herramientas: la prueba mide quién sigue vivo."""

    def setUp(self):
        self.herramientas: list[subprocess.Popen] = []
        for parche in (
            mock.patch("motor.plataforma.hora_inicio", return_value="t0"),
            mock.patch(
                "motor.plataforma.pid_vivo",
                side_effect=lambda pid: any(
                    p.pid == pid and p.poll() is None for p in self.herramientas
                ),
            ),
            mock.patch("motor.plataforma.matar_proc", side_effect=self.matar),
            mock.patch.object(nec, "CI_GRACIA_S", 10),
        ):
            parche.start()
            self.addCleanup(parche.stop)

    def matar(self, pid, inicio):
        for p in self.herramientas:
            if p.pid == pid:
                p.kill()
                p.wait()
        return True

    def lanzar(self, r, ident: str, operacion: str, intento: str = "intento-1-a"):
        proceso = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
        )
        self.herramientas.append(proceso)
        self.addCleanup(proceso.wait)
        self.addCleanup(proceso.kill)
        marca = r.d / intento / "herramientas" / f"{ident}.json"
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(
            json.dumps(
                {"pid": proceso.pid, "inicio": "t0", "solicitud": ident, "operacion": operacion}
            )
        )
        return proceso, marca


class MatarElTurnoNoMataLaCorridaDeCi(_CorridaViva):
    """ci_base_2: la sesión del responsable se marcó `colgada`, `ciclo.barrer`
    cerró las herramientas del intento y mató también la corrida de CI."""

    def matar_el_turno(self, r):
        matar = {"matar": True, "lanzar": False, "eventos": []}
        with mock.patch("motor.encargos.ciclo._decision", return_value=matar):
            self.barrer(r)

    def test_matar_el_turno_no_mata_la_corrida_de_ci_pero_si_las_demas_herramientas(self):
        r, ident = self.montar("sobrevive-1")
        ci, marca_ci = self.lanzar(r, ident, "ci.correr")
        otra, marca_otra = self.lanzar(r, "consulta_1", "sqlite.consultar")

        self.matar_el_turno(r)

        self.assertIsNone(ci.poll(), "la corrida de CI sigue viva")
        self.assertTrue(marca_ci.is_file(), "su marca queda para recoger el resultado")
        self.assertIsNotNone(otra.poll(), "una consulta sí depende del turno")
        self.assertTrue(marca_otra.with_suffix(".hecha").is_file())
        self.assertEqual(self.sol(r, ident).estado, "pendiente")

    def test_la_corrida_que_sobrevivio_entrega_su_resultado_despues(self):
        r, ident = self.montar("sobrevive-2")
        ci, _marca = self.lanzar(r, ident, "ci.correr")
        self.matar_el_turno(r)
        self.intento(r, "intento-2-b")
        self.termina(r.d / "intento-1-a", ident)
        ci.kill()
        ci.wait()

        self.barrer(r)

        self.assertEqual(self.sol(r, ident).estado, "resuelta")
        self.assertEqual(self.buzon(r, ident)["datos"]["run"], "41274694053")


class ParoCancelarSiDetienenLaCorrida(_CorridaViva):
    def test_parar_cierra_la_corrida_de_un_intento_anterior(self):
        r, ident = self.montar("detiene-1")
        ci, marca = self.lanzar(r, ident, "ci.correr")
        self.intento(r, "intento-2-b")

        ordenar(r.reg, r.enc, "parar", "parar-1", ahora_iso())
        self.barrer(r)

        self.assertIsNotNone(ci.poll(), "parar detiene la corrida aunque sea de otro intento")
        self.assertTrue(marca.with_suffix(".hecha").is_file())

    def test_cancelar_tambien(self):
        r, ident = self.montar("detiene-2")
        ci, marca = self.lanzar(r, ident, "ci.correr")

        ordenar(r.reg, r.enc, "cancelar", "cancelar-1", ahora_iso())
        self.barrer(r)

        self.assertIsNotNone(ci.poll())
        self.assertTrue(marca.with_suffix(".hecha").is_file())


class UnCiIgualReusaLaCorridaViva(_CorridaViva):
    """El agente pidió dos veces la misma línea base porque la primera no
    llegaba: 18 minutos de CI cada una."""

    def pedir_otra(self, r, ident: str, job: str = "jaula-linux") -> list[list[str]]:
        enc = r.fold()
        peticiones = r.espacio / ".interno" / "buzon" / "peticiones"
        (peticiones / f"{ident}.json").write_text(
            json.dumps(
                {
                    "id": ident,
                    "encargo": enc.id,
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "generacion": enc.generacion,
                    "conductor": "",
                    "operacion": "ci.correr",
                    "recurso": "ci-linux",
                    "parametros": {"job": job},
                    "motivo": "la línea base otra vez",
                }
            )
        )
        lanzadas: list[list[str]] = []

        def falso_popen(argv, **kw):
            lanzadas.append(argv)
            return mock.Mock(pid=2**22, poll=lambda: None)

        with mock.patch.object(nec, "ESPERA_CI_S", 0.2), mock.patch.object(
            nec.subprocess, "Popen", side_effect=falso_popen
        ):
            self.barrer(r)
        return lanzadas

    def corrida_en_curso(self, clave: str):
        r, ident = self.montar(clave)
        self.lanzar(r, ident, "ci.correr")
        return r, ident

    def test_el_mismo_recurso_job_y_version_no_lanza_otra_corrida(self):
        r, primera = self.corrida_en_curso("reuso-1")

        lanzadas = self.pedir_otra(r, "ci_base_2")

        self.assertEqual(lanzadas, [])
        segunda = self.sol(r, "ci_base_2")
        self.assertEqual(segunda.carpeta, self.sol(r, primera).carpeta)
        self.assertEqual(segunda.estado, "pendiente")
        self.assertEqual(self.buzon(r, "ci_base_2")["estado"], "pendiente")

    def test_al_terminar_la_corrida_las_dos_solicitudes_reciben_su_resultado(self):
        r, primera = self.corrida_en_curso("reuso-2")
        self.pedir_otra(r, "ci_base_2")
        self.intento(r, "intento-2-b")
        self.termina(r.d / "intento-1-a", primera)
        self.herramientas[0].kill()
        self.herramientas[0].wait()

        self.barrer(r)

        for ident in (primera, "ci_base_2"):
            self.assertEqual(self.sol(r, ident).estado, "resuelta", ident)
            doc = self.buzon(r, ident)
            self.assertEqual(doc["datos"]["run"], "41274694053")
            self.assertTrue(Path(doc["datos"]["log"]).is_relative_to(r.espacio))

    def test_otro_job_si_lanza_su_propia_corrida(self):
        r, _primera = self.corrida_en_curso("reuso-3")

        lanzadas = self.pedir_otra(r, "ci_otro_job", job="otro-job")

        self.assertEqual(len(lanzadas), 1)

    def test_otra_version_del_producto_si_lanza_su_propia_corrida(self):
        r, _primera = self.corrida_en_curso("reuso-4")
        (r.espacio / "dato.txt").write_text("43")

        lanzadas = self.pedir_otra(r, "ci_nueva_version")

        self.assertEqual(len(lanzadas), 1)

    def configurar(self, r, **cambios) -> None:
        ruta = r.base / "capacidades.json"
        doc = json.loads(ruta.read_text())
        doc["recursos"]["ci-linux"].update(cambios)
        ruta.write_text(json.dumps(doc))

    def test_otra_configuracion_del_recurso_si_lanza_su_propia_corrida(self):
        r, _primera = self.corrida_en_curso("reuso-6")
        self.configurar(r, workflow="otra-bateria.yml", repo="otro/repo")

        lanzadas = self.pedir_otra(r, "ci_otra_config")

        self.assertEqual(len(lanzadas), 1)
        self.assertNotEqual(self.sol(r, "ci_otra_config").carpeta, self.sol(r, _primera).carpeta)

    def test_la_misma_configuracion_con_otro_orden_de_claves_se_reusa(self):
        r, _primera = self.corrida_en_curso("reuso-7")
        doc = json.loads((r.base / "capacidades.json").read_text())
        recurso = doc["recursos"]["ci-linux"]
        doc["recursos"]["ci-linux"] = dict(reversed(list(recurso.items())))
        (r.base / "capacidades.json").write_text(json.dumps(doc))

        self.assertEqual(self.pedir_otra(r, "ci_base_2"), [])

    def autorizar(self, r) -> None:
        enc = r.fold()
        r.reg.anotar(
            r.enc,
            {
                "kind": "autorizacion",
                "data": {
                    "recurso": "ci-linux",
                    "operacion": "ci.correr",
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "texto": "si",
                    "autor": "propietario",
                    "hora": ahora_iso(),
                },
            },
            "t-autoriza",
        )

    def test_un_recurso_que_pide_autorizacion_no_se_reusa_sin_ella(self):
        self.autorizacion = "propietario"
        r, _primera = self.corrida_en_curso("reuso-8")

        lanzadas = self.pedir_otra(r, "ci_sin_permiso")

        self.assertEqual(len(lanzadas), 1, "la herramienta decide si pregunta; no se une a la ajena")
        self.assertNotEqual(self.sol(r, "ci_sin_permiso").carpeta, self.sol(r, _primera).carpeta)

    def test_con_la_autorizacion_del_propietario_si_se_reusa(self):
        self.autorizacion = "propietario"
        r, primera = self.corrida_en_curso("reuso-9")
        self.autorizar(r)

        lanzadas = self.pedir_otra(r, "ci_con_permiso")

        self.assertEqual(lanzadas, [])
        self.assertEqual(self.sol(r, "ci_con_permiso").carpeta, self.sol(r, primera).carpeta)

    def test_si_la_primera_ya_murio_se_lanza_una_nueva(self):
        r, primera = self.corrida_en_curso("reuso-5")
        self.herramientas[0].kill()
        self.herramientas[0].wait()

        lanzadas = self.pedir_otra(r, "ci_base_2")

        self.assertEqual(len(lanzadas), 1)


if __name__ == "__main__":
    unittest.main()


