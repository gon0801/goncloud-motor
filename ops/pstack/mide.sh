#!/usr/bin/env bash
# Mide, con registros reales, qué hace cada CLI de modelos con la copia fija de pstack.
# Corre en la Mac (local), la Mini (ssh) y la PC (ssh; Muse dentro de WSL). En cada máquina baja
# `origin/main` de goncloud-motor-conf a una carpeta temporal, copia ahí `pstack/` y `pstack-motor/`
# (la copia fija), lanza cada CLI con su ruta real de rutas.toml y el encargo mínimo, y trae el
# registro de cada corrida a tests/datos/pstack/<cli>-<maquina>/. Las carpetas temporales se borran.
# Cada CLI corre como lo lanza el motor: el argv de su ruta tal cual (solo se sustituyen {prompt}, {brief},
# {result}, {pstack} y {sesion}), el entorno de la máquina con secretos y sin MOTOR_*, y el registro se
# lee donde lo lee el motor. Los flags que hacen legible un registro van en rutas.toml de la conf.
#   --juzga   corre la medición en vivo, no toca tests/datos y sale 0 solo si `juzga` da Cumple para
#             todos los CLI de los carriles (modelos.toml) en las tres máquinas
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$aqui/../.." && pwd)"
. "$repo/ops/lib.sh"

# El playbook Docs no existe en el pstack fijo: Investigation es el de solo lectura.
PLAYBOOK="${MIDE_PLAYBOOK:-investigation}"
TOPE_S="${MIDE_TOPE_S:-900}"

juzga=0
for a in "$@"; do
  case "$a" in
    --juzga) juzga=1 ;;
    *) echo "uso: ops/pstack/mide.sh [--juzga]" >&2; exit 2 ;;
  esac
done

d="$(mktemp -d)"
trap 'rm -rf "$d"' EXIT

mide_py() { cat <<'PY'
import json, os, re, shutil, signal, string, subprocess, sys, tempfile, threading, time, tomllib, uuid
from pathlib import Path

conf_url, maquina, playbook, tope = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
WIN = os.name == "nt"
HOME = Path.home()
CLIS = ["claude", "kimi", "opencode", "grok", "muse", "qwen"]
PREFERIDA = {"claude": "sonnet", "kimi": "k3", "opencode": "glm", "grok": "grok", "muse": "muse", "qwen": "qwen"}
TAREA = (
    "Carga poteto-mode, abre el playbook " + playbook + " y lanza un subagente que lea README.md. "
    "No cambies nada."
)


def corre(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, **kw)


def toml(p, opcional=False):
    p = Path(p)
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if opcional:
            return {}
        raise RuntimeError(f"la conf no trae {p.name}") from None
    except (OSError, ValueError) as e:
        raise RuntimeError(f"no pude leer {p.name}: {e}") from None


def ruta_wsl(x):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", x)
    return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}" if m else x


def mata_grupo(p):
    """Mata al hijo y a todo lo que lanzó (subagentes, workers): el tope solo mata al hijo directo."""
    try:
        if WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def corre_con_tope(argv, cwd, env, log, tope):
    """Como runwrap: stdout y stderr juntos en un archivo (log.txt), en su propio grupo de procesos."""
    kw = {"creationflags": 0x200} if WIN else {"start_new_session": True}
    with open(log, "wb") as f:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        try:
            rc = p.wait(timeout=tope)
        except subprocess.TimeoutExpired:
            mata_grupo(p)
            p.wait()
            return None, f"pasó el tope de {tope}s"
        mata_grupo(p)
        return rc, ""


tmp_dirs = []


def temporal(prefijo):
    d = Path(tempfile.mkdtemp(prefix=prefijo))
    tmp_dirs.append(d)
    return d


salida = {"maquina": maquina, "conf_sha": "", "carriles": [], "corridas": [], "error": ""}
try:
    mq = toml(HOME / ".motor" / "maquina.toml", opcional=True)
    env = dict(os.environ)
    for k in [k for k in env if k.startswith("MOTOR_")] + ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID"]:
        env.pop(k, None)
    env.pop("WSLENV", None)
    secretos = {}
    try:
        for linea in (HOME / ".motor" / "secretos.env").read_text(encoding="utf-8").splitlines():
            k, _, v = linea.strip().partition("=")
            if k and v and not k.startswith(("#", "MOTOR_")):
                secretos[k.strip()] = v.strip()
    except OSError:
        pass
    env.update({k: str(v) for k, v in (mq.get("env") or {}).items()})
    env.update(secretos)
    env["PATH"] = os.pathsep.join([*mq.get("path_extra", []), env.get("PATH", "")])
    wsl = set(mq.get("wsl", []))

    clon = temporal("mide-pstack-conf-")
    r = corre(["git", "clone", "--quiet", "--depth", "1", "--branch", "main", conf_url, str(clon)], env=env)
    if r.returncode:
        raise RuntimeError("no pude bajar origin/main de la conf: " + r.stderr.strip()[-300:])
    salida["conf_sha"] = corre(["git", "-C", str(clon), "rev-parse", "HEAD"], env=env).stdout.strip()
    rutas = toml(clon / "rutas.toml").get("rutas", {})
    carriles = toml(clon / "modelos.toml").get("carriles", {})
    if not rutas:
        raise RuntimeError("rutas.toml no trae ninguna ruta")
    modelos = {m for ms in carriles.values() for m in ms}
    if not modelos:
        raise RuntimeError("modelos.toml no pone ningún modelo en los carriles")
    sin_ruta = sorted(modelos - set(rutas))
    if sin_ruta:
        raise RuntimeError("modelos.toml pone en un carril a " + ", ".join(sin_ruta) + " y rutas.toml no los trae")
    salida["carriles"] = sorted({rutas[m]["cli"] for m in modelos})
    copia = temporal("mide-pstack-copia-")
    for n in ("pstack", "pstack-motor"):
        shutil.copytree(clon / n, copia / n)
    shutil.rmtree(clon, ignore_errors=True)

    def raiz_de(cli):
        return ruta_wsl(str(copia)) if cli in wsl else str(copia)

    def herramienta(cli):
        return {
            "claude": "`Skill`", "kimi": "`Skill`", "opencode": "`skill`", "muse": "`read_skill`",
        }.get(cli)

    def encargo(cli):
        raiz = raiz_de(cli)
        partes = [f"# Encargo de medición · {cli}\n\n{TAREA}\n"]
        for titulo, f in (("Lo que manda el motor sobre pstack", "motor.md"), (f"Mapa de {cli}", f"{cli}.md")):
            p = copia / "pstack-motor" / f
            if p.is_file():
                partes.append(f"## {titulo}\n{p.read_text(encoding='utf-8').strip()}\n")
        h = herramienta(cli)
        cargar = f"la herramienta de skills de tu CLI ({h})" if h else "una lectura de su `SKILL.md`"
        partes.append(
            "## Cómo trabajas\n"
            f"Este encargo se hace con pstack, en la copia fija `{raiz}`. Antes lee `{raiz}/pstack-motor/motor.md` "
            f"y `{raiz}/pstack-motor/{cli}.md`.\n"
            f"1. Carga la skill `poteto-mode` antes de tu acción número 6: con {cargar} o leyendo "
            f"`{raiz}/pstack/skills/poteto-mode/SKILL.md`. Si no la cargas, el motor te detiene.\n"
            f"2. Abre el playbook `{playbook}` de poteto-mode (`{raiz}/pstack/skills/poteto-mode/playbooks/{playbook}.md`).\n"
            "3. Lanza un subagente con el mecanismo de tu CLI y pásale la ruta absoluta de la skill.\n"
            "4. El motor lee el registro de tus herramientas, no tu resumen: lo que solo digas no cuenta.\n"
        )
        return "\n".join(partes)

    def entorno_de(cli, ruta):
        """Lo mismo que Efectos.env_base + el `env` de la ruta (con ${VAR}), sin nada MOTOR_*."""
        e = dict(env)
        e.update({k: string.Template(str(v)).safe_substitute(env) for k, v in (ruta.get("env") or {}).items()})
        if cli in wsl and "META_API_KEY" in e:  # lo que el motor reenvía a WSL (variables_wsl)
            e["WSLENV"] = "META_API_KEY"
        return e

    def argv_de(cli, ruta, prompt, brief, result, sid):
        """El argv de la ruta tal cual, como effects.start_attempt: solo se sustituyen los marcadores."""
        a = [x.format(brief=brief, prompt=prompt, result=result, pstack=raiz_de(cli), sesion=sid) for x in ruta["argv"]]
        if cli in wsl:
            return ["wsl.exe", "-e", *(ruta_wsl(x) for x in a)]
        exe = shutil.which(a[0], path=env["PATH"])
        return [exe or a[0], *a[1:]]

    def registros_extra(cli, sid, trabajo, log_texto):
        """Los registros que el motor lee antes de log.txt: el transcript de claude (por --session-id) y
        los updates.jsonl de grok que nombra su salida (aquí, todos los ids que parecen sessionId; juicio_de deja los que el motor leería). Es lo de Efectos._prepara_pstack y _registros_de."""
        extra = []
        if cli == "claude":
            base = Path(env.get("CLAUDE_CONFIG_DIR") or HOME / ".claude") / "projects"
            for c in dict.fromkeys([str(trabajo), str(trabajo.resolve())]):
                p = base / re.sub(r"[^A-Za-z0-9]", "-", c) / f"{sid}.jsonl"
                if p.is_file():
                    extra.append({"fuente": f"~/.claude/projects/<cwd>/{sid}.jsonl", "texto": p.read_text(encoding="utf-8", errors="replace")})
        if cli == "grok":
            ids = dict.fromkeys(re.findall(r'"session_?[iI]d"\s*:\s*"([A-Za-z0-9._-]+)"', log_texto))
            base = Path(env.get("GROK_HOME") or HOME / ".grok") / "sessions"
            for i in ids:
                for p in sorted(base.glob(f"*/{i}/updates.jsonl")):
                    extra.append({"id": i, "fuente": f"~/.grok/sessions/<cwd>/{i}/updates.jsonl", "texto": p.read_text(encoding="utf-8", errors="replace")})
        return extra

    def una(cli):
        res = {"cli": cli, "ruta": "", "plantilla": [], "rc": None, "segundos": 0, "raiz": raiz_de(cli),
               "registro": "", "extras": [], "version": "", "error": ""}
        ruta_n = PREFERIDA[cli]
        ruta = rutas.get(ruta_n)
        if not ruta or ruta.get("cli") != cli:
            res["error"] = f"rutas.toml no trae la ruta {ruta_n}"
            return res
        res["ruta"], res["plantilla"] = ruta_n, list(ruta["argv"])
        exe = ("wsl.exe" if cli in wsl else shutil.which(ruta["argv"][0], path=env["PATH"]))
        if not exe:
            res["error"] = f"{ruta['argv'][0]} no está instalado en {maquina}"
            return res
        try:
            v = corre(["wsl.exe", "-e", cli, "--version"] if cli in wsl else [exe, "--version"], env=env, timeout=60)
            res["version"] = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else ""
        except (OSError, subprocess.TimeoutExpired):
            pass
        corrida = temporal(f"mide-pstack-{cli}-")
        trabajo = corrida / "trabajo"
        trabajo.mkdir()
        (trabajo / "README.md").write_text("# Carpeta de medición\n\nNo hay nada que cambiar aquí.\n", encoding="utf-8")
        brief = corrida / "brief.md"
        brief.write_text(encargo(cli), encoding="utf-8")
        brief_c = ruta_wsl(str(brief)) if cli in wsl else str(brief)
        prompt = f"Lee {brief_c} y ejecútalo completo sin preguntar nada."
        sid = str(uuid.uuid4())
        try:
            argv = argv_de(cli, ruta, prompt, brief_c, ruta_wsl(str(corrida / "result.json")) if cli in wsl else str(corrida / "result.json"), sid)
        except (KeyError, IndexError, ValueError) as e:
            res["error"] = f"el argv de la ruta {ruta_n} no se puede armar: {e!r}"
            return res
        t0 = time.time()
        log = corrida / "log.txt"
        try:
            res["rc"], res["error"] = corre_con_tope(argv, trabajo, entorno_de(cli, ruta), log, tope)
        except OSError as e:
            res["error"] = f"no pude lanzarlo: {e}"
        res["segundos"] = round(time.time() - t0)
        texto = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        res["registro"] = texto
        res["extras"] = registros_extra(cli, sid, trabajo, texto)
        return res

    hilos, resultados = [], {}
    for cli in CLIS:
        def corre_uno(c=cli):
            resultados[c] = una(c)
        t = threading.Thread(target=corre_uno)
        hilos.append(t)
        t.start()
    for t in hilos:
        t.join()
    salida["corridas"] = [resultados[c] for c in CLIS]
except Exception as e:  # noqa: BLE001
    salida["error"] = f"{type(e).__name__}: {e}"
finally:
    for d in tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)
sys.stdout.buffer.write(("MIDE-JSON:" + json.dumps(salida, ensure_ascii=True) + "\n").encode("ascii"))
PY
}

mide_py >"$d/mide.py"

posix_path='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"'
py_posix="$posix_path; if python3 -c 'import tomllib' 2>/dev/null; then py=python3; else py='uv run --python 3.12 python'; fi"

mide_en() {
  local m=$1 url=$CONF_URL
  [ "$m" = mini ] && url=$CONF_URL_MINI
  case "$m" in
    pc)
      ssh_a "$(host_de pc)" "$PC_UV run --python 3.12 python - $url $m $PLAYBOOK $TOPE_S" <"$d/mide.py" >"$d/$m.out" 2>"$d/$m.err"
      ;;
    *)
      local cmd="$py_posix; \$py - $url $m $PLAYBOOK $TOPE_S"
      if es_local "$m"; then
        bash -c "$cmd" <"$d/mide.py" >"$d/$m.out" 2>"$d/$m.err"
      else
        ssh_a "$(host_de "$m")" "$(printf '%q ' bash -c "$cmd")" <"$d/mide.py" >"$d/$m.out" 2>"$d/$m.err"
      fi
      ;;
  esac
  echo $? >"$d/$m.rc"
}

for m in $MAQUINAS; do mide_en "$m" & done
wait

python3 -I - "$d" "$repo" "$MAQUINAS" "$juzga" <<'PY'
import json, re, shutil, sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, sys.argv[2])
from motor import pstack as ps

EXIGENCIA = ps.Exigencia(playbook=True, min_subagentes=1)
TOPE_TEXTO = 1500


def recorta(x):
    """Los textos largos (archivos que el agente leyó) se acortan: el registro conserva las llamadas, no el contenido."""
    if isinstance(x, str):
        return x if len(x) <= TOPE_TEXTO else x[:TOPE_TEXTO] + f"…[{len(x) - TOPE_TEXTO} caracteres más]"
    if isinstance(x, list):
        return [recorta(i) for i in x]
    if isinstance(x, dict):
        return {k: recorta(v) for k, v in x.items()}
    return x


def registro_corto(texto):
    inicio = re.search(r"(?m)^\[", texto)
    if inicio:  # un arreglo completo (qwen --output-format json), con avisos antes o después
        try:
            arreglo, fin = json.JSONDecoder().raw_decode(texto[inicio.start():])
            antes, despues = texto[:inicio.start()], texto[inicio.start() + fin:]
            return antes + json.dumps(recorta(arreglo), ensure_ascii=False) + despues.rstrip() + "\n"
        except ValueError:
            pass
    out = []
    for linea in texto.splitlines():
        try:
            o = json.loads(linea) if linea.lstrip()[:1] == "{" else None
        except ValueError:
            o = None
        out.append(json.dumps(recorta(o), ensure_ascii=False) if isinstance(o, dict) else linea[:TOPE_TEXTO])
    return "\n".join(out) + "\n"


def grok_ids(texto):
    """Los sessionId que Efectos._registros_de saca de log.txt: de primer nivel, de los objetos que lee ps._objetos."""
    return {
        sid
        for o in ps._objetos(texto)
        for sid in (o.get("sessionId") or o.get("session_id"),)
        if isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9._-]+", sid)
    }


def juicio_de(c):
    """(estado, huella, fuente, texto) de una corrida: el registro que el motor leería, el primero legible."""
    ids = grok_ids(c["registro"]) if c["cli"] == "grok" else None
    extras = [r for r in c.get("extras", []) if ids is None or r.get("id") in ids]
    candidatos = [*extras, {"fuente": "log.txt (stdout y stderr)", "texto": c["registro"]}]
    h, fuente, texto = ps.Huella(legible=False), candidatos[-1]["fuente"], c["registro"]
    for r in candidatos:
        h = ps.huella(r["texto"], c["cli"], c["raiz"])
        fuente, texto = r["fuente"], r["texto"]
        if h.legible:
            break
    v = ps.juzga(h, EXIGENCIA)
    if isinstance(v, ps.Cumple):
        estado = "Cumple" if v.verificable else "NO VERIFICABLE"
    else:
        estado = "Falta: " + v.texto()
    if c["error"]:
        estado = f"{c['error']} ({estado})"
    return estado, h, fuente, texto


def agrega(salidas, maquinas):
    """salidas: {maquina: {"out", "err", "rc"}} con lo que dejó mide_py en cada una.
    Devuelve (filas, problemas, corridas). `problemas` vacío = todos los CLI de los carriles Cumplen en todas."""
    problemas, filas, corridas, por_maquina = [], [], [], {}
    for m in maquinas:
        s = salidas.get(m) or {"out": "", "err": "", "rc": "?"}
        marca = [x for x in s["out"].splitlines() if x.startswith("MIDE-JSON:")]
        err = s["err"].strip().splitlines()
        if not marca:
            problemas.append(f"{m}: no respondió (código {str(s['rc']).strip()}): {err[-1] if err else 'sin salida'}")
            continue
        try:
            r = json.loads(marca[-1][len("MIDE-JSON:"):])
        except ValueError:
            problemas.append(f"{m}: la medición dejó un MIDE-JSON ilegible")
            continue
        if r["error"]:
            problemas.append(f"{m}: {r['error']}")
            continue
        if not r["carriles"]:
            problemas.append(f"{m}: la medición no trae ningún CLI de los carriles")
        por_maquina[m] = r
    union = sorted({cli for r in por_maquina.values() for cli in r["carriles"]})
    if not union:
        problemas.append("ningún CLI de los carriles quedó medido: no hay nada que decir Cumple")
    for m, r in por_maquina.items():
        medidas = {c["cli"]: c for c in r["corridas"]}
        for cli in union:
            if cli not in medidas:
                problemas.append(f"{m}/{cli}: sin medición")
        for c in r["corridas"]:
            estado, h, fuente, texto = juicio_de(c)
            filas.append(f"{m:<5} {c['cli']:<9} {estado}")
            c.update(juicio=estado, fuente=fuente, conf_sha=r["conf_sha"], en_carril=c["cli"] in union,
                     huella=h.to_dict())
            corridas.append((m, c, texto))
            if c["cli"] in union and estado != "Cumple":
                problemas.append(f"{m}/{c['cli']}: {estado}")
    return filas, problemas, corridas


def escribe_datos(destino, maquinas, corridas):
    """Una carpeta por (CLI, máquina) de ESTA corrida: lo que quedó de otra corrida en una máquina que respondió se borra, no se mezcla;
    una máquina que no respondió conserva sus datos."""
    for m in {m for m, _, _ in corridas}:
        for cli in ps.FORMATOS:
            shutil.rmtree(destino / f"{cli}-{m}", ignore_errors=True)
    for m, c, texto in corridas:
        carpeta = destino / f"{c['cli']}-{m}"
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "registro.jsonl").write_text(registro_corto(texto) if texto.strip() else "", encoding="utf-8")
        meta = {k: v for k, v in c.items() if k not in ("registro", "extras", "juicio", "huella", "en_carril")}
        meta["registro_cola"] = c["registro"][-600:]
        (carpeta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    d, repo, maquinas, juzga = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].split(), sys.argv[4] == "1"
    salidas = {
        m: {k: (d / f"{m}.{k}").read_text(errors="replace") if (d / f"{m}.{k}").exists() else ""
            for k in ("out", "err", "rc")}
        for m in maquinas
    }
    filas, problemas, corridas = agrega(salidas, maquinas)
    print("\n".join(filas))
    if juzga:
        if problemas:
            print("\nNO cumple:\n" + "\n".join(f"  - {p}" for p in problemas), file=sys.stderr)
            sys.exit(1)
        print("\nCumple en las tres máquinas.")
    else:
        escribe_datos(repo / "tests" / "datos" / "pstack", maquinas, corridas)
        if problemas:
            print("\nAvisos:\n" + "\n".join(f"  - {p}" for p in problemas), file=sys.stderr)
PY
