#!/usr/bin/env bash
# Mide, con registros reales, qué hace cada CLI de modelos con la copia fija de pstack.
# Corre en la Mac (local), la Mini (ssh) y la PC (ssh; Muse dentro de WSL). En cada máquina baja
# `origin/main` de goncloud-motor-conf a una carpeta temporal, copia ahí `pstack/` y `pstack-motor/`
# (la copia fija), lanza cada CLI con su ruta real de rutas.toml y el encargo mínimo, y trae el
# registro de cada corrida a tests/datos/pstack/<cli>-<maquina>/. Las carpetas temporales se borran.
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
import base64, json, os, re, shutil, subprocess, sys, tempfile, threading, time, tomllib, uuid
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


def toml(p):
    try:
        return tomllib.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ruta_wsl(x):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", x)
    return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}" if m else x


tmp = Path(tempfile.mkdtemp(prefix="mide-pstack-"))
salida = {"maquina": maquina, "conf_sha": "", "carriles": [], "corridas": [], "error": ""}
try:
    mq = toml(HOME / ".motor" / "maquina.toml")
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
    extra = [p for p in mq.get("path_extra", [])]
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    wsl = set(mq.get("wsl", []))

    r = corre(["git", "clone", "--quiet", "--depth", "1", "--branch", "main", conf_url, str(tmp / "conf")], env=env)
    if r.returncode:
        raise RuntimeError("no pude bajar origin/main de la conf: " + r.stderr.strip()[-300:])
    salida["conf_sha"] = corre(["git", "-C", str(tmp / "conf"), "rev-parse", "HEAD"], env=env).stdout.strip()
    copia = tmp / "copia"
    for n in ("pstack", "pstack-motor"):
        shutil.copytree(tmp / "conf" / n, copia / n)
    rutas = toml(tmp / "conf" / "rutas.toml").get("rutas", {})
    carriles = toml(tmp / "conf" / "modelos.toml").get("carriles", {})
    salida["carriles"] = sorted({rutas[m]["cli"] for ms in carriles.values() for m in ms if m in rutas})

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

    def ruta_env(cli, ruta):
        """Lo que el motor le agrega al CLI de esta ruta (rutas.toml `env`, con ${VAR} de secretos.env)."""
        base = {**env, **secretos}
        return {k: re.sub(r"\$\{(\w+)\}", lambda m: base.get(m.group(1), ""), str(v)) for k, v in (ruta.get("env") or {}).items()}

    def entorno_de(cli, ruta):
        """El entorno del CLI medido: el de la máquina, sin nada MOTOR_*, y con lo que el motor le da a su ruta."""
        e = dict(env)
        if cli == "claude":
            e.update(secretos)
        e.update(ruta_env(cli, ruta))
        for k in [k for k in e if k.startswith("MOTOR_")]:
            del e[k]
        return e

    def argv_de(cli, ruta, prompt, brief, sid):
        a = []
        for x in ruta["argv"]:
            a.append(x.replace("{prompt}", prompt).replace("{brief}", brief).replace("{result}", str(tmp / "result.json")))
        if cli == "claude":
            a[1:1] = ["--plugin-dir", str(copia / "pstack"), "--session-id", sid]
        elif cli == "kimi":
            a[1:1] = ["--skills-dir", str(copia / "pstack" / "skills")]
        elif cli == "opencode":
            a[a.index(prompt):a.index(prompt)] = ["--format", "json"]
        elif cli == "grok":
            a[1:1] = ["--session-id", sid]
        elif cli == "muse":
            a[2:2] = ["--trust-workspace", "--session-id", sid]
        if cli in wsl:
            a = ["wsl.exe", "-e", "env", *(f"{k}={v}" for k, v in ruta_env(cli, ruta).items()), *(ruta_wsl(x) for x in a)]
        else:
            exe = shutil.which(a[0], path=env["PATH"])
            if exe:
                a[0] = exe
        return a

    def registro_aparte(cli, sid, t0, trabajo, stdout):
        """(texto, fuente) del registro que el motor leería; vacío si el CLI no lo deja en stdout."""
        if cli == "claude":
            base = Path(env.get("CLAUDE_CONFIG_DIR") or HOME / ".claude") / "projects"
            for c in dict.fromkeys([str(trabajo), str(trabajo.resolve())]):
                p = base / re.sub(r"[^A-Za-z0-9]", "-", c) / f"{sid}.jsonl"
                if p.is_file():
                    return p.read_text(encoding="utf-8", errors="replace"), f"~/.claude/projects/<cwd>/{sid}.jsonl"
        if cli == "grok":
            ids = [sid]
            m = re.search(r'"sessionId"\s*:\s*"([A-Za-z0-9._-]+)"', stdout)
            if m:
                ids.append(m.group(1))
            base = Path(env.get("GROK_HOME") or HOME / ".grok") / "sessions"
            for i in ids:
                for p in sorted(base.glob(f"*/{i}/updates.jsonl")):
                    return p.read_text(encoding="utf-8", errors="replace"), f"~/.grok/sessions/<cwd>/{i}/updates.jsonl"
        if cli == "muse":
            if cli in wsl:
                r = corre(["wsl.exe", "-e", "bash", "-c", f"cat ~/.local/share/muse/sessions/*/*/*/{sid}/session.jsonl"], env=env)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout, f"(WSL) ~/.local/share/muse/sessions/AAAA/MM/DD/{sid}/session.jsonl"
            else:
                for p in sorted((HOME / ".local" / "share" / "muse" / "sessions").glob(f"*/*/*/{sid}/session.jsonl")):
                    return p.read_text(encoding="utf-8", errors="replace"), f"~/.local/share/muse/sessions/AAAA/MM/DD/{sid}/session.jsonl"
        return "", ""

    def una(cli):
        res = {"cli": cli, "ruta": "", "rc": None, "segundos": 0, "raiz": raiz_de(cli), "fuente": "", "registro": "",
               "stdout_cola": "", "stderr_cola": "", "version": "", "error": ""}
        ruta_n = PREFERIDA[cli]
        ruta = rutas.get(ruta_n)
        if not ruta or ruta.get("cli") != cli:
            res["error"] = f"rutas.toml no trae la ruta {ruta_n}"
            return res
        res["ruta"] = ruta_n
        exe = ("wsl.exe" if cli in wsl else shutil.which(ruta["argv"][0], path=env["PATH"]))
        if not exe:
            res["error"] = f"{ruta['argv'][0]} no está instalado en {maquina}"
            return res
        try:
            v = corre(["wsl.exe", "-e", cli, "--version"] if cli in wsl else [exe, "--version"], env=env, timeout=60)
            res["version"] = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else ""
        except (OSError, subprocess.TimeoutExpired):
            pass
        corrida = tmp / f"corrida-{cli}"
        trabajo = corrida / "trabajo"
        trabajo.mkdir(parents=True)
        (trabajo / "README.md").write_text("# Carpeta de medición\n\nNo hay nada que cambiar aquí.\n", encoding="utf-8")
        brief = corrida / "brief.md"
        brief.write_text(encargo(cli), encoding="utf-8")
        brief_c = ruta_wsl(str(brief)) if cli in wsl else str(brief)
        prompt = f"Lee {brief_c} y ejecútalo completo sin preguntar nada."
        sid = str(uuid.uuid4())
        argv = argv_de(cli, ruta, prompt, brief_c, sid)
        t0 = time.time()
        f_out, f_err = corrida / "stdout.txt", corrida / "stderr.txt"
        try:
            with open(f_out, "wb") as so, open(f_err, "wb") as se:
                p = subprocess.run(argv, cwd=trabajo, env=entorno_de(cli, ruta), stdout=so, stderr=se,
                                   stdin=subprocess.DEVNULL, timeout=tope)
            res["rc"] = p.returncode
        except subprocess.TimeoutExpired:
            res["error"] = f"pasó el tope de {tope}s"
        except OSError as e:
            res["error"] = f"no pude lanzarlo: {e}"
        out, err = (x.read_text(encoding="utf-8", errors="replace") if x.exists() else "" for x in (f_out, f_err))
        res["segundos"] = round(time.time() - t0)
        res["stdout_cola"], res["stderr_cola"] = out[-600:], err[-600:]
        texto, fuente = registro_aparte(cli, sid, t0, trabajo, out)
        if not texto:
            texto, fuente = out, "stdout"
        res["registro"], res["fuente"] = texto, fuente
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
    shutil.rmtree(tmp, ignore_errors=True)
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
import json, shutil, sys
from pathlib import Path

d, repo, maquinas, juzga = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].split(), sys.argv[4] == "1"
sys.path.insert(0, str(repo))
from motor import pstack as ps

destino = repo / "tests" / "datos" / "pstack"
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
    t = texto.strip()
    try:
        if t.startswith("["):
            return json.dumps(recorta(json.loads(t)), ensure_ascii=False)
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

problemas, filas, carriles = [], [], set()
for m in maquinas:
    out = (d / f"{m}.out").read_text(errors="replace")
    err = (d / f"{m}.err").read_text(errors="replace").strip().splitlines()
    marca = [x for x in out.splitlines() if x.startswith("MIDE-JSON:")]
    if not marca:
        problemas.append(f"{m}: no respondió (código {(d / f'{m}.rc').read_text().strip()}): {err[-1] if err else 'sin salida'}")
        continue
    s = json.loads(marca[-1][len("MIDE-JSON:"):])
    if s["error"]:
        problemas.append(f"{m}: {s['error']}")
        continue
    carriles |= set(s["carriles"])
    for c in s["corridas"]:
        cli = c["cli"]
        h = ps.huella(c["registro"], cli, c["raiz"])
        v = ps.juzga(h, ps.Exigencia(playbook=True, min_subagentes=1))
        estado = "Cumple" if isinstance(v, ps.Cumple) and v.verificable else ("NO VERIFICABLE" if isinstance(v, ps.Cumple) else "Falta: " + v.texto())
        if c["error"]:
            estado = f"{c['error']} ({estado})"
        filas.append(f"{m:<5} {cli:<9} {estado}")
        c["juicio"] = estado
        c["en_carril"] = cli in s["carriles"]
        c["conf_sha"] = s["conf_sha"]
        if c["en_carril"] and estado != "Cumple":
            problemas.append(f"{m}/{cli}: {estado}")
        if not juzga and c["registro"]:
            carpeta = destino / f"{cli}-{m}"
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / "registro.jsonl").write_text(registro_corto(c["registro"]), encoding="utf-8")
            meta = {k: v for k, v in c.items() if k != "registro"}
            (carpeta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("\n".join(filas))
if juzga:
    esperados = {(m, cli) for m in maquinas for cli in carriles}
    vistos = {tuple(f.split()[:2]) for f in filas}
    faltan = sorted(esperados - vistos)
    problemas += [f"{m}/{cli}: sin medición" for m, cli in faltan]
    if problemas:
        print("\nNO cumple:\n" + "\n".join(f"  - {p}" for p in problemas), file=sys.stderr)
        sys.exit(1)
    print("\nCumple en las tres máquinas.")
elif problemas:
    print("\nAvisos:\n" + "\n".join(f"  - {p}" for p in problemas), file=sys.stderr)
PY
