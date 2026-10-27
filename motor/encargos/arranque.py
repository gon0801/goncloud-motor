"""Wrapper durable. Publica su identidad antes de dar capacidades al agente."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

# El wrapper se ejecuta por ruta absoluta y nunca importa código del espacio del agente.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from motor import plataforma
from motor.encargos.frontera import leer_parada_local, turno
from motor.encargos.registro import Registro
from motor.runwrap import _publica


class Observacion:
    def __init__(self, directorio: Path):
        self.directorio = directorio
        self.abiertas: set[str] = set()
        self.secuencia = 0

    def evento(self, data: dict) -> None:
        tipo = data.get("type")
        item = data.get("item") or {}
        if tipo in ("item.started", "item.completed") and item.get("type") in (
            "command_execution",
            "mcp_tool_call",
            "web_search",
            "file_change",
        ):
            ident = str(item.get("id") or item.get("type"))
            if tipo == "item.started":
                self.abiertas.add(ident)
            else:
                self.abiertas.discard(ident)
        elif tipo in ("assistant", "user"):
            for bloque in (data.get("message") or {}).get("content") or []:
                if not isinstance(bloque, dict):
                    continue
                if bloque.get("type") == "tool_use":
                    self.abiertas.add(str(bloque.get("id")))
                elif bloque.get("type") == "tool_result":
                    self.abiertas.discard(str(bloque.get("tool_use_id")))
        else:
            return
        if tipo == "item.completed" and item.get("type") == "command_execution":
            with open(
                self.directorio / "ejecuciones-runtime.jsonl", "a", encoding="utf-8"
            ) as f:
                f.write(
                    json.dumps(
                        {
                            "comando": item.get("command"),
                            "rc": item.get("exit_code"),
                            "salida": (item.get("aggregated_output") or "")[-16000:],
                        }
                    )
                    + "\n"
                )
        self.secuencia += 1
        _publica(
            self.directorio / "herramienta.json",
            json.dumps(
                {
                    "observada": True,
                    "activas": sorted(self.abiertas),
                    "secuencia": self.secuencia,
                    "hora": time.time(),
                    "hecho": f"{tipo} {item.get('type') or ''}".strip(),
                }
            ),
        )


class SalidaRuntime:
    def __init__(self, agente: str):
        self.agente = agente
        self.terminal = ""
        self.ultimo_error = ""

    def evento(self, data: dict) -> None:
        tipo = data.get("type")
        update = data.get("sessionUpdate")
        error_grok = self.agente == "grok" and (
            tipo == "error"
            or (isinstance(update, dict) and update.get("update") == "error")
        )
        if tipo in ("error", "turn.failed") or error_grok:
            origen = update if error_grok and isinstance(update, dict) else data
            error = origen.get("error") or origen.get("message") or origen.get("data")
            if isinstance(error, dict):
                error = error.get("message") or json.dumps(error)
            self.ultimo_error = str(error or "el proveedor cerro el turno con error")[
                -1000:
            ]
            if tipo == "turn.failed" or error_grok:
                self.terminal = self.ultimo_error
        elif tipo == "turn.completed" and self.agente == "codex":
            self.terminal = self.ultimo_error = ""

    def fallo(self, rc: int, stderr: str) -> str:
        if self.terminal:
            return self.terminal
        if rc:
            return (
                self.ultimo_error
                or stderr.strip()
                or f"el CLI termino con codigo {rc}, sin error estructurado"
            )[-1000:]
        return ""


def ejecutar(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    d = spec_path.parent
    candado = plataforma.candado(d / "arranque.lock")
    if candado is None:
        return 1
    proc = {
        "pid": os.getpid(),
        "inicio": plataforma.hora_inicio(os.getpid()),
        "boot_id": plataforma.boot_id(),
        "host": spec["host"],
        "token": spec["token"],
        "aislamiento": spec["aislamiento"],
    }
    _publica(d / "wrapper.json", json.dumps(proc))
    plataforma.sincroniza_directorio(d)
    reg = Registro(Path(spec["base"]))
    try:
        with turno(reg.base):
            enc = reg.fold(spec["encargo"])
            if (
                enc is None
                or enc.conductor != spec["token"]
                or not enc.intentos_nuevos
                or leer_parada_local(reg.base)
            ):
                _publica(d / "outcome.json", json.dumps({"sin_liberar": True}))
                return 0
            evento = {
                "kind": "intento_abierto",
                "data": {
                    "id": spec["token"],
                    **proc,
                    "sesion": spec["sesion"],
                    "rol": spec["rol"],
                    "agente": spec["agente"],
                    "hora": spec["hora"],
                    "herramientas_observables": True,
                },
            }
            resultado = reg.anotar(enc.id, evento, f"intento:{enc.id}:{spec['token']}")
            if resultado not in ("nuevo", "igual"):
                return 1
            from motor.encargos.ciclo import _avisar_intento

            if not _avisar_intento(reg, enc.id, proc, spec["hora"]):
                _publica(
                    d / "outcome.json",
                    json.dumps({"sin_liberar": True, "autoridad": False}),
                )
                return 1
            _publica(d / "proc.json", json.dumps(proc))
            plataforma.sincroniza_directorio(d)
            opciones = {
                "cwd": spec["cwd"],
                "env": spec["env"],
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if os.name == "nt":
                from motor.plataforma.jaula_windows import crear

                creado = crear(spec["cmd"], spec["jaula"], control=d, **opciones)
                p = creado.proceso
            else:
                # Hereda el grupo ya identificado del wrapper. No hay una ventana de hijo huérfano.
                p = subprocess.Popen(spec["cmd"], start_new_session=False, **opciones)
                creado = plataforma.ProcesoCreado(p, "grupo")
            _publica(d / "agent.inicio", plataforma.hora_inicio(p.pid))
            _publica(d / "agent.pid", str(p.pid))
            if creado.job:
                _publica(d / "agent.job", creado.job)
            plataforma.sincroniza_directorio(d)
            creado.liberar()
        from motor.encargos.sesion import guardar_descendientes

        fin_observacion = threading.Event()

        def observar_hijos():
            while not fin_observacion.wait(0.5):
                guardar_descendientes(d, os.getpid())

        threading.Thread(target=observar_hijos, daemon=True).start()
        observacion = Observacion(d)
        _publica(
            d / "herramienta.json",
            json.dumps(
                {"observada": True, "activas": [], "secuencia": 0, "hora": time.time()}
            ),
        )
        runtime = SalidaRuntime(spec["agente"])
        errores = deque(maxlen=40)

        def leer_errores():
            with open(d / "stderr.txt", "a", encoding="utf-8") as log:
                for linea in p.stderr:
                    errores.append(linea[-1000:])
                    log.write(linea)
                    log.flush()

        lector_error = threading.Thread(target=leer_errores, daemon=True)
        lector_error.start()
        with open(d / "log.txt", "a", encoding="utf-8") as log:
            for linea in p.stdout:
                log.write(linea)
                log.flush()
                try:
                    data = json.loads(linea)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    observacion.evento(data)
                    runtime.evento(data)
        rc = p.wait()
        lector_error.join(1)
        fin_observacion.set()
        _publica(
            d / "outcome.json",
            json.dumps(
                {
                    "exit": rc,
                    "token": spec["token"],
                    "fallo_runtime": runtime.fallo(rc, "".join(errores)),
                }
            ),
        )
        return rc
    except (OSError, RuntimeError) as exc:
        _publica(
            d / "outcome.json",
            json.dumps(
                {
                    "exit": 1,
                    "token": spec["token"],
                    "fallo_runtime": f"arranque {type(exc).__name__}: {exc}",
                }
            ),
        )
        return 1
    finally:
        reg.close()


if __name__ == "__main__":
    raise SystemExit(ejecutar(Path(sys.argv[1])))
