"""Regresión del incidente del 29 de octubre en la Mini.

El responsable terminó con código cero tras explicar que no podía trabajar: el
temporal de su shell caía fuera de las rutas escriturables de la jaula. El motor
lo tomó por producto listo, convocó al revisor y convirtió su fallo de
instrumento en otra tarea del responsable, 419 veces.

El caso recorre los llamadores reales (`_incorporar` -> `_incorporar_responsable`
y `_incorporar_revisor`, y `barrer` -> `decision_proceso`) con un lanzador de
ensayo que solo cuenta invocaciones. No llama a proveedores ni a un modelo.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.aislamiento import (
    ComandoAislado,
    SondaEntorno,
    lanzar_en_jaula,
    sondear,
    usa_shell_de_git,
    usa_wsl,
)
from motor.encargos.ciclo import _incorporar, ahora_iso, barrer, recibir
from motor.encargos.comprobacion import firma_producto
from motor.encargos.dominio import fuentes_autorizadas, vista
from motor.encargos.registro import Registro

IMPEDIMENTO = SondaEntorno(
    False,
    "no se puede crear el temporal del CLI en /private/tmp/claude-501/trabajo",
    "que el temporal del CLI caiga en una ruta escribible del aislamiento",
)
RECUPERADO = SondaEntorno(True)
HAY_JAULA = Path("/usr/bin/sandbox-exec").is_file()


class Banco:
    """El test hace de CLI y de entorno; nadie ejecuta un modelo."""

    def __init__(self, tmp: str, sonda: dict, modelos_solicitados: tuple = ()):
        self.sonda = sonda
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        self.hora = ahora_iso()
        self.enc = recibir(
            self.reg, "Cambia los modelos del motor", "mini-1", self.hora,
            modelos_solicitados=modelos_solicitados,
        )["encargo"]
        self.espacio = self.base / "espacio" / self.enc
        self.espacio.mkdir(parents=True, exist_ok=True)
        self.directorio = self.base / "ejecucion" / self.enc / "intento-1-token"
        self.directorio.mkdir(parents=True, exist_ok=True)
        self.resultado = self.directorio / "resultado.json"
        self.lanzamientos: list[str] = []

    def fold(self):
        enc = self.reg.fold(self.enc)
        assert enc is not None
        return enc

    def eventos(self, kind: str) -> list[dict]:
        return [e for e in self.reg.eventos(self.enc) if e["kind"] == kind]

    def anotar(self, kind: str, data: dict, clave: str) -> str:
        return self.reg.anotar(self.enc, {"kind": kind, "data": data}, clave)

    def correr(self, marca: str = "1") -> None:
        """Un intento que el runner ya cerró con código cero."""
        e = self.fold()
        generacion = e.generacion + 1
        self.anotar(
            "reserva",
            {"rol": e.rol or "responsable", "generacion": generacion},
            f"t-reserva:{generacion}",
        )
        self.anotar("reserva_cerrada", {}, f"t-cerrada:{generacion}")
        self.anotar(
            "estrategia",
            {
                "rol": e.rol or "responsable",
                "estrategia": e.estrategia,
                "motivo": e.motivo,
                "siguiente": e.siguiente,
            },
            f"t-rol:{generacion}",
        )
        (self.directorio / "outcome.json").write_text(
            json.dumps(
                {
                    "exit": 0,
                    "token": marca,
                    "fallo_runtime": "",
                    "autenticacion_expirada": False,
                    "sesion_runtime": "",
                }
            )
        )
        (self.directorio / "incorporado").unlink(missing_ok=True)
        _incorporar(self.reg, self.fold(), self.espacio, self.hora, self.directorio)

    def juicio(self, contenido) -> None:
        (self.directorio / "revision.json").write_text(
            json.dumps(
                {
                    "observaciones": str(self.directorio / "observaciones.json"),
                    "resultado": str(self.resultado),
                    "version": firma_producto(self.espacio),
                }
            )
        )
        self.resultado.write_text(
            contenido if isinstance(contenido, str) else json.dumps(contenido)
        )

    def referencia_con_version(self, version: str) -> None:
        ruta = self.directorio / "revision.json"
        referencia = json.loads(ruta.read_text())
        referencia["version"] = version
        ruta.write_text(json.dumps(referencia))

    def fallo_de_comprobacion(self, causa: str) -> None:
        self.juicio(
            {
                "version": firma_producto(self.espacio),
                "veredicto": "no_concluyente",
                "evidencia": "el comprobador no llegó a ejecutarse",
                "instrumento": {"causa": causa},
            }
        )

    def lanzador(self, enc, prompt, espacio, sesion):
        self.lanzamientos.append(f"{enc.rol}:{enc.generacion}")
        return ["/bin/echo", "sin proveedor"]

    def barrer(self, veces: int = 1) -> None:
        for _ in range(veces):
            barrer(self.reg, ahora_iso(), time.time(), self.lanzador)


class BucleInstrumento(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sonda = {"actual": IMPEDIMENTO}
        parche = mock.patch(
            "motor.encargos.aislamiento.sondear",
            side_effect=lambda *a, **k: self.sonda["actual"],
        )
        parche.start()
        self.addCleanup(parche.stop)
        # El lanzamiento de ensayo se cuenta y se corta antes de tocar el host.
        for destino, valor in (
            ("motor.encargos.ciclo.preparar_administrado", None),
            ("motor.encargos.ciclo.lanzar_administrado", {"pid": os.getpid(), "inicio": "ensayo"}),
        ):
            parche = mock.patch(destino, return_value=valor)
            parche.start()
            self.addCleanup(parche.stop)
        parche = mock.patch(
            "motor.flota.runtime.exigir_poteto",
            return_value=(Path("/tmp/poteto-mode/SKILL.md"), "digest-de-ensayo"),
        )
        parche.start()
        self.addCleanup(parche.stop)

    def banco(self) -> Banco:
        banco = Banco(self._tmp.name, self.sonda)
        self.addCleanup(banco.reg.close)
        (banco.espacio / "modelos.toml").write_text("modelos = {}\n")
        return banco

    def test_codigo_cero_sin_avance_no_convoca_revisor_y_reanuda_al_recuperar(self):
        banco = self.banco()
        firma = firma_producto(banco.espacio)

        # 1. El responsable salió con código cero y el entorno no le dejó trabajar.
        banco.correr()
        e = banco.fold()
        self.assertEqual(banco.eventos("producto_listo"), [])
        self.assertEqual(e.rol, "responsable")
        self.assertTrue(e.impedimento)
        self.assertIn("temporal del CLI", e.impedimento_causa)
        self.assertIn("escribible", e.impedimento_condicion)
        self.assertEqual(firma_producto(banco.espacio), firma)
        mostrado = vista(e)["impedimento"]
        self.assertEqual(mostrado["clase"], e.impedimento)
        self.assertIn("temporal del CLI", mostrado["causa"])
        self.assertTrue(mostrado["condicion"])

        # 2. Ningún barrido con las mismas condiciones vuelve a convocar a nadie,
        #    tampoco después de reabrir el registro como tras un reinicio.
        banco.barrer(3)
        self.assertEqual(banco.lanzamientos, [])
        banco.reg.close()
        banco.reg = Registro(banco.base)
        banco.barrer(2)
        self.assertEqual(banco.lanzamientos, [])
        self.assertEqual(banco.eventos("producto_listo"), [])
        self.assertEqual(banco.eventos("revision"), [])
        self.assertEqual(len(banco.eventos("impedimento")), 1)
        self.assertEqual(banco.fold().impedimento, e.impedimento)

        # 3. La recuperación comprobada del entorno retoma el mismo encargo.
        self.sonda["actual"] = RECUPERADO
        banco.barrer()
        self.assertEqual(len(banco.lanzamientos), 1)
        self.assertTrue(banco.lanzamientos[0].startswith("responsable:"))
        e = banco.fold()
        self.assertEqual(e.impedimento, "")
        self.assertEqual(len(banco.eventos("impedimento_resuelto")), 1)
        self.assertEqual(firma_producto(banco.espacio), firma)
        self.assertEqual(vista(e)["impedimento"], None)
        # El intento reservado cierra la puerta a un segundo lanzamiento.
        banco.barrer(2)
        self.assertEqual(len(banco.lanzamientos), 1)

    def test_el_fallo_de_instrumento_del_revisor_no_vuelve_al_responsable(self):
        banco = self.banco()
        with mock.patch("motor.encargos.aislamiento.sondear", return_value=RECUPERADO):
            banco.correr()
            self.assertEqual(banco.fold().rol, "revisor")
            banco.fallo_de_comprobacion("Bash no está disponible en esta jaula")

        # El revisor falla con el entorno roto: queda el impedimento, no otra tarea.
        self.sonda["actual"] = IMPEDIMENTO
        (banco.directorio / "incorporado").unlink(missing_ok=True)
        _incorporar(banco.reg, banco.fold(), banco.espacio, banco.hora, banco.directorio)
        e = banco.fold()
        self.assertEqual(e.rol, "revisor")
        self.assertTrue(e.impedimento)
        self.assertIn("temporal del CLI", e.impedimento_causa)
        self.assertEqual(banco.eventos("instrumento_fallo"), [])
        self.assertEqual(banco.eventos("revision"), [])
        banco.barrer(2)
        self.assertEqual(banco.lanzamientos, [])

    def test_el_mismo_instrumento_cuatro_veces_no_habilita_otro_lanzamiento(self):
        banco = self.banco()
        with mock.patch("motor.encargos.aislamiento.sondear", return_value=RECUPERADO):
            for vuelta in range(4):
                banco.correr(f"responsable-{vuelta}")
                banco.fallo_de_comprobacion(
                    "el mismo comprobador externo sigue indisponible"
                )
                banco.correr(f"revisor-{vuelta}")
        e = banco.fold()
        self.assertTrue(e.impedimento)
        self.assertIn("indisponible", e.impedimento_causa)
        self.assertEqual(e.impedimento_fuente, "instrumento")
        self.assertLessEqual(len(banco.eventos("producto_listo")), 2)
        banco.barrer(3)
        self.assertEqual(banco.lanzamientos, [])

    def test_dos_causas_distintas_no_se_tratan_como_repeticion(self):
        banco = self.banco()
        with mock.patch("motor.encargos.aislamiento.sondear", return_value=RECUPERADO):
            banco.correr()
            banco.juicio("{")
            banco.correr("juicio-1")
            primero = banco.fold()
            self.assertEqual(primero.rol, "revisor")
            self.assertFalse(primero.impedimento)

            # Un error distinto es un intento de reparación, no una repetición.
            banco.juicio(
                {
                    "version": firma_producto(banco.espacio),
                    "evidencia": "ya es JSON válido, falta el veredicto",
                }
            )
            banco.correr("juicio-2")
            segundo = banco.fold()
            self.assertEqual(segundo.rol, "revisor")
            self.assertFalse(segundo.impedimento)
            self.assertNotIn("sin información nueva", segundo.motivo)

    def test_una_recaida_se_registra_como_causa_nueva(self):
        banco = self.banco()
        banco.correr()
        banco.barrer(2)
        self.assertEqual(len(banco.eventos("impedimento")), 1)

        self.sonda["actual"] = RECUPERADO
        banco.barrer()
        self.assertEqual(banco.fold().impedimento, "")
        self.assertEqual(len(banco.eventos("impedimento_resuelto")), 1)

        # El mismo impedimento vuelve. No es una retransmisión del anterior.
        self.sonda["actual"] = IMPEDIMENTO
        banco.correr("reincidencia")
        e = banco.fold()
        self.assertTrue(e.impedimento)
        self.assertEqual(e.impedimento, "entorno")
        self.assertEqual(len(banco.eventos("impedimento")), 2)
        lanzamientos = len(banco.lanzamientos)
        banco.barrer(3)
        self.assertEqual(len(banco.lanzamientos), lanzamientos)

    def test_continuar_no_levanta_un_impedimento_del_entorno(self):
        banco = self.banco()
        banco.correr()
        self._parar_y_continuar(banco)
        e = banco.fold()
        self.assertEqual(e.estado, "recibido")
        self.assertTrue(e.impedimento)
        self.assertEqual(banco.eventos("impedimento_resuelto"), [])
        banco.barrer(2)
        self.assertEqual(banco.lanzamientos, [])

    def _parar_y_continuar(self, banco) -> None:
        banco.anotar("orden", {"accion": "parar", "hora": banco.hora}, "t-parar")
        banco.anotar("arbol", {"observacion": "ausente", "hora": banco.hora}, "t-ausente")
        self.assertEqual(banco.fold().estado, "detenido")
        banco.anotar("orden", {"accion": "continuar", "hora": banco.hora}, "t-continuar")

    def test_continuar_no_saltea_el_instrumento_pendiente(self):
        banco = self.banco()
        archivo = self._bloquear_comprobaciones(banco)
        self._parar_y_continuar(banco)
        e = banco.fold()
        self.assertEqual(e.estado, "recibido")
        self.assertEqual(e.impedimento, "comprobaciones.json")
        self.assertEqual(archivo.read_text(), "{")
        self.assertEqual(banco.eventos("impedimento_resuelto"), [])
        banco.barrer(3)
        self.assertEqual(banco.lanzamientos, [])
        self.assertTrue(banco.fold().impedimento)

    def _bloquear_comprobaciones(self, banco) -> Path:
        interno = banco.espacio / ".interno"
        interno.mkdir(exist_ok=True)
        archivo = interno / "comprobaciones.json"
        archivo.write_text("{")
        with mock.patch("motor.encargos.aislamiento.sondear", return_value=RECUPERADO):
            banco.correr("comprobaciones-1")
            banco.correr("comprobaciones-2")
        self.assertEqual(banco.fold().impedimento, "comprobaciones.json")
        self.assertEqual(banco.fold().impedimento_fuente, "instrumento")
        return archivo

    def test_reparar_el_archivo_levanta_la_espera_sin_orden(self):
        banco = self.banco()
        archivo = self._bloquear_comprobaciones(banco)
        archivo.write_text("[]")
        with mock.patch("motor.encargos.aislamiento.sondear", return_value=RECUPERADO):
            banco.barrer(3)
        e = banco.fold()
        self.assertEqual(e.impedimento, "")
        self.assertGreaterEqual(len(banco.eventos("impedimento_resuelto")), 1)
        self.assertEqual(len(banco.lanzamientos), 1)

    def test_un_fallo_del_entorno_no_borra_el_del_instrumento(self):
        banco = self.banco()
        archivo = self._bloquear_comprobaciones(banco)
        self.sonda["actual"] = IMPEDIMENTO
        banco.barrer()
        self.assertEqual(banco.fold().impedimento_fuente, "instrumento")
        self.sonda["actual"] = RECUPERADO
        banco.barrer(2)
        e = banco.fold()
        self.assertEqual(archivo.read_text(), "{")
        self.assertTrue(e.impedimento)
        self.assertEqual(e.impedimento, "comprobaciones.json")
        self.assertEqual(banco.lanzamientos, [])

    def test_el_mismo_defecto_con_otro_identificador_no_reinicia(self):
        banco = self.banco()
        with mock.patch("motor.encargos.aislamiento.sondear", return_value=RECUPERADO):
            for vuelta in range(4):
                banco.correr(f"responsable-token-{vuelta}")
                banco.fallo_de_comprobacion(
                    f"comprobador externo no disponible; intento=intento-{vuelta}"
                )
                banco.correr(f"revisor-token-{vuelta}")
                if banco.fold().impedimento:
                    break
        e = banco.fold()
        self.assertEqual(e.impedimento, "comprobacion")
        self.assertFalse(e.impedimentos[0].verificable)
        self.assertEqual(len(banco.eventos("producto_listo")), 2)
        banco.barrer(3)
        self.assertEqual(banco.lanzamientos, [])

    def test_fases_ciclicas_no_se_dan_por_reparadas_sin_cambiar_el_archivo(self):
        banco = self.banco()
        self.sonda["actual"] = RECUPERADO
        interno = banco.espacio / ".interno"
        interno.mkdir(exist_ok=True)
        archivo = interno / "fases.json"
        archivo.write_text(
            json.dumps(
                {
                    "fases": [
                        {"id": "a", "resultado": "resultado A", "requiere": ["b"]},
                        {"id": "b", "resultado": "resultado B", "requiere": ["a"]},
                    ]
                }
            )
        )
        banco.correr("fases-ciclicas-1")
        banco.correr("fases-ciclicas-2")
        self.assertEqual(banco.fold().impedimento, "fases.json")
        self.assertEqual(len(banco.fold().fases), 0)

        # La forma ya era aceptable: lo que falla es adoptarla. Sin reparación
        # no hay resolución ni lanzamiento.
        banco.barrer(3)
        self.assertTrue(banco.fold().impedimento)
        self.assertEqual(banco.eventos("impedimento_resuelto"), [])
        self.assertEqual(banco.lanzamientos, [])

        archivo.write_text(
            json.dumps(
                {
                    "fases": [
                        {"id": "a", "resultado": "resultado A"},
                        {"id": "b", "resultado": "resultado B", "requiere": ["a"]},
                    ]
                }
            )
        )
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertGreaterEqual(len(banco.eventos("impedimento_resuelto")), 1)
        self.assertEqual(len(banco.lanzamientos), 1)

    def test_el_juicio_reparado_levanta_la_espera(self):
        banco = self.banco()
        self.sonda["actual"] = RECUPERADO
        banco.correr()
        version = firma_producto(banco.espacio)
        for numero in range(2):
            banco.juicio({"version": version})
            banco.correr(f"sin-veredicto-{numero}")
        self.assertEqual(banco.fold().impedimento, "juicio:esquema:veredicto")

        banco.juicio(
            {
                "version": version,
                "veredicto": "aprobado",
                "evidencia": "Comportamiento comprobado",
            }
        )
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertGreaterEqual(len(banco.eventos("impedimento_resuelto")), 1)
        self.assertEqual(len(banco.lanzamientos), 1)

    def test_dos_defectos_del_juicio_distintos_no_son_la_misma_falla(self):
        banco = self.banco()
        self.sonda["actual"] = RECUPERADO
        banco.correr()
        version = firma_producto(banco.espacio)
        banco.juicio({"version": version})
        banco.correr("sin-veredicto")
        self.assertFalse(banco.fold().impedimento)

        # Corregir el veredicto y fallar después por la evidencia es avanzar.
        banco.juicio({"version": version, "veredicto": "aprobado"})
        banco.correr("veredicto-corregido")
        clases = [
            ev["data"]["clase"] for ev in banco.eventos("instrumento_fallo")
        ]
        self.assertEqual(
            clases, ["juicio:esquema:veredicto", "juicio:esquema:evidencia"]
        )
        self.assertFalse(banco.fold().impedimento)
        banco.barrer()
        self.assertEqual(len(banco.lanzamientos), 1)

    def test_un_juicio_con_codificacion_invalida_es_fallo_de_instrumento(self):
        banco = self.banco()
        self.sonda["actual"] = RECUPERADO
        banco.correr()
        banco.juicio({"version": firma_producto(banco.espacio)})
        banco.resultado.write_bytes(b"\xff")

        # No escapa ninguna excepción: queda registrado y el intento incorporado.
        banco.correr("juicio-utf8-invalido")
        fallos = banco.eventos("instrumento_fallo")
        self.assertEqual(len(fallos), 1)
        self.assertEqual(fallos[0]["data"]["clase"], "juicio:lectura")
        self.assertTrue((banco.directorio / "incorporado").exists())
        self.assertFalse(banco.fold().impedimento)

    def test_la_referencia_obsoleta_no_relanza_al_revisor(self):
        banco = self.banco()
        self.sonda["actual"] = RECUPERADO
        banco.correr()
        version = firma_producto(banco.espacio)
        valido = {
            "version": version,
            "veredicto": "aprobado",
            "evidencia": "Comportamiento comprobado",
        }
        for numero in range(2):
            banco.juicio(valido)
            banco.referencia_con_version("version-que-no-es")
            banco.correr(f"stale-{numero}")
        clases = [
            ev["data"]["clase"] for ev in banco.eventos("instrumento_fallo")
        ]
        self.assertEqual(clases, ["juicio:referencia", "juicio:referencia"])
        self.assertEqual(banco.fold().impedimento, "juicio:referencia")
        banco.barrer(3)
        self.assertEqual(banco.lanzamientos, [])

        # La referencia de la versión vigente levanta la espera sin orden.
        banco.juicio(valido)
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertGreaterEqual(len(banco.eventos("impedimento_resuelto")), 1)
        self.assertEqual(len(banco.lanzamientos), 1)

    def test_una_referencia_con_version_ajena_no_levanta_la_espera(self):
        banco = self.banco()
        self.sonda["actual"] = RECUPERADO
        banco.correr()
        (banco.directorio / "revision.json").unlink(missing_ok=True)
        banco.correr("sinref-1")
        banco.correr("sinref-2")
        self.assertEqual(banco.fold().impedimento, "juicio:referencia")

        # El juicio al que apunta ya valida, pero la referencia es de otra
        # versión: es la misma que la incorporación seguiría rechazando.
        banco.juicio(
            {
                "version": firma_producto(banco.espacio),
                "veredicto": "aprobado",
                "evidencia": "Comportamiento comprobado",
            }
        )
        banco.referencia_con_version("version-que-no-es")
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "juicio:referencia")
        self.assertEqual(banco.eventos("impedimento_resuelto"), [])
        self.assertEqual(banco.lanzamientos, [])

    def test_la_aprobacion_sin_enumerar_efectos_no_relanza_al_revisor(self):
        banco = self.banco()
        self.sonda["actual"] = RECUPERADO
        banco.correr()
        interno = banco.espacio / ".interno"
        interno.mkdir(exist_ok=True)
        (interno / "pedido-efecto.json").write_text(
            json.dumps(
                [
                    {
                        "id": "efecto-1",
                        "operacion": "publicar",
                        "clave": "clave-1",
                        "cuerpo": "texto del efecto",
                    }
                ]
            )
        )
        version = firma_producto(banco.espacio)
        for numero in range(2):
            banco.juicio(
                {
                    "version": version,
                    "veredicto": "aprobado",
                    "evidencia": "Comportamiento comprobado",
                    "efectos_pendientes": [],
                }
            )
            banco.correr(f"sin-efectos-{numero}")
        self.assertEqual(banco.fold().impedimento, "juicio:esquema:efectos")
        banco.barrer(3)
        self.assertEqual(banco.lanzamientos, [])

        banco.juicio(
            {
                "version": version,
                "veredicto": "aprobado",
                "evidencia": "Comportamiento comprobado",
                "efectos_pendientes": ["efecto-1"],
            }
        )
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertEqual(len(banco.lanzamientos), 1)

    def test_no_lanza_sin_comprobar_el_entorno(self):
        banco = self.banco()
        banco.barrer()
        self.assertEqual(banco.lanzamientos, [])
        e = banco.fold()
        self.assertTrue(e.impedimento)
        self.assertIn("temporal del CLI", e.impedimento_causa)

    @unittest.skipUnless(HAY_JAULA, "hace falta el aislamiento de macOS")
    def test_la_sonda_no_toca_archivos_del_producto(self):
        espacio = Path(self._tmp.name) / "espacio" / "producto"
        espacio.mkdir(parents=True)
        ajeno = espacio / ".sonda"
        ajeno.write_text("contenido del usuario\n")
        (espacio / "modelos.toml").write_text("modelos = {}\n")
        antes = sorted(p.name for p in espacio.iterdir())

        resultado = sondear(espacio, Path(self._tmp.name) / "runtime" / "sonda")

        self.assertTrue(resultado.ok)
        self.assertTrue(ajeno.is_file())
        self.assertEqual(ajeno.read_text(), "contenido del usuario\n")
        self.assertEqual(sorted(p.name for p in espacio.iterdir()), antes)

    @unittest.skipUnless(HAY_JAULA, "hace falta el aislamiento de macOS")
    def test_una_fuente_declarada_ausente_no_es_exito(self):
        espacio = Path(self._tmp.name) / "espacio" / "producto"
        espacio.mkdir(parents=True)
        fuente = Path(self._tmp.name) / "partida-que-no-esta.csv"
        self.assertFalse(fuente.exists())

        class Pedido:
            adjuntos = (str(fuente),)

        fuentes = fuentes_autorizadas(Pedido())
        self.assertEqual([str(p) for p in fuentes], [str(fuente)])
        resultado = sondear(
            espacio, Path(self._tmp.name) / "runtime" / "fuentes", fuentes
        )
        self.assertFalse(resultado.ok)
        self.assertIn("fuente autorizada", resultado.causa)
        self.assertIn(str(fuente), resultado.causa)

    def test_un_shell_de_git_que_no_arranca_frena_antes_de_lanzar(self):
        causa = (
            "el shell de Git no funciona bajo el aislamiento (codigo 66: "
            "*** fatal error - NtCreateDirectoryObject(\\BaseNamedObjects\\"
            "msys-2.0S5-1888ae32e00d56aa): 0xC0000022)"
        )
        self.sonda["actual"] = SondaEntorno(False, causa, "que el shell arranque")
        banco = self.banco()
        banco.barrer(3)
        self.assertEqual(banco.lanzamientos, [])
        e = banco.fold()
        self.assertEqual(e.impedimento, "entorno")
        self.assertEqual(e.impedimento_causa, causa)


class ShellDeGitAlLanzar(unittest.TestCase):
    """El shell se prueba según el agente que se lanza en ese intento, con la ruta
    `claude, codex` autorizada. Usa el mismo banco del bucle con otra sonda."""

    CAUSA = (
        "el shell de Git no funciona bajo el aislamiento (codigo 66: "
        "*** fatal error - NtCreateDirectoryObject(\\BaseNamedObjects\\"
        "msys-2.0S5-1888ae32e00d56aa): 0xC0000022)"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(mock.patch.stopall)
        self.llamadas: list = []
        self.shell_sano = False
        for destino, valor in (
            ("motor.encargos.ciclo.preparar_administrado", None),
            (
                "motor.encargos.ciclo.lanzar_administrado",
                {"pid": os.getpid(), "inicio": "ensayo"},
            ),
        ):
            mock.patch(destino, return_value=valor).start()
        mock.patch(
            "motor.flota.runtime.exigir_poteto",
            return_value=(Path("/tmp/poteto-mode/SKILL.md"), "digest-de-ensayo"),
        ).start()
        mock.patch("motor.encargos.aislamiento._en_windows", return_value=True).start()
        self.distro_sana = mock.patch(
            "motor.plataforma.jaula_wsl.causa_no_disponible", return_value=""
        )
        self.distro_sana.start()
        self.parche_sonda = mock.patch("motor.encargos.aislamiento.sondear", side_effect=self.sondear)
        self.parche_sonda.start()

    def sondear(self, espacio, runtime, lecturas=(), agente=None):
        self.llamadas.append(agente)
        con_claude = "claude" in (agente if isinstance(agente, tuple) else (agente,))
        if con_claude and not self.shell_sano:
            return SondaEntorno(False, self.CAUSA, "que el shell arranque")
        return RECUPERADO

    def sonda_real_de_claude(self):
        """Claude se sondea con la sonda real (aqui: la de WSL, con la distro sustituida); los
        demas casos, como el chequeo general del barrido, siguen con el doble."""

        def sondar(espacio, runtime, lecturas=(), agente=None):
            if agente == "claude":
                self.llamadas.append(agente)
                return sondear(espacio, runtime, lecturas, agente)
            return RECUPERADO

        self.parche_sonda.stop()
        self.parche_sonda = mock.patch(
            "motor.encargos.aislamiento.sondear", side_effect=sondar
        )
        self.parche_sonda.start()

    def banco_con(self, agente: str, ruta=("claude", "codex"), solicitado=()) -> Banco:
        banco = Banco(self._tmp.name, {"actual": RECUPERADO}, solicitado)
        self.addCleanup(banco.reg.close)
        (banco.espacio / "modelos.toml").write_text("modelos = {}\n")
        banco.anotar(
            "ruta_agentes",
            {"agentes": list(ruta), "hora": ahora_iso()},
            "ruta:ensayo",
        )

        def lanzador(enc, prompt, espacio, sesion):
            banco.lanzamientos.append(f"{enc.rol}:{enc.generacion}:{agente}")
            return ["/bin/echo", "sin proveedor"], {"agente": agente}

        banco.lanzador = lanzador
        return banco

    def test_un_agente_distinto_de_claude_no_se_frena_por_el_shell(self):
        banco = self.banco_con("codex")
        banco.barrer(2)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertEqual(len(banco.lanzamientos), 1)
        self.assertNotIn("claude", self.llamadas)

    def test_claude_con_el_shell_roto_frena_antes_de_lanzar_al_agente(self):
        import motor.encargos.ciclo as ciclo

        banco = self.banco_con("claude")
        banco.barrer()
        e = banco.fold()
        self.assertEqual(e.impedimento, "entorno")
        self.assertEqual(e.impedimento_causa, self.CAUSA)
        self.assertIn("claude", self.llamadas)
        ciclo.lanzar_administrado.assert_not_called()
        ciclo.preparar_administrado.assert_not_called()

    def test_claude_en_windows_se_prueba_en_wsl_y_el_appcontainer_roto_no_lo_frena(self):
        """En la PC msys no arranca en el AppContainer, y claude no lo usa: corre en WSL. Su
        sonda es la de la distro (DeepSeek F1): con el AppContainer inutilizable y la distro
        sana, el lanzamiento sigue."""
        import motor.encargos.ciclo as ciclo

        self.sonda_real_de_claude()
        banco = self.banco_con("claude")
        with (
            mock.patch("motor.encargos.aislamiento.aislar", side_effect=AssertionError("AppContainer")),
            mock.patch("motor.plataforma.jaula_wsl.ejecutar", return_value=(0, "ok\n", "")) as wsl,
        ):
            banco.barrer(2)
        self.assertEqual(banco.fold().impedimento, "")
        wsl.assert_called_once()
        ciclo.lanzar_administrado.assert_called_once()

    def sustituir_sonda(self, funcion):
        self.parche_sonda.stop()
        self.parche_sonda = mock.patch(
            "motor.encargos.aislamiento.sondear", side_effect=funcion
        )
        self.parche_sonda.start()

    def test_la_sonda_no_corre_con_el_turno_tomado(self):
        """Una sonda lenta o colgada no puede detener a los demas encargos (#110 F1): mientras
        corre, otro hilo toma el turno global sin esperar."""
        import threading

        from motor.encargos.frontera import turno
        import motor.encargos.ciclo as ciclo

        banco = self.banco_con("claude")
        visto = []

        def sondar(espacio, runtime, lecturas=(), agente=None):
            if agente == "claude":
                libre = threading.Event()

                def otro():
                    with turno(banco.base):
                        libre.set()

                threading.Thread(target=otro, daemon=True).start()
                visto.append(libre.wait(3))
            return RECUPERADO

        self.sustituir_sonda(sondar)
        banco.barrer(2)
        self.assertEqual(visto, [True])
        ciclo.lanzar_administrado.assert_called_once()

    def lanzador_que_pregunta_por_la_distro(self, banco):
        """Como el del supervisor (cli._supervisor.lanzar): filtra por `_disponible`."""
        from motor.encargos import agentes

        def lanzador(enc, prompt, espacio, sesion):
            if not agentes._disponible("claude", None):
                return None
            return ["/bin/echo", "sin proveedor"], {"agente": "claude"}

        banco.lanzador = lanzador

    def test_la_consulta_de_disponibilidad_de_wsl_no_corre_con_el_turno_tomado(self):
        """Una distro lenta o colgada no puede detener a los demas encargos (#110 F1 aplicado
        a la consulta de WSL): wsl.exe se pregunta antes del turno, y el lanzador, que corre
        con el turno, solo lee la respuesta recordada."""
        import threading

        from motor.encargos.frontera import turno
        from motor.plataforma import jaula_wsl
        import motor.encargos.ciclo as ciclo

        jaula_wsl._CACHE_DISPONIBLE.clear()
        self.addCleanup(jaula_wsl._CACHE_DISPONIBLE.clear)
        self.shell_sano = True
        banco = self.banco_con("claude")
        self.lanzador_que_pregunta_por_la_distro(banco)
        self.distro_sana.stop()
        visto = []

        def consulta(distribucion, orden, *args, **kwargs):
            libre = threading.Event()

            def otro():
                with turno(banco.base):
                    libre.set()

            threading.Thread(target=otro, daemon=True).start()
            visto.append(libre.wait(3))
            return {"causa": ""}

        with (
            mock.patch("motor.plataforma.jaula_wsl._orden", side_effect=consulta),
            mock.patch(
                "motor.plataforma.jaula_wsl.ejecutar", return_value=(0, "ok\n", "")
            ),
        ):
            banco.barrer(2)
        self.assertEqual(visto, [True])
        ciclo.lanzar_administrado.assert_called_once()

    def test_con_el_turno_tomado_el_lanzador_no_arranca_wsl_ni_siquiera_con_la_respuesta_vencida(self):
        from motor.plataforma import jaula_wsl
        import motor.encargos.ciclo as ciclo

        jaula_wsl._CACHE_DISPONIBLE.clear()
        self.addCleanup(jaula_wsl._CACHE_DISPONIBLE.clear)
        self.shell_sano = True
        banco = self.banco_con("claude")
        self.lanzador_que_pregunta_por_la_distro(banco)
        self.distro_sana.stop()
        with (
            mock.patch("motor.encargos.ciclo._consultar_wsl"),
            mock.patch("motor.plataforma.jaula_wsl._orden", side_effect=AssertionError("wsl.exe")) as orden,
        ):
            banco.barrer(2)
        orden.assert_not_called()
        ciclo.lanzar_administrado.assert_not_called()

    def test_la_flota_no_queda_reservada_mientras_la_sonda_corre_ni_si_la_sonda_lanza(self):
        """La sonda del lanzamiento no libera la flota si lanza una excepcion (#110 F3)."""
        import contextlib

        banco = self.banco_con("claude")
        home = Path(self._tmp.name) / "flota"
        (home / "trabajos").mkdir(parents=True)
        (banco.base / "flota-home.json").write_text(json.dumps({"home": str(home)}))
        reserva = home / "trabajos" / f"{banco.enc}.json"
        visto = []

        def sondar(espacio, runtime, lecturas=(), agente=None):
            if agente == "claude":
                visto.append(reserva.exists())
                raise RuntimeError("la sonda se rompio")
            return RECUPERADO

        self.sustituir_sonda(sondar)
        with (
            mock.patch("motor.flota.control.asignar", return_value="g1"),
            mock.patch(
                "motor.flota.control._cerrojo_flota",
                side_effect=lambda _home: contextlib.nullcontext(),
            ),
        ):
            banco.barrer(1)
        self.assertEqual(visto, [False])
        self.assertFalse(reserva.exists())
        (fallo,) = [
            e for e in banco.eventos("instrumento_fallo") if e["data"].get("fuente") == "barrido"
        ]
        self.assertEqual(fallo["data"]["clase"], "barrido:RuntimeError")

    def test_el_impedimento_se_vuelve_a_probar_con_el_agente_que_fallo_no_con_uno_supuesto(self):
        """El impedimento del shell no se ata a claude por el texto de la causa (#110 F2)."""
        banco = self.banco_con("claude")
        banco.barrer(2)
        self.assertEqual(banco.fold().impedimentos[0].agente, "claude")
        self.llamadas.clear()
        banco.barrer(1)
        self.assertIn("claude", self.llamadas)

    def test_un_impedimento_sin_agente_no_se_prueba_como_si_fuera_de_claude(self):
        banco = self.banco_con("codex")
        banco.anotar(
            "impedimento",
            {"clase": "entorno", "causa": self.CAUSA, "condicion": "que el shell arranque",
             "agente": "", "hora": ahora_iso()},
            "imp:sin-agente",
        )
        self.llamadas.clear()
        banco.barrer(1)
        self.assertNotIn("claude", self.llamadas)
        self.assertEqual(banco.fold().impedimento, "")

    def test_si_la_ruta_ya_no_incluye_al_agente_que_fallo_el_impedimento_se_levanta(self):
        import motor.encargos.ciclo as ciclo

        banco = self.banco_con("claude")
        banco.barrer(2)
        self.assertEqual(banco.fold().impedimento, "entorno")
        ciclo.lanzar_administrado.assert_not_called()
        banco.anotar(
            "ruta_agentes", {"agentes": ["codex"], "hora": ahora_iso()}, "ruta:cambio"
        )
        banco.lanzador = lambda enc, prompt, espacio, sesion: (
            ["/bin/echo", "sin proveedor"], {"agente": "codex"}
        )
        self.llamadas.clear()
        banco.barrer(2)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertEqual(len(banco.eventos("impedimento_resuelto")), 1)
        self.assertNotIn("claude", self.llamadas)
        ciclo.lanzar_administrado.assert_called_once()

    def test_el_impedimento_no_se_levanta_sin_probar_si_el_pedido_solicita_al_agente_para_el_rol(self):
        """El lanzador elige primero el agente solicitado para el rol: que la ruta no incluya a
        claude no basta para dar por resuelta su causa (#133 F1)."""
        import motor.encargos.ciclo as ciclo

        banco = self.banco_con(
            "claude", ruta=("codex",), solicitado=(("responsable", "claude", "claude-opus"),)
        )
        banco.barrer(4)
        self.assertEqual(banco.fold().impedimento, "entorno")
        self.assertEqual(banco.fold().impedimentos[0].agente, "claude")
        self.assertEqual(len(banco.eventos("impedimento_resuelto")), 0)
        self.assertEqual(len(banco.eventos("impedimento")), 1)
        ciclo.lanzar_administrado.assert_not_called()

        self.shell_sano = True
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertEqual(len(banco.eventos("impedimento_resuelto")), 1)
        ciclo.lanzar_administrado.assert_called_once()

    def test_el_impedimento_de_claude_se_levanta_si_el_pedido_solicita_a_otro_agente_para_el_rol(self):
        banco = self.banco_con("claude")
        banco.barrer(2)
        self.assertEqual(banco.fold().impedimento, "entorno")
        from dataclasses import replace

        from motor.encargos.ciclo import _verificar_impedimento

        solicitando_codex = replace(
            banco.fold(), modelos_solicitados=(("responsable", "codex", "gpt-5"),)
        )
        self.llamadas.clear()
        resuelto = _verificar_impedimento(
            "entorno", solicitando_codex, banco.espacio, banco.base
        )
        self.assertIs(resuelto, True)
        self.assertNotIn("claude", self.llamadas)

    def test_claude_con_bash_roto_en_wsl_frena_con_la_causa_de_la_distro_y_su_agente(self):
        self.sonda_real_de_claude()
        banco = self.banco_con("claude")
        with mock.patch(
            "motor.plataforma.jaula_wsl.ejecutar",
            return_value=(0, "git:codigo 128: fatal: detected dubious ownership\n", ""),
        ):
            banco.barrer(2)
        e = banco.fold()
        self.assertEqual(e.impedimento, "entorno")
        self.assertEqual(
            e.impedimento_causa,
            "git no funciona en la jaula de WSL (codigo 128: fatal: detected dubious ownership)",
        )
        self.assertEqual(e.impedimentos[0].agente, "claude")

    def test_el_impedimento_del_shell_solo_se_levanta_cuando_el_shell_arranca(self):
        import motor.encargos.ciclo as ciclo

        banco = self.banco_con("claude")
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "entorno")
        ciclo.lanzar_administrado.assert_not_called()
        self.assertEqual(len(banco.eventos("impedimento_resuelto")), 0)

        self.shell_sano = True
        banco.barrer(3)
        self.assertEqual(banco.fold().impedimento, "")
        self.assertEqual(len(banco.eventos("impedimento_resuelto")), 1)
        ciclo.lanzar_administrado.assert_called_once()


class SondaDelShellDeGit(unittest.TestCase):
    """La sonda de la jaula de Windows: la de los agentes que no usan el bash de Git.

    Corre el mismo guion que lanza en la PC, por `sondear`, con `aislar` y el
    host de Windows sustituidos. El shell de Git no es parte de ella (#131 F7):
    quien lo usa corre en WSL y su sonda es la de `SondaDeWsl`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.espacio = self.tmp / "espacio" / "producto"
        self.espacio.mkdir(parents=True)

    def shell(self, nombre: str, cuerpo: str) -> Path:
        ruta = self.tmp / nombre
        ruta.write_text("#!/bin/sh\n" + cuerpo)
        ruta.chmod(0o755)
        return ruta

    def sondear(self, bash: Path | None = None, agente=None) -> SondaEntorno:
        temporal = self.tmp / "temporal"
        temporal.mkdir(exist_ok=True)
        entorno = {
            "PATH": os.environ["PATH"],
            "CLAUDE_CODE_TMPDIR": str(temporal),
            "TMPDIR": str(temporal),
            "HOME": str(self.tmp),
        }
        if bash is not None:
            entorno["MOTOR_SONDA_SHELL"] = str(bash)
            entorno["MOTOR_SONDA_SH"] = str(bash)
        with (
            mock.patch("motor.encargos.aislamiento._en_windows", return_value=True),
            mock.patch(
                "motor.encargos.aislamiento.aislar",
                side_effect=lambda argv, *a, **k: ComandoAislado(
                    argv, entorno, "ensayo"
                ),
            ),
        ):
            return sondear(
                self.espacio, self.tmp / "runtime" / "sonda", agente=agente
            )

    def test_la_sonda_de_windows_pasa_sin_tocar_el_producto(self):
        resultado = self.sondear()
        self.assertTrue(resultado.ok, resultado.causa)
        self.assertEqual(sorted(p.name for p in self.espacio.iterdir()), [])

    def test_el_shell_de_git_no_frena_a_la_sonda_del_appcontainer(self):
        """Aunque el bash de Git no arranque en el AppContainer, esa sonda no lo prueba: lo
        frenaría por un shell que nadie ejecuta ahí. Con `usa_wsl` en falso para `claude`
        (combinación que en producción no existe) la sonda sigue sin leerlo."""
        roto = self.shell("bash-roto", "exit 66\n")
        for agente in (None, "codex", "claude"):
            with self.subTest(agente=agente):
                with mock.patch(
                    "motor.encargos.aislamiento.usa_wsl", return_value=False
                ):
                    resultado = self.sondear(roto, agente=agente)
                self.assertTrue(resultado.ok, resultado.causa)

    def test_el_error_del_proceso_queda_en_la_causa_si_la_sonda_no_termina(self):
        roto = self.shell("bash-sano", 'exec /bin/sh "$@"\n')
        with mock.patch(
            "motor.encargos.aislamiento._ejecutar_en_jaula",
            return_value=(
                3221225781,
                "",
                "Traceback...\nImportError: DLL load failed (0xC0000142)\n",
            ),
        ):
            resultado = self.sondear(roto)
        self.assertFalse(resultado.ok)
        self.assertIn("codigo 3221225781", resultado.causa)
        self.assertIn("DLL load failed (0xC0000142)", resultado.causa)

    def test_sin_error_ni_salida_la_causa_solo_trae_el_codigo(self):
        roto = self.shell("bash-sano", 'exec /bin/sh "$@"\n')
        with mock.patch(
            "motor.encargos.aislamiento._ejecutar_en_jaula", return_value=(7, "", "")
        ):
            resultado = self.sondear(roto)
        self.assertEqual(
            resultado.causa, "la sonda del entorno no terminó: codigo 7"
        )

    def test_una_excepcion_de_la_jaula_no_tumba_a_quien_sondea(self):
        sano = self.shell("bash-sano", 'exec /bin/sh "$@"\n')
        for excepcion in (
            RuntimeError("la jaula requiere una estacion no interactiva"),
            ValueError("la jaula necesita stdin cerrado y salidas administradas"),
            KeyError("SystemRoot"),
        ):
            with self.subTest(tipo=type(excepcion).__name__):
                with mock.patch(
                    "motor.encargos.aislamiento._ejecutar_en_jaula",
                    side_effect=excepcion,
                ):
                    resultado = self.sondear(sano)
                self.assertFalse(resultado.ok)
                self.assertIn(type(excepcion).__name__, resultado.causa)
                self.assertTrue(resultado.condicion)

    def test_una_excepcion_al_preparar_la_jaula_tampoco_la_tumba(self):
        temporal = self.tmp / "temporal"
        temporal.mkdir(exist_ok=True)
        with (
            mock.patch("motor.encargos.aislamiento._en_windows", return_value=True),
            mock.patch(
                "motor.encargos.aislamiento.aislar",
                side_effect=KeyError("SystemRoot"),
            ),
        ):
            resultado = sondear(self.espacio, self.tmp / "runtime" / "sonda")
        self.assertFalse(resultado.ok)
        self.assertIn("KeyError", resultado.causa)


class EnsayoDelOperador(unittest.TestCase):
    """`ops/ensayo_shell_jaula.py` no puede fallar por permisos de la jaula: el push va al
    temporal. Corre la unica variante de la jaula de Windows (la sonda); Claude y su revisor
    corren en WSL y los cubre `ops/ensayo_wsl_jaula.py` (EnsayoDeLaPcTest)."""

    def test_el_push_corre_donde_la_variante_puede_escribir(self):
        import importlib.util

        ruta = Path(__file__).resolve().parents[1] / "ops" / "ensayo_shell_jaula.py"
        especificacion = importlib.util.spec_from_file_location("ensayo_shell", ruta)
        ensayo = importlib.util.module_from_spec(especificacion)
        especificacion.loader.exec_module(ensayo)
        lanzados = []

        def aislar(cmd, espacio, runtime, *, lectura=False, **_):
            temporal = runtime / "tmp"
            temporal.mkdir(parents=True, exist_ok=True)
            escritura = [str(runtime)] + ([] if lectura else [str(espacio)])
            return ComandoAislado(
                cmd, {"TMPDIR": str(temporal)}, "appcontainer", {"escritura": escritura}
            )

        def lanzar(comando, cwd, entorno, *, plazo):
            lanzados.append((comando.argv, Path(cwd), comando.jaula["escritura"]))
            return 0, "ok\n"

        herramientas = {
            "MOTOR_SONDA_SHELL": "bash.exe",
            "MOTOR_SONDA_SH": "sh.exe",
            "MOTOR_SONDA_GIT": "git.exe",
        }
        with (
            mock.patch.object(ensayo, "aislar", aislar),
            mock.patch.object(ensayo, "lanzar_en_jaula", lanzar),
            mock.patch.object(ensayo, "_herramientas_git", return_value=herramientas),
            mock.patch.object(
                ensayo, "sondear", return_value=SondaEntorno(True)
            ),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(ensayo.main(), 0)
        empujes = [x for x in lanzados if ensayo.PUSH in x[0]]
        self.assertEqual(len(empujes), 1)
        for _argv, cwd, escritura in empujes:
            self.assertTrue(
                any(cwd.is_relative_to(raiz) for raiz in escritura), (cwd, escritura)
            )
        self.assertEqual(empujes[0][1].name, "tmp")


class EnsayoDeObjetosDeLaJaula(unittest.TestCase):
    """`ops/ensayo_objetos_jaula.py` se ejecuta en la PC. Aquí solo se prueba que el
    guion compila y que lee bien una medida, con las dos respuestas posibles."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        ruta = Path(__file__).resolve().parents[1] / "ops" / "ensayo_objetos_jaula.py"
        especificacion = importlib.util.spec_from_file_location("ensayo_objetos", ruta)
        cls.ensayo = importlib.util.module_from_spec(especificacion)
        especificacion.loader.exec_module(cls.ensayo)

    def medida(self, global_: str, propio: str) -> dict:
        espacio = "\\Sessions\\1\\AppContainerNamedObjects\\S-1-15-2-9"
        return {
            "sesion": 1,
            "espacio_del_appcontainer": espacio,
            "pruebas": [
                ["crear", "\\BaseNamedObjects\\motor-sonda-ab", global_],
                ["crear", "\\Sessions\\BNOLINKS\\1\\motor-sonda-ab", global_],
                ["crear en el espacio del AppContainer", espacio + "\\motor-sonda-ab", propio],
            ],
        }

    def con_reapertura(self, solo_everyone: str, con_paquetes: str) -> dict:
        medida = self.medida("0xC0000022", "0x00000000")
        propio = medida["espacio_del_appcontainer"]
        medida["pruebas"] += [
            [self.ensayo.SOLO_EVERYONE, propio + "\\motor-sonda-ab-w", solo_everyone],
            [self.ensayo.CON_PAQUETES, propio + "\\motor-sonda-ab-aap", con_paquetes],
        ]
        return medida

    def test_el_guion_compila(self):
        compile(self.ensayo.NT, "nt", "exec")
        compile(self.ensayo.CODIGO, "guion", "exec")

    def test_el_espacio_propio_se_completa_a_ruta_nt_absoluta(self):
        # En la PC, GetAppContainerNamedObjectPath devolvió una ruta relativa y
        # NtCreateDirectoryObject la rechazó con 0xC000003B.
        espacio_nt: dict = {}
        exec(self.ensayo.RUTA_PROPIA, espacio_nt)
        completa = espacio_nt["ruta_nt_propia"]
        relativa = "AppContainerNamedObjects\\S-1-15-2-368368348-2193055017"
        self.assertEqual(completa(relativa, 1), "\\Sessions\\1\\" + relativa)
        absoluta = "\\Sessions\\1\\" + relativa
        self.assertEqual(completa(absoluta, 1), absoluta)
        self.assertEqual(completa("", 1), "")

    def test_el_experimento_imprime_el_stderr_de_msys(self):
        herramientas = {
            "MOTOR_SONDA_SHELL": "bash.exe",
            "MOTOR_SONDA_SH": "sh.exe",
            "MOTOR_SONDA_GIT": "git.exe",
        }
        comando = ComandoAislado(["x"], {}, "appcontainer", {})
        texto = "bash.exe: *** fatal error - NtCreateDirectoryObject: 0xC0000022"
        with (
            mock.patch.object(self.ensayo, "aislar", return_value=comando),
            mock.patch.object(
                self.ensayo,
                "_ejecutar_en_jaula",
                return_value=(3221225794, "", texto),
            ),
            mock.patch("builtins.print") as imprimir,
        ):
            fallos = self.ensayo._shell({}, Path("/e"), Path("/r"), herramientas)
        self.assertEqual(fallos, 3)
        self.assertTrue(all("0xC0000022" in c.args[0] for c in imprimir.call_args_list))

    def test_el_guion_usa_la_ruta_completa_del_espacio_propio(self):
        self.assertIn("propio = ruta_nt_propia(ruta.value, sesion)", self.ensayo.CODIGO)

    def test_confirma_la_causa_cuando_solo_el_espacio_propio_deja_crear(self):
        notas = self.ensayo.interpretar(self.medida("0xC0000022", "0x00000000"))
        self.assertTrue(notas[0].startswith("Confirmado"))
        self.assertFalse(any(n.startswith("Una concesión acotada") for n in notas))

    def test_descarta_la_concesion_acotada_si_el_contenedor_no_reabre_lo_suyo(self):
        notas = self.ensayo.interpretar(self.con_reapertura("0xC0000022", "0x00000000"))
        self.assertTrue(notas[0].startswith("Confirmado"))
        self.assertTrue(notas[1].startswith("Una concesión acotada no alcanza"), notas)

    def test_deja_abierta_la_concesion_si_el_contenedor_reabre_lo_suyo(self):
        notas = self.ensayo.interpretar(self.con_reapertura("0x00000000", "0x00000000"))
        self.assertIn("puede bastar", notas[1])

    def test_el_experimento_pide_los_dos_paquetes_y_etiqueta_baja(self):
        self.assertIn(";;;AC)", self.ensayo.SDDL_CONCESION)
        self.assertIn(";;;S-1-15-2-2)", self.ensayo.SDDL_CONCESION)
        self.assertIn("(ML;;NW;;;LW)", self.ensayo.SDDL_CONCESION)

    def test_la_medida_de_reapertura_tambien_nombra_al_paquete_restringido(self):
        self.assertIn("(A;;0xF000F;;;S-1-15-2-2)", self.ensayo.CODIGO)
        self.assertIn("token_actual()", self.ensayo.CODIGO)

    def test_reabrir_con_el_sid_del_contenedor_basta_para_leer_la_concesion(self):
        medida = self.con_reapertura("0xC0000022", "0xC0000022")
        medida["pruebas"].append(
            [self.ensayo.CON_SID, "\\x\\motor-sonda-ab-sid", "0x00000000"]
        )
        notas = self.ensayo.interpretar(medida)
        self.assertTrue(notas[1].startswith("Una concesión acotada no alcanza"), notas)

    def test_un_token_lpac_se_avisa_y_no_se_descarta_por_el_paquete_general(self):
        medida = self.con_reapertura("0xC0000022", "0xC0000022")
        medida["token"] = {"appcontainer": True, "lpac": True, "sid": "S-1-15-2-7"}
        notas = self.ensayo.interpretar(medida)
        self.assertFalse(any(n.startswith("Una concesión acotada no alcanza") for n in notas))
        self.assertTrue(any("LPAC" in n and "S-1-15-2-2" in n and "S-1-15-2-7" in n for n in notas))

    def test_sin_leer_el_token_se_dice(self):
        notas = self.ensayo.interpretar(self.medida("0xC0000022", "0x00000000"))
        self.assertTrue(any("No se pudo leer si el token es LPAC" in n for n in notas))

    def test_descarta_la_causa_cuando_el_global_deja_crear(self):
        notas = self.ensayo.interpretar(self.medida("0x00000000", "0x00000000"))
        self.assertIn("la causa no es el espacio de nombres", notas[0])

    def test_sin_medida_del_espacio_propio_no_concluye(self):
        medida = self.medida("0xC0000022", "0x00000000")
        medida["espacio_del_appcontainer"] = ""
        self.assertTrue(self.ensayo.interpretar(medida)[0].startswith("Sin conclusión"))


class LaSondaLanzaDentroDeLaJaula(unittest.TestCase):
    def test_en_appcontainer_no_se_usa_subprocess_run(self):
        comando = ComandoAislado(["x"], {}, "appcontainer", {"runtime": "r"})
        proceso = mock.Mock(returncode=0)
        proceso.communicate.return_value = ("ok\n", "")
        creado = mock.Mock(proceso=proceso)
        crear = mock.Mock(return_value=creado)
        grupo = mock.Mock()
        modulo = mock.Mock(crear=crear)
        with (
            mock.patch.dict(
                "sys.modules", {"motor.plataforma.jaula_windows": modulo}
            ),
            mock.patch("motor.plataforma.matar_grupo", grupo, create=True),
            mock.patch("subprocess.run") as ejecutar,
        ):
            codigo, salida = lanzar_en_jaula(
                comando, Path("/espacio"), {"A": "1"}, plazo=5
            )
        ejecutar.assert_not_called()
        crear.assert_called_once()
        self.assertEqual(crear.call_args.args[:2], (["x"], {"runtime": "r"}))
        creado.liberar.assert_called_once()
        grupo.assert_called_once_with(proceso)
        self.assertEqual((codigo, salida), (0, "ok\n"))


class ElShellDelCli(unittest.TestCase):
    def test_solo_claude_usa_el_bash_de_git(self):
        self.assertTrue(usa_shell_de_git("claude"))
        self.assertFalse(usa_shell_de_git("codex"))
        self.assertFalse(usa_shell_de_git(None))

    def test_el_agente_del_shell_de_git_corre_en_wsl_solo_en_windows(self):
        with mock.patch("motor.encargos.aislamiento._en_windows", return_value=False):
            self.assertFalse(usa_wsl("claude"))
        with mock.patch("motor.encargos.aislamiento._en_windows", return_value=True):
            self.assertTrue(usa_wsl("claude"))
            self.assertFalse(usa_wsl("codex"))
            self.assertFalse(usa_wsl(None))


class SondaDeWsl(unittest.TestCase):
    """La sonda de un agente que corre en WSL: el guion que corre en la distro (con un `sh`
    de verdad) y la manera en que se arma y se interpreta. La jaula real la cubre CI."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.espacio = self.tmp / "espacio" / "producto"
        self.espacio.mkdir(parents=True)
        self.runtime = self.tmp / "runtime" / "enc" / "sonda"
        (self.tmp / "temporal").mkdir()

    def guion(self, *fuentes, path=None):
        from motor.encargos.aislamiento import _GUION_SONDA_WSL

        entorno = {
            "PATH": path or os.environ["PATH"],
            "HOME": str(self.tmp),
            "CLAUDE_CODE_TMPDIR": str(self.tmp / "temporal"),
            "TMPDIR": str(self.tmp / "temporal"),
            "MOTOR_SONDA_MARCA": "m1",
            "MOTOR_SONDA_ESPACIO": str(self.espacio),
        }
        hecho = subprocess.run(
            ["/bin/sh", "-c", _GUION_SONDA_WSL, "sonda", *map(str, fuentes)],
            env=entorno, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
        )
        return hecho.returncode, hecho.stdout.strip().splitlines()[-1:], hecho

    def test_un_entorno_sano_prueba_bash_sh_y_git_push_a_un_bare_local(self):
        codigo, ultima, hecho = self.guion()
        self.assertEqual((codigo, ultima), (0, ["ok"]), hecho.stderr)
        self.assertEqual([p for p in (self.tmp / "temporal").rglob("*") if "sonda" in p.name], [])
        self.assertEqual(sorted(self.espacio.iterdir()), [])

    def test_un_git_que_falla_nombra_a_git_con_su_codigo_y_su_salida(self):
        falso = self.tmp / "falso"
        falso.mkdir()
        git = falso / "git"
        git.write_text("#!/bin/sh\necho 'fatal: detected dubious ownership' >&2\nexit 128\n")
        git.chmod(0o755)
        codigo, ultima, hecho = self.guion(path=f"{falso}:{os.environ['PATH']}")
        self.assertEqual(codigo, 7, hecho.stderr)
        self.assertEqual(ultima, ["git:codigo 128: fatal: detected dubious ownership"])
        self.assertEqual([p for p in (self.tmp / "temporal").rglob("*") if "sonda" in p.name], [])

    def test_una_fuente_ausente_se_nombra(self):
        codigo, ultima, _ = self.guion(self.tmp / "no-esta.db")
        self.assertEqual((codigo, ultima), (6, [f"fuente:{self.tmp / 'no-esta.db'}"]))

    def sondear_con(self, resultado, fuentes=()):
        from motor.encargos import aislamiento

        with mock.patch("motor.plataforma.jaula_wsl.ejecutar", **resultado) as ejecutar:
            sonda = aislamiento._sondear_en_wsl(
                self.espacio, self.runtime, tuple(fuentes), "m1"
            )
        return sonda, ejecutar

    def test_se_arma_con_la_concesion_en_rutas_de_la_distro_y_solo_monta_lo_que_existe(self):
        from motor.encargos.aislamiento import PLAZO_SONDA_WSL_S

        existe = self.tmp / "datos.db"
        existe.write_text("x")
        ausente = self.tmp / "no-esta.db"
        sonda, ejecutar = self.sondear_con({"return_value": (0, "ok\n", "")}, (existe, ausente))
        self.assertTrue(sonda.ok)
        jaula, cmd, entorno = ejecutar.call_args.args
        self.assertEqual(jaula["lecturas"], [str(existe)])
        self.assertFalse(jaula["lectura"])
        self.assertEqual(cmd[:2], ["/bin/sh", "-c"])
        self.assertEqual(cmd[-2:], [str(existe), str(ausente)])
        self.assertEqual(entorno, {"MOTOR_SONDA_ESPACIO": str(self.espacio), "MOTOR_SONDA_MARCA": "m1"})
        self.assertEqual(ejecutar.call_args.kwargs["plazo"], PLAZO_SONDA_WSL_S)
        self.assertEqual(ejecutar.call_args.kwargs["control"].parent, self.runtime.parent)

    def test_la_causa_concreta_de_bash_y_de_git_nombra_la_jaula_de_wsl(self):
        sonda, _ = self.sondear_con({"return_value": (7, "shell:codigo 126: Permission denied\n", "")})
        self.assertFalse(sonda.ok)
        self.assertEqual(sonda.causa, "bash no funciona en la jaula de WSL (codigo 126: Permission denied)")
        self.assertEqual(sonda.condicion, "que bash funcione dentro de la jaula de WSL con el entorno del agente")
        sonda, _ = self.sondear_con({"return_value": (7, "git:codigo 128: fatal: x\n", "")})
        self.assertEqual(sonda.causa, "git no funciona en la jaula de WSL (codigo 128: fatal: x)")

    def test_una_distro_que_no_termina_o_no_arranca_frena_con_su_causa_y_no_propaga(self):
        sonda, _ = self.sondear_con({"return_value": (124, "", "la jaula de WSL no termino en 60 s")})
        self.assertFalse(sonda.ok)
        self.assertIn("no terminó: codigo 124: la jaula de WSL no termino en 60 s", sonda.causa)
        self.assertEqual(sonda.condicion, "que la jaula de WSL funcione")
        sonda, _ = self.sondear_con({"side_effect": OSError("no hay wsl.exe")})
        self.assertFalse(sonda.ok)
        self.assertIn("OSError: no hay wsl.exe", sonda.causa)

    def test_sondear_manda_al_agente_de_wsl_a_la_distro_y_a_los_demas_al_aislamiento(self):
        from motor.encargos import aislamiento

        with (
            mock.patch("motor.encargos.aislamiento._en_windows", return_value=True),
            mock.patch("motor.plataforma.jaula_wsl.ejecutar", return_value=(0, "ok\n", "")) as wsl,
            mock.patch("motor.encargos.aislamiento.aislar", side_effect=AssertionError("AppContainer")),
        ):
            self.assertTrue(aislamiento.sondear(self.espacio, self.runtime, (), "claude").ok)
        wsl.assert_called_once()
        with (
            mock.patch("motor.encargos.aislamiento._en_windows", return_value=True),
            mock.patch("motor.plataforma.jaula_wsl.ejecutar", side_effect=AssertionError("WSL")),
            mock.patch("motor.encargos.aislamiento.aislar", side_effect=KeyError("SystemRoot")),
        ):
            sonda = aislamiento.sondear(self.espacio, self.runtime, (), "codex")
        self.assertFalse(sonda.ok)
        self.assertIn("no se pudo preparar el aislamiento", sonda.causa)


class SondaSinAislamiento(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_sin_aislamiento_la_sonda_no_acredita_recuperacion(self):
        espacio = Path(self._tmp.name) / "espacio" / "producto"
        espacio.mkdir(parents=True)
        with mock.patch(
            "motor.encargos.aislamiento.aislar",
            side_effect=RuntimeError("aislamiento no disponible"),
        ):
            resultado = sondear(espacio, Path(self._tmp.name) / "runtime" / "otra")
        self.assertFalse(resultado.ok)
        self.assertIn("aislamiento", resultado.causa)
        self.assertTrue(resultado.condicion)


if __name__ == "__main__":
    unittest.main()
