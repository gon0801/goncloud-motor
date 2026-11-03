"""Recorrido aislado de fase 4. No toca servicios, Orbit ni conversaciones reales.

Las entradas de director son el CLI del candidato. El puente de necesidades y la
adopción de fases son las funciones que ya usa el supervisor.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

PY = sys.executable


def correr(*argv: str, base: Path) -> dict:
    hecho = subprocess.run(
        [PY, "-B", "-m", "motor", "director", *argv, "--base", str(base)],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    try:
        datos = json.loads(hecho.stdout) if hecho.stdout.strip() else {}
    except json.JSONDecodeError:
        datos = {"stdout": hecho.stdout, "stderr": hecho.stderr}
    datos["_rc"] = hecho.returncode
    datos["_stderr"] = hecho.stderr.strip()
    return datos


def main() -> int:
    from motor.encargos.fases import adoptar
    from motor.encargos.necesidades import atender
    from motor.encargos.registro import Registro, exigir_base

    raiz = Path(sys.argv[1]).resolve()
    base = exigir_base(raiz / "base")
    transporte = raiz / "transporte.py"
    llamadas = raiz / "llamadas.txt"
    llamadas.write_text("0", encoding="utf-8")
    transporte.write_text(
        textwrap.dedent(
            """\
            import json, os, sys
            from pathlib import Path
            base = Path(os.environ["MOTOR_AVISO_BASE"]).parent
            contador = base / "llamadas.txt"
            n = int(contador.read_text(encoding="utf-8") or "0") + 1
            contador.write_text(str(n), encoding="utf-8")
            cuerpo = sys.stdin.read()
            with (base / "transporte.jsonl").open("a", encoding="utf-8") as salida:
                salida.write(json.dumps({
                    "id": os.environ.get("MOTOR_AVISO_ID", ""),
                    "encargo": os.environ.get("MOTOR_AVISO_ENCARGO", ""),
                    "cuerpo": cuerpo,
                }, ensure_ascii=False) + "\\n")
            print(json.dumps({"recibo": "r-" + os.environ.get("MOTOR_AVISO_ID", "")}))
            """
        ),
        encoding="utf-8",
    )
    (base / "canal.json").write_text(
        json.dumps(
            {
                "hermes-ensayo": {
                    "comando": [PY, str(transporte)],
                    "idempotente": True,
                }
            }
        ),
        encoding="utf-8",
    )
    plan = raiz / "plan.txt"
    plan.write_text(
        "Desarrolla la consulta de métricas.\n"
        "Fase datos: conservar las métricas diarias.\n"
        "Fase interfaz: mostrar las ganancias.\n"
        "No integres ni despliegues.\n",
        encoding="utf-8",
    )
    sqlite = raiz / "metrics.sqlite"
    with sqlite3.connect(sqlite) as db:
        db.execute("create table daily (fecha text, ventas integer)")
        db.execute("insert into daily values ('2026-10-01', 3)")

    abierto = correr(
        "encargar",
        "--director",
        "hermes",
        "--via",
        "durable",
        "--canal",
        "hermes-ensayo",
        "--clave",
        "msg-abierto",
        "--texto",
        "arregla el informe local",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    codigo = correr(
        "encargar",
        "--director",
        "hermes",
        "--via",
        "durable",
        "--canal",
        "hermes-ensayo",
        "--clave",
        "msg-plan",
        "--archivo",
        str(plan),
        "--repo",
        "gon0801/ensayo-fase4",
        "--base-rama",
        "master",
        "--responsable",
        "meta/muse-wonder-1.0",
        "--revisor",
        "kimi-code/k3.1-pro",
        "--restriccion",
        "no despliegues",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    local = correr(
        "encargar",
        "--director",
        "hermes",
        "--via",
        "durable",
        "--canal",
        "hermes-ensayo",
        "--clave",
        "msg-local",
        "--texto",
        "solo local",
        "--repo",
        "gon0801/ensayo-fase4",
        "--entrega",
        "terminado",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    encargo = codigo["encargo"]
    reg = Registro(base)
    try:
        interno = base / "espacio" / encargo / ".interno"
        interno.mkdir(parents=True)
        (interno / "fases.json").write_text(
            json.dumps(
                {
                    "fases": [
                        {"id": "datos", "resultado": "conservar las métricas diarias"},
                        {
                            "id": "interfaz",
                            "resultado": "mostrar las ganancias",
                            "requiere": ["datos"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        enc = reg.fold(encargo)
        adoptar(reg, enc, interno, "2026-10-30T00:00:00Z")
        (base / "capacidades.json").write_text(
            json.dumps(
                {
                    "recursos": {
                        "metrics": {
                            "tipo": "sqlite",
                            "ruta": str(sqlite),
                            "autorizacion": "propietario",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        enc = reg.fold(encargo)
        buzon = interno / "buzon" / "peticiones"
        buzon.mkdir(parents=True)
        peticion = {
            "id": "preg1",
            "encargo": encargo,
            "operacion": "sqlite.tablas",
            "recurso": "metrics",
            "fase": enc.fase_activa,
            "intento": "ensayo",
            "generacion": 0,
            "conductor": "ensayo",
            "motivo": "hace falta el esquema para elegir la fuente",
            "parametros": {},
        }
        (buzon / "preg1.json").write_text(
            json.dumps(peticion), encoding="utf-8"
        )
        (buzon / "dec1.json").write_text(
            json.dumps(
                {
                    "id": "dec1",
                    "encargo": encargo,
                    "operacion": "decision.registrar",
                    "recurso": "",
                    "fase": enc.fase_activa,
                    "intento": "ensayo",
                    "generacion": 0,
                    "conductor": "ensayo",
                    "motivo": "la fuente equivalente cumple el contrato observado",
                    "parametros": {
                        "eleccion": "usar la tabla daily",
                        "motivo": "tiene fecha y ventas",
                        "evidencia": "una fila de ensayo",
                    },
                }
            ),
            encoding="utf-8",
        )
        espacio = base / "espacio" / encargo
        directorio = base / "ejecucion" / encargo
        atender(reg, reg.fold(encargo), espacio, directorio, "2026-10-30T00:01:00Z")
    finally:
        reg.close()

    antes = int(llamadas.read_text(encoding="utf-8"))
    aviso1 = correr("avisar", base=base)
    medio = int(llamadas.read_text(encoding="utf-8"))
    aviso2 = correr("avisar", base=base)
    despues = int(llamadas.read_text(encoding="utf-8"))
    visto = correr("ver", encargo, base=base)
    ajeno = correr(
        "responder",
        encargo,
        "--solicitud",
        "preg1",
        "--respuesta",
        "sí",
        "--clave",
        "msg-respuesta",
        "--canal",
        "otro-canal",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    respuesta = correr(
        "responder",
        encargo,
        "--solicitud",
        "preg1",
        "--respuesta",
        "sí",
        "--clave",
        "msg-respuesta",
        "--canal",
        "hermes-ensayo",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    repetida = correr(
        "responder",
        encargo,
        "--solicitud",
        "preg1",
        "--respuesta",
        "sí",
        "--clave",
        "msg-respuesta",
        "--canal",
        "hermes-ensayo",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    tardia = correr(
        "responder",
        encargo,
        "--solicitud",
        "dec1",
        "--respuesta",
        "sí, haz otra cosa",
        "--clave",
        "msg-tardia",
        "--canal",
        "hermes-ensayo",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    corr = int(llamadas.read_text(encoding="utf-8"))
    correr("cerrar-sesion", encargo, base=base)
    visto_cerrado = correr("ver", encargo, base=base)
    correr("avisar", base=base)
    final_llamadas = int(llamadas.read_text(encoding="utf-8"))
    duplicado = correr(
        "encargar",
        "--director",
        "hermes",
        "--via",
        "durable",
        "--canal",
        "hermes-ensayo",
        "--clave",
        "msg-plan",
        "--archivo",
        str(plan),
        "--repo",
        "gon0801/ensayo-fase4",
        "--base-rama",
        "master",
        "--responsable",
        "meta/muse-wonder-1.0",
        "--revisor",
        "kimi-code/k3.1-pro",
        "--restriccion",
        "no despliegues",
        "--sesion",
        "sesion-ensayo",
        base=base,
    )
    openbot = {"presente": False}
    cli = Path("/Applications/OpenBot.app/Contents/Resources/cli/openbot-cli.mjs")
    if cli.is_file():
        hecho = subprocess.run(
            [PY, "-B", "-m", "motor", "director", "capacidades-openbot", "--base", str(base), "--cli", f"node {cli}"],
            cwd=RAIZ,
            capture_output=True,
            text=True,
        )
        openbot = {"presente": True, "rc": hecho.returncode, "stdout": hecho.stdout, "stderr": hecho.stderr}

    informe = {
        "abierto_entrega": (abierto.get("entrega") or {}).get("nivel_autorizado"),
        "codigo_entrega": (codigo.get("entrega") or {}).get("nivel_autorizado"),
        "local_expresa": (local.get("entrega") or {}).get("nivel_autorizado"),
        "texto_integro": visto.get("texto") == plan.read_text(encoding="utf-8"),
        "restriccion": "no despliegues" in (visto.get("texto") and ""),
        "modelos": visto.get("modelos_solicitados"),
        "seguimiento": visto_cerrado.get("seguimiento"),
        "bitacora": visto_cerrado.get("bitacora"),
        "avisos_nuevos": [
            {"tipo": a.get("tipo"), "novedad": a.get("novedad"), "entregado": a.get("entregado"), "solicitud": a.get("solicitud")}
            for a in aviso1.get("avisos") or []
            if a.get("novedad")
        ],
        "llamadas": {"antes": antes, "tras_primer_aviso": medio, "tras_segundo": despues, "tras_respuesta": corr, "tras_cierre": final_llamadas},
        "segundo_sin_novedad": all(not a.get("novedad") for a in aviso2.get("avisos") or []),
        "canal_ajeno": {"rc": ajeno.get("_rc"), "motivo": ajeno.get("motivo"), "aplicada": ajeno.get("aplicada")},
        "respuesta": {
            "rc": respuesta.get("_rc"),
            "aplicada": respuesta.get("aplicada"),
            "retransmision": respuesta.get("retransmision"),
            "continuacion": respuesta.get("continuacion"),
        },
        "repetida": {
            "rc": repetida.get("_rc"),
            "retransmision": repetida.get("retransmision"),
            "aplicada": repetida.get("aplicada"),
            "continuacion": repetida.get("continuacion"),
        },
        "tardia": {"rc": tardia.get("_rc"), "aplicada": tardia.get("aplicada"), "tardia": tardia.get("tardia")},
        "mismo_encargo": duplicado.get("encargo") == encargo and not duplicado.get("conflicto"),
        "fase_tras_cierre": (visto_cerrado.get("seguimiento") or {}).get("fase_actual"),
        "openbot": openbot,
        "stderr": {
            "codigo": codigo.get("_stderr"),
            "respuesta": respuesta.get("_stderr"),
            "ajeno": ajeno.get("_stderr"),
        },
    }
    # La restricción vive en el registro, no en el texto del pedido.
    reg = Registro(base)
    try:
        enc = reg.fold(encargo)
        informe["restriccion"] = "no despliegues" in (enc.restricciones if enc else ())
        informe["repo"] = enc.entrega.repo if enc else ""
        informe["base_git"] = enc.entrega.base if enc else ""
        sol = next((s for s in (enc.solicitudes if enc else ()) if s.id == "preg1"), None)
        informe["pregunta_estado"] = sol.estado if sol else ""
        informe["pregunta_fase"] = sol.fase if sol else ""
    finally:
        reg.close()
    destino = raiz / "informe.json"
    destino.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    fallos = []
    if informe["abierto_entrega"] != "terminado":
        fallos.append("abierto")
    if informe["codigo_entrega"] != "pr":
        fallos.append("codigo")
    if informe["local_expresa"] != "terminado":
        fallos.append("local")
    if not informe["texto_integro"] or not informe["restriccion"]:
        fallos.append("integro")
    if informe["modelos"] != {
        "responsable": "meta/muse-wonder-1.0",
        "revisor": "kimi-code/k3.1-pro",
    }:
        fallos.append("modelos")
    if (informe["seguimiento"] or {}).get("fase_actual") != "datos":
        fallos.append("fase")
    if not informe["bitacora"]:
        fallos.append("bitacora")
    if medio <= antes or despues != medio:
        fallos.append("avisos")
    if not informe["segundo_sin_novedad"]:
        fallos.append("novedad")
    if ajeno.get("_rc") == 0 or ajeno.get("aplicada"):
        fallos.append("canal")
    if not respuesta.get("aplicada"):
        fallos.append("respuesta")
    if not repetida.get("retransmision"):
        fallos.append("repetida")
    if tardia.get("aplicada"):
        fallos.append("tardia")
    if informe["pregunta_estado"] != "resuelta":
        fallos.append("continuacion")
    if final_llamadas != corr:
        fallos.append("cierre")
    if not informe["mismo_encargo"]:
        fallos.append("duplicado")
    if fallos:
        print("FALLOS", ",".join(fallos), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
