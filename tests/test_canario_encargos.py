"""El juez del canario: la tabla de un encargo terminado contra lo esperado.

`juzgar` se prueba con observaciones armadas a mano, una variación por fila.
`observar` se prueba sobre un encargo real del banco de la compuerta
(`tests/test_compuerta_e2e.py`): el motor lo recibe, lo trabaja con el agente
de ensayo, lo mergea en el git local y lo despliega; la prueba solo lo mira.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from typing import get_args

from motor import plataforma
from ops.canario_encargos import (
    ESPERADO,
    PR,
    TABLA,
    ArchivoDesplegado,
    Esperado,
    NoPliega,
    NombreFila,
    Observacion,
    Pliegue,
    Recibo,
    Resto,
    Topes,
    Version,
    juzgar,
    leer_esperado,
    main,
    observar,
)
from tests import git_env_limpio
from tests.test_compuerta_e2e import (
    SEPARA_TRABAJADOR,
    Banco,
    _DeepSeekAprueba,
    _escenario_tres_fases,
    _git,
    _trabajador_contenido,
)

SHA = "a" * 40
OTRO = "b" * 40
RECIBIDO = "2026-01-10T10:00:00Z"
CIERRE = "2026-01-10T10:20:00Z"
HORA = "2026-01-10T10:30:00Z"
TOPES = Topes(minutos_termina=90, minutos=45, turnos_por_rol=2, rondas=1)
NO_MEDIDAS = ("no pierde", "no repite", "se detiene")


def _pliegue(**cambios) -> Pliegue:
    base = Pliegue(
        estado="terminado",
        nivel_autorizado="desplegado",
        nivel_efectivo="desplegado",
        nivel_alcanzado="desplegado",
        base="main",
        recibido=RECIBIDO,
        cierre=CIERRE,
        desplegables=("uno",),
        turnos=(("responsable", 1), ("revisor", 1)),
        rondas=1,
        violaciones=(),
    )
    return dataclasses.replace(base, **cambios)


def _obs(**cambios) -> Observacion:
    """Un encargo que cumple cada punto medible."""
    base = Observacion(
        hora=HORA,
        pliegue=_pliegue(),
        prs=(PR("uno", 7, "mergeado", "main", SHA),),
        recibos=(Recibo("uno", "aplicado", SHA),),
        restos=(
            Resto("rama", "la rama remota r", False),
            Resto("runtime", "runtime/x", False),
            Resto("espacio", "espacio/x", True),
            Resto("ejecucion", "ejecucion/x", True),
            Resto("proceso", "algún proceso", False),
        ),
        version=Version(SHA, SHA, SHA),
        archivo=ArchivoDesplegado("corridas/m.txt", "marca", True),
    )
    return dataclasses.replace(base, **cambios)


def _esperado(**filas: str) -> Esperado:
    todas = {nombre: ("no medido" if nombre in NO_MEDIDAS else "pasa") for nombre, *_ in TABLA}
    todas.update({k.replace("_", " "): v for k, v in filas.items()})
    return Esperado(filas=todas, topes=TOPES)  # type: ignore[arg-type]


def _fila(obs: Observacion, nombre: str, esperado: Esperado | None = None):
    return next(f for f in juzgar(obs, esperado or _esperado()) if f.nombre == nombre)


class LaTabla(unittest.TestCase):
    def test_la_tabla_es_cerrada_y_sigue_el_orden_del_diseno(self):
        self.assertEqual([nombre for nombre, *_ in TABLA], list(get_args(NombreFila)))
        filas = juzgar(_obs(), _esperado())
        self.assertEqual([f.nombre for f in filas], list(get_args(NombreFila)))
        self.assertTrue(all(f.observado and f.fuente for f in filas))

    def test_un_encargo_que_cumple_pasa_cada_fila_medible(self):
        resultados = {f.nombre: f.resultado for f in juzgar(_obs(), _esperado())}
        self.assertEqual(
            resultados,
            {n: ("no medido" if n in NO_MEDIDAS else "pasa") for n, *_ in TABLA},
        )

    def test_las_filas_sin_corte_dicen_no_medido_aunque_todo_falle(self):
        obs = _obs(pliegue=_pliegue(estado="detenido", rondas=9), prs=(), recibos=())
        for nombre in NO_MEDIDAS:
            self.assertEqual(_fila(obs, nombre).resultado, "no medido")


class Termina(unittest.TestCase):
    def test_un_encargo_detenido_no_termina(self):
        fila = _fila(_obs(pliegue=_pliegue(estado="detenido")), "termina")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("detenido", fila.observado)

    def test_terminar_en_un_nivel_menor_al_pedido_falla(self):
        obs = _obs(pliegue=_pliegue(nivel_alcanzado="integrado", nivel_efectivo="integrado"))
        fila = _fila(obs, "termina")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("integrado", fila.observado)

    def test_terminar_despues_del_tope_falla(self):
        fila = _fila(_obs(pliegue=_pliegue(cierre="2026-01-10T12:00:00Z")), "termina")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("120 min", fila.observado)

    def test_sin_registro_no_se_mide(self):
        self.assertEqual(_fila(_obs(pliegue=None), "termina").resultado, "no medido")


class Entregado(unittest.TestCase):
    def falla(self, obs: Observacion, texto: str) -> None:
        fila = _fila(obs, "entregado")
        self.assertEqual(fila.resultado, "falla", fila.observado)
        self.assertIn(texto, fila.observado)

    def test_un_pr_abierto_no_esta_entregado(self):
        self.falla(_obs(prs=(PR("uno", 7, "abierto", "main"),)), "#7 está abierto")

    def test_un_pr_mergeado_en_otra_base_no_esta_entregado(self):
        self.falla(_obs(prs=(PR("uno", 7, "mergeado", "otra", SHA),)), "se mergeó en otra")

    def test_una_fase_sin_pr_no_esta_entregada(self):
        self.falla(_obs(prs=(PR("uno", 0, None),)), "no abrió PR")

    def test_un_recibo_de_otro_commit_no_cuenta_como_desplegado(self):
        self.falla(_obs(recibos=(Recibo("uno", "aplicado", OTRO),)), "recibo aplicado de su commit")

    def test_un_recibo_fallido_no_cuenta_como_desplegado(self):
        self.falla(_obs(recibos=(Recibo("uno", "fallido", SHA),)), "recibo aplicado de su commit")

    def test_el_archivo_desplegado_sin_la_marca_falla(self):
        archivo = ArchivoDesplegado("corridas/m.txt", "marca", False)
        self.falla(_obs(archivo=archivo), "no trae la marca")

    def test_el_archivo_desplegado_que_no_existe_falla(self):
        archivo = ArchivoDesplegado("corridas/m.txt", "marca", None)
        self.falla(_obs(archivo=archivo), "no existe")

    def test_si_github_no_responde_no_se_mide(self):
        fila = _fila(_obs(prs=(PR("uno", 7, None),)), "entregado")
        self.assertEqual(fila.resultado, "no medido")

    def test_un_encargo_integrado_no_pide_recibo(self):
        obs = _obs(
            pliegue=_pliegue(nivel_autorizado="integrado", nivel_alcanzado="integrado"),
            recibos=(),
        )
        self.assertEqual(_fila(obs, "entregado").resultado, "pasa")


class Limpio(unittest.TestCase):
    def test_una_rama_que_queda_no_esta_limpio(self):
        restos = (Resto("rama", "la rama remota r", True),)
        fila = _fila(_obs(restos=restos), "limpio")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("la rama remota r", fila.observado)

    def test_una_rama_que_queda_falla_aunque_el_reloj_vaya_atras_del_cierre(self):
        restos = (Resto("rama", "la rama remota r", True),)
        fila = _fila(_obs(restos=restos, hora="2026-01-10T10:10:00Z"), "limpio")
        self.assertEqual(fila.resultado, "falla")

    def test_un_proceso_vivo_no_esta_limpio(self):
        fila = _fila(_obs(restos=(Resto("proceso", "algún proceso", True),)), "limpio")
        self.assertEqual(fila.resultado, "falla")

    def test_la_copia_dentro_de_su_plazo_esta_limpia_y_se_nombra(self):
        fila = _fila(_obs(), "limpio")
        self.assertEqual(fila.resultado, "pasa")
        self.assertIn("espacio/x hasta 2026-01-17", fila.observado)

    def test_la_copia_vencida_no_esta_limpia(self):
        fila = _fila(_obs(hora="2026-01-18T10:30:00Z"), "limpio")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("espacio/x", fila.observado)

    def test_lo_que_no_se_pudo_mirar_no_se_mide(self):
        fila = _fila(_obs(restos=(Resto("rama", "la rama remota r", None),)), "limpio")
        self.assertEqual(fila.resultado, "no medido")

    def test_un_encargo_sin_cerrar_no_se_mide(self):
        fila = _fila(_obs(pliegue=_pliegue(estado="ejecutando", cierre="")), "limpio")
        self.assertEqual(fila.resultado, "no medido")


class CuestaPoco(unittest.TestCase):
    def test_un_turno_de_mas_por_rol_cuesta(self):
        obs = _obs(pliegue=_pliegue(turnos=(("responsable", 3), ("revisor", 1))))
        self.assertEqual(_fila(obs, "cuesta poco").resultado, "falla")

    def test_una_ronda_de_mas_cuesta(self):
        self.assertEqual(_fila(_obs(pliegue=_pliegue(rondas=2)), "cuesta poco").resultado, "falla")

    def test_pasar_los_minutos_cuesta(self):
        obs = _obs(pliegue=_pliegue(cierre="2026-01-10T10:50:00Z"))
        fila = _fila(obs, "cuesta poco")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("50 min (tope 45)", fila.observado)


class VersionYLegible(unittest.TestCase):
    def test_un_arbol_en_otro_sha_falla(self):
        fila = _fila(_obs(version=Version(SHA, OTRO, SHA)), "versión")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn(OTRO[:12], fila.observado)

    def test_una_pasada_vieja_falla(self):
        self.assertEqual(_fila(_obs(version=Version(SHA, SHA, OTRO)), "versión").resultado, "falla")

    def test_sin_recibo_de_instalacion_no_se_mide(self):
        fila = _fila(_obs(version=Version("", SHA, SHA)), "versión")
        self.assertEqual(fila.resultado, "no medido")

    def test_un_registro_que_no_pliega_no_es_legible(self):
        obs = _obs(pliegue=NoPliega(3, "revision", "TransicionIlegal"))
        fila = _fila(obs, "legible")
        self.assertEqual(fila.resultado, "falla")
        self.assertIn("evento 3", fila.observado)
        self.assertEqual(_fila(obs, "termina").resultado, "no medido")

    def test_una_invariante_rota_no_es_legible(self):
        obs = _obs(pliegue=_pliegue(violaciones=("mismo_estado_al_replegar",)))
        self.assertEqual(_fila(obs, "legible").resultado, "falla")


class LoEsperado(unittest.TestCase):
    def test_coincide_cuando_cada_fila_da_lo_esperado(self):
        obs = _obs(restos=(Resto("rama", "la rama remota r", True),))
        filas = juzgar(obs, _esperado(limpio="falla"))
        self.assertEqual([f.nombre for f in filas if f.discrepancia], [])

    def test_una_fila_que_pasaba_y_falla_es_regresion(self):
        filas = juzgar(_obs(pliegue=_pliegue(rondas=3)), _esperado())
        self.assertEqual(
            [(f.nombre, f.discrepancia) for f in filas if f.discrepancia],
            [("cuesta poco", "regresión")],
        )

    def test_una_fila_que_empieza_a_pasar_pide_actualizar_el_esperado(self):
        filas = juzgar(_obs(), _esperado(limpio="falla"))
        self.assertEqual(
            [(f.nombre, f.discrepancia) for f in filas if f.discrepancia],
            [("limpio", "mejora: actualiza el esperado")],
        )

    def test_una_fila_que_deja_de_medirse_no_coincide(self):
        filas = juzgar(_obs(version=Version("", SHA, SHA)), _esperado())
        self.assertEqual(
            [(f.nombre, f.discrepancia) for f in filas if f.discrepancia],
            [("versión", "dejó de medirse")],
        )

    def test_el_esperado_del_repo_nombra_cada_fila(self):
        esperado = leer_esperado(ESPERADO)
        self.assertEqual(set(esperado.filas), set(get_args(NombreFila)))
        self.assertEqual(esperado.filas["limpio"], "falla")
        for nombre in NO_MEDIDAS:
            self.assertEqual(esperado.filas[nombre], "no medido")

    def test_un_esperado_al_que_le_falta_una_fila_se_rechaza(self):
        tmp = Path(tempfile.mkdtemp(prefix="esperado-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        datos = json.loads(ESPERADO.read_text(encoding="utf-8"))
        del datos["filas"]["limpio"]
        ruta = tmp / "esperado.json"
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        with self.assertRaises(ValueError):
            leer_esperado(ruta)


# --------------------------------------------------------------------------- observar


def _registro_vacio(base: Path) -> None:
    db = sqlite3.connect(base / "registro.sqlite")
    db.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
    )
    db.commit()
    db.close()


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _GitHubCaido:
    def ver(self, repo, numero):
        raise RuntimeError("gh pr view: sin red")


class ObservarLaBase(unittest.TestCase):
    """Lo que no necesita un encargo trabajado: versión y registro roto."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="canario-base-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        _registro_vacio(self.base)

    def test_la_version_sale_del_recibo_de_la_pasada_y_del_arbol(self):
        arbol = self.base / "arbol"
        arbol.mkdir()
        (arbol / "a.txt").write_text("a", encoding="utf-8")
        _git(arbol, "init", "-q", "-b", "main")
        _git(arbol, "add", "-A")
        _git(arbol, "-c", "user.name=t", "-c", "user.email=t@local", "commit", "-q", "-m", "a")
        head = _git(arbol, "rev-parse", "HEAD")
        (self.base / "instalacion.json").write_text(
            json.dumps({"destino": {"codigo": SHA}}), encoding="utf-8"
        )
        (self.base / "supervisor-pasada.json").write_text(
            json.dumps({"sha": OTRO, "ruta": str(arbol)}), encoding="utf-8"
        )
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA)
        self.assertEqual(obs.version, Version(SHA, head, OTRO))
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA, sha_desplegado=head)
        self.assertEqual(obs.version.desplegado, head)

    def test_sin_recibos_la_version_queda_vacia(self):
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA)
        self.assertIsNone(obs.pliegue)
        self.assertEqual(obs.version, Version("", "", ""))

    def test_un_registro_que_no_pliega_se_observa_como_roto(self):
        db = sqlite3.connect(self.base / "registro.sqlite")
        db.execute(
            "INSERT INTO events (ts, encargo, kind, data) VALUES (?, ?, ?, ?)",
            (RECIBIDO, "enc_x", "revision", "{}"),
        )
        db.commit()
        db.close()
        obs = observar(self.base, "enc_x", _GitHubCaido(), hora=HORA)
        self.assertEqual(obs.pliegue, NoPliega(1, "revision", "TransicionIlegal"))

    def test_main_sale_con_2_si_el_encargo_no_esta(self):
        self.assertEqual(main(["enc_x", "--base", str(self.base)], gh=_GitHubCaido()), 2)


class ObservarUnEncargoDelBanco(unittest.TestCase):
    """El motor trabaja, mergea y despliega un encargo de una fase; la prueba lo observa."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="canario-banco-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        if not SEPARA_TRABAJADOR:
            _trabajador_contenido(self)

    def test_observa_pr_recibo_restos_y_procesos_del_encargo(self):
        escenario = _escenario_tres_fases(True, self.tmp / "destino")
        escenario["fases"] = escenario["fases"][:1]
        escenario["producto"] = {"uno": escenario["producto"]["uno"]}
        banco = Banco(self.tmp, escenario=escenario, gh_cls=_DeepSeekAprueba)
        self.addCleanup(banco.reg.close)
        banco.correr(lambda: banco.fold().estado == "terminado")

        (pr_fake,) = json.loads(
            (self.tmp / "fake" / "github.json").read_text(encoding="utf-8")
        )["prs"].values()
        merge = pr_fake["merge_commit"]
        archivo = (banco.destino / "pasos", merge)
        obs = observar(banco.base, banco.ident, banco.gh, hora=_ahora(), archivo=archivo)

        self.assertIsInstance(obs.pliegue, Pliegue)
        assert isinstance(obs.pliegue, Pliegue)
        self.assertEqual(obs.pliegue.estado, "terminado")
        self.assertEqual(obs.pliegue.nivel_alcanzado, "desplegado")
        self.assertEqual(obs.pliegue.violaciones, ())
        self.assertEqual(
            {rol for rol, _ in obs.pliegue.turnos}, {"responsable", "revisor"}
        )
        self.assertEqual(obs.prs, (PR("uno", pr_fake["numero"], "mergeado", "main", merge),))
        self.assertIn(Recibo("uno", "aplicado", merge), obs.recibos)
        self.assertEqual(obs.archivo, ArchivoDesplegado(str(archivo[0]), merge, True))

        restos = {r.nombre: r for r in obs.restos}
        rama = pr_fake["rama"]
        self.assertEqual(
            restos[f"la rama remota {rama}"].queda,
            bool(_git(self.tmp / "repo.git", "branch", "--list", rama)),
        )
        espacio = f"espacio/{banco.ident}"
        self.assertEqual(restos[espacio].queda, (banco.base / espacio).exists())
        self.assertFalse(any(r.queda for r in obs.restos if r.tipo == "proceso"))

        esperado = _esperado(limpio="falla")
        self.assertEqual(_fila(obs, "entregado", esperado).resultado, "pasa")
        self.assertEqual(_fila(obs, "legible", esperado).resultado, "pasa")
        self.assertEqual(
            _fila(obs, "limpio", esperado).resultado,
            "falla" if restos[f"la rama remota {rama}"].queda else "pasa",
        )

        # Lo que el canario borra o deja vivo, la observación lo ve al volver a mirar.
        _git(self.tmp / "repo.git", "branch", "-D", rama)
        shutil.rmtree(banco.base / espacio)
        trabajador = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], env=git_env_limpio()
        )
        self.addCleanup(trabajador.wait)
        self.addCleanup(trabajador.kill)
        clave = banco.base / "despliegues" / "otro-intento"
        clave.mkdir(parents=True)
        (clave / "pedido.json").write_text(
            json.dumps({"identidad": {"encargo": banco.ident}}), encoding="utf-8"
        )
        (clave / "proceso.json").write_text(json.dumps({
            "pid": trabajador.pid,
            "inicio": plataforma.hora_inicio(trabajador.pid),
            "boot_id": plataforma.boot_id(),
        }), encoding="utf-8")
        otra = {r.nombre: r for r in observar(banco.base, banco.ident, banco.gh, hora=_ahora()).restos}
        self.assertFalse(otra[f"la rama remota {rama}"].queda)
        self.assertFalse(otra[espacio].queda)
        self.assertTrue(otra["algún trabajador de despliegue"].queda)

        caido = observar(banco.base, banco.ident, _GitHubCaido(), hora=_ahora())
        self.assertEqual(caido.prs, (PR("uno", pr_fake["numero"], None),))
        self.assertEqual(_fila(caido, "entregado", esperado).resultado, "no medido")


if __name__ == "__main__":
    unittest.main()
