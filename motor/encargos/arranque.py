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
        self.hora = time.time()
        self.hecho = ""
        self.llamadas_grok: dict[str, dict] = {}
        self.modelo_en_curso: bool | None = None

    def evento(self, data: dict) -> None:
        if data.get("payload_type"):
            tipo = data["payload_type"]
            payload = data.get("payload") or {}
            if tipo == "run.lifecycle.started" or tipo == "run.output.delta":
                self.modelo_en_curso = True
            elif tipo == "task.lifecycle.started":
                self.abiertas.add(str(payload.get("task_id")))
                self.modelo_en_curso = True
            elif tipo in (
                "task.lifecycle.completed",
                "task.lifecycle.failed",
                "task.lifecycle.cancelled",
            ):
                self.abiertas.discard(str(payload.get("task_id")))
            elif tipo.startswith("run.terminal."):
                self.abiertas.clear()
                self.modelo_en_curso = False
                self.secuencia += 1
                self.hora = time.time()
                self.hecho = tipo
            else:
                return
            self.publicar()
            return
        if data.get("role") in ("assistant", "tool"):
            self.modelo_en_curso = True
            if data["role"] == "assistant":
                llamadas = data.get("tool_calls")
                if isinstance(llamadas, list):
                    for llamada in llamadas:
                        if isinstance(llamada, dict):
                            ident = llamada.get("id")
                            if isinstance(ident, str) and ident:
                                self.abiertas.add(ident)
            else:
                self.abiertas.discard(str(data.get("tool_call_id")))
                self.secuencia += 1
                self.hora = time.time()
                self.hecho = "tool.result"
            self.publicar()
            return
        tipo = data.get("type")
        item = data.get("item") or {}
        avance = True
        ejecucion = None
        if tipo in ("turn.started", "thought", "text") or (
            tipo in ("item.started", "item.completed")
            and item.get("type") == "reasoning"
        ):
            self.modelo_en_curso = True
            self.publicar()
            return
        if tipo in ("turn.completed", "turn.failed", "result"):
            self.modelo_en_curso = False
            self.secuencia += 1
            self.hora = time.time()
            self.hecho = str(tipo)
            self.publicar()
            return
        if tipo in ("tool_call", "tool_call_update"):
            ident = data.get("toolCallId")
            estado = data.get("status")
            if not isinstance(ident, str) or not ident:
                return
            previo = self.llamadas_grok.get(ident) or {}
            if tipo == "tool_call":
                previo = {
                    **previo,
                    "nombre": data.get("toolName"),
                }
                estado = estado or "pending"
            if estado not in (
                "pending",
                "in_progress",
                "completed",
                "failed",
                "cancelled",
                "canceled",
            ):
                return
            if previo.get("estado") in ("completed", "failed", "cancelled", "canceled"):
                return
            terminal = estado in ("completed", "failed", "cancelled", "canceled")
            self.llamadas_grok[ident] = {**previo, "estado": estado}
            if terminal:
                self.modelo_en_curso = True
                self.abiertas.discard(ident)
                salida = data.get("rawOutput")
                if (
                    isinstance(salida, dict)
                    and "exit_code" in salida
                    and salida.get("command")
                ):
                    ejecucion = {
                        "comando": salida["command"],
                        "rc": salida["exit_code"],
                        "salida": str(salida.get("output_for_prompt") or "")[-16000:],
                    }
            else:
                self.abiertas.add(ident)
            avance = terminal
            hecho = f"{tipo} {previo.get('nombre') or ident} {estado}"
        elif tipo in ("item.started", "item.completed") and item.get("type") in (
            "command_execution",
            "mcp_tool_call",
            "web_search",
            "file_change",
        ):
            ident = str(item.get("id") or item.get("type"))
            if tipo == "item.started":
                self.abiertas.add(ident)
            else:
                self.modelo_en_curso = True
                self.abiertas.discard(ident)
        elif tipo in ("assistant", "user"):
            self.modelo_en_curso = True
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
            ejecucion = {
                "comando": item.get("command"),
                "rc": item.get("exit_code"),
                "salida": (item.get("aggregated_output") or "")[-16000:],
            }
        if ejecucion is not None:
            with open(
                self.directorio / "ejecuciones-runtime.jsonl", "a", encoding="utf-8"
            ) as f:
                f.write(json.dumps(ejecucion) + "\n")
        if avance:
            self.secuencia += 1
            self.hora = time.time()
            self.hecho = (
                hecho
                if tipo in ("tool_call", "tool_call_update")
                else f"{tipo} {item.get('type') or ''}".strip()
            )
        self.publicar()

    def publicar(self) -> None:
        _publica(
            self.directorio / "herramienta.json",
            json.dumps(
                {
                    "observada": True,
                    "modelo_en_curso": self.modelo_en_curso,
                    "activas": sorted(self.abiertas),
                    "secuencia": self.secuencia,
                    "hora": self.hora,
                    "hecho": self.hecho,
                }
            ),
        )


class SalidaRuntime:
    def __init__(self, agente: str):
        self.agente = agente
        self.terminal = ""
        self.ultimo_error = ""
        self.sesion_runtime = ""

    def evento(self, data: dict) -> None:
        if self.agente in ("codex", "kimi", "muse") and not self.sesion_runtime:
            for clave in ("thread_id", "session_id"):
                valor = data.get(clave)
                if isinstance(valor, str) and valor:
                    self.sesion_runtime = valor
                    break
            else:
                hilo = data.get("thread")
                if isinstance(hilo, dict) and isinstance(hilo.get("id"), str):
                    self.sesion_runtime = hilo["id"]
                stream = data.get("stream") or {}
                if self.agente == "muse" and stream.get("kind") == "session":
                    self.sesion_runtime = str(stream.get("id") or "")
        if self.agente == "muse" and str(data.get("payload_type") or "").startswith(
            "run.terminal."
        ):
            payload = data.get("payload") or {}
            if payload.get("terminal") == "completed":
                self.terminal = self.ultimo_error = ""
            else:
                self.terminal = self.ultimo_error = str(
                    payload.get("reason") or "Muse no completo el turno"
                )[-1000:]
            return
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


def _sesion_kimi(indice: Path, espacio: Path) -> str:
    try:
        lineas = indice.read_text(encoding="utf-8").splitlines()
        cwd = espacio.resolve()
    except (OSError, UnicodeError, RuntimeError):
        return ""
    for linea in reversed(lineas):
        if not linea.strip():
            continue
        try:
            fila = json.loads(linea)
        except ValueError:
            continue
        if not isinstance(fila, dict):
            continue
        workdir, ident = fila.get("workDir"), fila.get("sessionId")
        if (
            not isinstance(workdir, str)
            or not workdir.strip()
            or not isinstance(ident, str)
            or not ident.strip()
        ):
            continue
        try:
            if Path(workdir).resolve() == cwd:
                return ident
        except (OSError, ValueError, RuntimeError):
            continue
    return ""


def _autenticacion_expirada(spec: dict, fallo: str) -> bool:
    from motor.fallas import clasifica

    expira = spec["env"].get("MOTOR_KIMI_ACCESS_EXPIRES_AT")
    return bool(
        spec["agente"] == "kimi"
        and fallo
        and expira
        and time.time() >= float(expira)
        and clasifica("kimi", "", fallo)["kind"] == "auth"
    )


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
            datos_intento = {
                "id": spec["token"],
                **proc,
                "sesion": spec["sesion"],
                "rol": spec["rol"],
                "agente": spec["agente"],
                "hora": spec["hora"],
                "herramientas_observables": True,
            }
            for clave in ("modelo", "sesion_modo", "poteto_digest"):
                datos_intento[clave] = spec.get(clave) or ""
            evento = {"kind": "intento_abierto", "data": datos_intento}
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
        observacion.publicar()
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
        if spec["agente"] == "kimi" and not runtime.sesion_runtime:
            indice = Path(spec["env"]["KIMI_CODE_HOME"]) / "session_index.jsonl"
            runtime.sesion_runtime = _sesion_kimi(indice, Path(spec["cwd"]))
        fallo_runtime = runtime.fallo(rc, "".join(errores))
        _publica(
            d / "outcome.json",
            json.dumps(
                {
                    "exit": rc,
                    "token": spec["token"],
                    "fallo_runtime": fallo_runtime,
                    "autenticacion_expirada": _autenticacion_expirada(
                        spec, fallo_runtime
                    ),
                    "sesion_runtime": runtime.sesion_runtime,
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
