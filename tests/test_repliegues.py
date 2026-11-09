"""El supervisor pliega menos sin servir fotos viejas.

`Registro.append` valida contra el mismo plegado que se persiste: no recorre la
historia entera por cada hecho y deja la caché en el estado confirmado. La
migración de marcadores no pliega encargos que no tienen nada que migrar.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.ciclo import ahora_iso, recibir
from motor.encargos.dominio import Encargo, EntregaAutorizada, Fase
from motor.encargos.necesidades import contexto_de
from motor.encargos.registro import Registro

HORA = "2026-11-08T12:00:00Z"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)

    def encargo(self, clave: str = "repliegues") -> str:
        return recibir(
            self.reg, "un pedido de prueba", clave, ahora_iso(), via="directa"
        )["encargo"]

    def nota(self, ident: str, texto: str) -> str:
        return self.reg.append(
            ident,
            "procedimiento",
            {"nota": texto, "hora": HORA},
            f"nota-{texto}",
            HORA,
        )


class Deduplicacion(Base):
    def test_el_mismo_hecho_dos_veces_no_duplica_ni_mueve_el_estado(self):  # no-roja-en-base: caracterización de la deduplicación por ukey
        ident = self.encargo()
        datos = {"nota": "una", "hora": HORA}

        self.assertEqual(
            self.reg.append(ident, "procedimiento", datos, "hecho-uno", HORA),
            "nuevo",
        )
        antes = self.reg.fold(ident)
        self.assertEqual(
            self.reg.append(ident, "procedimiento", datos, "hecho-uno", HORA),
            "igual",
        )

        self.assertEqual(self.reg.fold(ident), antes)
        otro = Registro(self.base)
        self.addCleanup(otro.close)
        self.assertEqual(otro.fold(ident), antes)
        self.assertEqual(
            len([ev for ev in otro.eventos(ident) if ev["kind"] == "procedimiento"]),
            1,
        )


class OtraConexionEscribiendo(Base):
    def test_un_append_ve_lo_que_otra_conexion_confirmo(self):  # no-roja-en-base: caracterización de la lectura de eventos de otra conexión
        ident = self.encargo()
        self.reg.fold(ident)
        otro = Registro(self.base)
        self.addCleanup(otro.close)
        otro.append(
            ident,
            "procedimiento",
            {"nota": "de fuera", "hora": HORA},
            "nota-de-fuera",
            HORA,
        )

        self.assertIn("de fuera", self.reg.fold(ident).procedimiento)
        self.assertEqual(self.nota(ident, "local"), "nuevo")

        verificador = Registro(self.base)
        self.addCleanup(verificador.close)
        esperado = verificador.fold(ident)
        self.assertIn("de fuera", esperado.procedimiento)
        self.assertIn("local", esperado.procedimiento)
        self.assertEqual(self.reg.fold(ident), esperado)


class Rechazo(Base):
    def test_un_evento_ilegal_no_mueve_el_estado_ni_la_cache(self):  # no-roja-en-base: caracterización del rechazo que ya validaba
        ident = self.encargo()
        self.reg.fold(ident)
        antes = self.reg.fold(ident)

        rechazado = self.reg.append(
            ident,
            "entrega",
            {
                "id": ident,
                "clave": "otra",
                "huella": "otra-huella",
                "texto": "un segundo pedido",
                "hora": HORA,
            },
            "entrega-repetida",
            HORA,
        )

        self.assertEqual(rechazado, "ilegal")
        self.assertEqual(self.reg.fold(ident), antes)
        otro = Registro(self.base)
        self.addCleanup(otro.close)
        self.assertEqual(otro.fold(ident), antes)


class AppendSinRepliegueCompleto(Base):
    def test_un_append_tras_plegar_valida_sin_recorrer_la_historia(self):
        import motor.encargos.registro as registro_mod

        ident = self.encargo()
        for i in range(5):
            self.nota(ident, f"previa-{i}")
        self.reg.fold(ident)

        with mock.patch.object(
            registro_mod, "reducir", wraps=registro_mod.reducir
        ) as reducir_espiado:
            self.assertEqual(self.nota(ident, "ultima"), "nuevo")

        self.assertEqual(
            reducir_espiado.call_count,
            1,
            "el append aplicó solo el hecho nuevo, no la historia entera",
        )


class MigracionSinArchivos(Base):
    def test_sin_archivos_viejos_la_migracion_no_pliega(self):  # no-roja-en-base: la migración llegó en 3b; aquí se fija que no pliega de más
        self.encargo()
        with mock.patch.object(
            Registro, "_plegar", autospec=True, wraps=Registro._plegar
        ) as plegar:
            ciclo.migrar_marcadores(self.reg, HORA)

        self.assertEqual(
            plegar.call_count, 0, "no hay marcador que migrar: no hace falta plegar"
        )


class MigracionConUnArchivo(Base):
    def test_un_incorporado_viejo_se_convierte_y_el_archivo_desaparece(self):  # no-roja-en-base: la migración llegó en 3b; aquí se fija el camino con archivos
        ident = self.encargo()
        directorio = Path(self.reg.base) / "ejecucion" / ident
        directorio.mkdir(parents=True, exist_ok=True)
        (directorio / "incorporado").write_text(HORA, encoding="utf-8")

        ciclo.migrar_marcadores(self.reg, HORA)

        eventos = [ev for ev in self.reg.eventos(ident) if ev["kind"] == "incorporado"]
        self.assertEqual(len(eventos), 1)
        self.assertFalse((directorio / "incorporado").exists())


if __name__ == "__main__":
    unittest.main()


class ContextoDeNecesidades(Base):
    def _encargo_con_fase(self, fase: Fase, rama_entrega: str) -> Encargo:
        return Encargo(
            id="enc_contexto",
            clave="contexto",
            huella="h",
            director="hermes",
            texto="un pedido",
            adjuntos=(),
            restricciones=(),
            canal="tg",
            fases=(fase,),
            fase_activa=fase.id,
            entrega=EntregaAutorizada(rama=rama_entrega),
        )

    def test_la_rama_es_la_de_la_fase_vigente(self):  # no-roja-en-base: la propiedad rama_entrega se borró en esta unidad; la prueba fija contexto_de tras el borrado
        enc = self._encargo_con_fase(
            Fase(id="a", resultado="fase", rama="rama/fase"), "rama/entrega"
        )

        self.assertEqual(contexto_de(enc)["rama"], "rama/fase")

    def test_sin_rama_en_la_fase_usa_la_de_la_entrega(self):  # no-roja-en-base: la propiedad rama_entrega se borró en esta unidad; la prueba fija el respaldo de contexto_de
        enc = self._encargo_con_fase(Fase(id="a", resultado="fase"), "rama/entrega")

        self.assertEqual(contexto_de(enc)["rama"], "rama/entrega")


class MigracionContenida(Base):
    def test_un_fallo_al_listar_los_directorios_no_tumba_la_migracion(self):  # no-roja-en-base: la migración de 3b ya contenía el listado; la prueba fija que no se pierde
        sano = self.encargo("sano")
        roto = self.encargo("roto")
        directorio = Path(self.reg.base) / "ejecucion" / sano
        directorio.mkdir(parents=True, exist_ok=True)
        (directorio / "incorporado").write_text(HORA, encoding="utf-8")
        original = ciclo._directorios_del_encargo

        def rompe(base: Path, encargo_id: str):
            if encargo_id == roto:
                raise OSError("directorio ilegible")
            return original(base, encargo_id)

        with mock.patch.object(ciclo, "_directorios_del_encargo", rompe):
            try:
                ciclo.migrar_marcadores(self.reg, HORA)
            except OSError as exc:
                self.fail(f"la migración no contuvo el fallo de un encargo: {exc}")

        eventos = [ev for ev in self.reg.eventos(sano) if ev["kind"] == "incorporado"]
        self.assertEqual(len(eventos), 1)
