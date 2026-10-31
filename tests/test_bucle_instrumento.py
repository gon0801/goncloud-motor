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
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.aislamiento import (
    ComandoAislado,
    SondaEntorno,
    _usa_shell_de_git,
    lanzar_en_jaula,
    sondear,
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

    def __init__(self, tmp: str, sonda: dict):
        self.sonda = sonda
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        self.hora = ahora_iso()
        self.enc = recibir(
            self.reg, "Cambia los modelos del motor", "mini-1", self.hora
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


class SondaDelShellDeGit(unittest.TestCase):
    """La sonda de Windows, con un `bash` de ensayo en lugar de msys.

    Corre el mismo guion que lanza en la PC, por `sondear`, con `aislar` y el
    host de Windows sustituidos: lo que se prueba es qué deja pasar y qué frena.
    """

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

    def sondear(self, bash: Path, agentes=()) -> SondaEntorno:
        temporal = self.tmp / "temporal"
        temporal.mkdir(exist_ok=True)
        entorno = {
            "PATH": os.environ["PATH"],
            "CLAUDE_CODE_TMPDIR": str(temporal),
            "TMPDIR": str(temporal),
            "HOME": str(self.tmp),
        }
        herramientas = {
            "MOTOR_SONDA_SHELL": str(bash),
            "MOTOR_SONDA_SH": str(bash),
            "MOTOR_SONDA_GIT": shutil.which("git"),
        }
        with (
            mock.patch("motor.encargos.aislamiento._en_windows", return_value=True),
            mock.patch(
                "motor.encargos.aislamiento._herramientas_git",
                return_value=herramientas,
            ),
            mock.patch(
                "motor.encargos.aislamiento.aislar",
                side_effect=lambda argv, *a, **k: ComandoAislado(
                    argv, entorno, "ensayo"
                ),
            ),
        ):
            return sondear(
                self.espacio, self.tmp / "runtime" / "sonda", agentes=agentes
            )

    def test_un_shell_sano_pasa_con_git_push_a_un_bare_local(self):
        resultado = self.sondear(self.shell("bash-sano", 'exec /bin/sh "$@"\n'))
        self.assertTrue(resultado.ok, resultado.causa)
        self.assertEqual(sorted(p.name for p in self.espacio.iterdir()), [])

    def test_un_shell_que_muere_al_arrancar_deja_la_causa_concreta(self):
        bash = self.shell(
            "bash-roto",
            "echo 'bash (6436) bash.exe: *** fatal error - "
            "NtCreateDirectoryObject(\\BaseNamedObjects\\msys-2.0S5-1888ae32e00d56aa)"
            ": 0xC0000022' >&2\nexit 66\n",
        )
        resultado = self.sondear(bash)
        self.assertFalse(resultado.ok)
        self.assertIn("el shell de Git no funciona bajo el aislamiento", resultado.causa)
        self.assertIn("codigo 66", resultado.causa)
        self.assertIn("0xC0000022", resultado.causa)
        self.assertIn("BaseNamedObjects", resultado.condicion)

    def test_un_git_push_que_falla_con_el_shell_vivo_nombra_a_git(self):
        bash = self.shell(
            "bash-sin-git",
            'case "$2" in echo*) echo ok;; *) echo "Could not read from remote'
            ' repository" >&2; exit 128;; esac\n',
        )
        resultado = self.sondear(bash)
        self.assertFalse(resultado.ok)
        self.assertTrue(resultado.causa.startswith("git no funciona"), resultado.causa)
        self.assertIn("Could not read from remote repository", resultado.causa)

    def test_un_shell_ausente_no_acredita_el_entorno(self):
        resultado = self.sondear(self.tmp / "no-esta" / "bash.exe")
        self.assertFalse(resultado.ok)
        self.assertIn("el shell de Git", resultado.causa)

    def test_un_agente_sin_bash_de_git_no_prueba_ese_shell(self):
        roto = self.shell("bash-roto", "exit 66\n")
        self.assertTrue(self.sondear(roto, agentes=("codex",)).ok)
        self.assertFalse(self.sondear(roto, agentes=("codex", "claude")).ok)
        self.assertFalse(self.sondear(roto).ok)


class EnsayoDelOperador(unittest.TestCase):
    """`ops/ensayo_shell_jaula.py` no puede fallar por permisos de la jaula:
    la variante del revisor no escribe en el espacio, y el push va al temporal."""

    def test_el_push_corre_donde_cada_variante_puede_escribir(self):
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
        self.assertEqual(len(empujes), 3)
        for _argv, cwd, escritura in empujes:
            self.assertTrue(
                any(cwd.is_relative_to(raiz) for raiz in escritura), (cwd, escritura)
            )
        lectura = [x for x in empujes if len(x[2]) == 1]
        self.assertEqual(len(lectura), 1)
        self.assertEqual(lectura[0][1].name, "tmp")


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

    def test_confirma_la_causa_cuando_solo_el_espacio_propio_deja_crear(self):
        notas = self.ensayo.interpretar(self.medida("0xC0000022", "0x00000000"))
        self.assertEqual(len(notas), 1)
        self.assertTrue(notas[0].startswith("Confirmado"))

    def test_descarta_la_concesion_acotada_si_el_contenedor_no_reabre_lo_suyo(self):
        notas = self.ensayo.interpretar(self.con_reapertura("0xC0000022", "0x00000000"))
        self.assertTrue(notas[0].startswith("Confirmado"))
        self.assertTrue(notas[1].startswith("Una concesión acotada no alcanza"), notas)

    def test_deja_abierta_la_concesion_si_el_contenedor_reabre_lo_suyo(self):
        notas = self.ensayo.interpretar(self.con_reapertura("0x00000000", "0x00000000"))
        self.assertIn("puede bastar", notas[1])

    def test_el_experimento_pide_paquetes_y_etiqueta_baja(self):
        self.assertIn(";;;AC)", self.ensayo.SDDL_CONCESION)
        self.assertIn("(ML;;NW;;;LW)", self.ensayo.SDDL_CONCESION)

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
    def test_solo_claude_o_un_agente_desconocido_usan_el_bash_de_git(self):
        self.assertTrue(_usa_shell_de_git(()))
        self.assertTrue(_usa_shell_de_git(("claude",)))
        self.assertTrue(_usa_shell_de_git(("codex", "claude")))
        self.assertFalse(_usa_shell_de_git(("codex",)))


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
