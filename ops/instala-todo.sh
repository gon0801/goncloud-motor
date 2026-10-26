#!/usr/bin/env bash
# Instala el motor en la Mac, la Mini y/o la PC.
# --maquina mac|mini|pc instala solo esa. Sin ella, las tres.
# --conf-sha SHA fija la configuración. Sin ella se toma una vez el HEAD del remoto
# y no se vuelve a seguir la rama.
# El destino se valida en copia. El recibo de origen se escribe antes del primer
# checkout. La misma pareja no toca el anterior. Un intento pendiente conserva el suyo.
# No libera mantenimiento ni rebaja el marcador de formato.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"
raiz="${MOTOR_INSTALA_RAIZ:-$(cd "$aqui/.." && pwd)}"
fuente="$(cd "$aqui/.." && pwd)"
rama="${MOTOR_RAMA:-main}"
toma_opciones_e 1 "$@" || exit $?
maquina_programa="$(origen_maquina_b64)" || exit 1

estado="$(mktemp -d)"
trap 'rm -rf "$estado"' EXIT

falla_lectura() {
  echo "$1: no pude guardar la revision anterior; reintenta sin tocar codigo" >&2
  exit 75
}

# --- alcance: sin escribir marcas. Si una máquina no contesta, no se etiqueta.
for m in $MAQUINAS; do
  if [ "$m" = pc ]; then
    ssh_a "$(host_de pc)" "echo ok" >/dev/null || falla_lectura pc
  else
    en_posix "$m" "$m" <<'REMOTO' >/dev/null || falla_lectura "$m"
set -euo pipefail
echo ok
REMOTO
  fi
done

# 1. La etiqueta, sobre un SHA que ya está en la rama principal (nunca sobre algo sin mergear).
reintenta 3 git -C "$raiz" fetch -q --force --tags origin || { echo "no pude traer origin" >&2; exit 3; }
sha="$(git -C "$raiz" rev-parse HEAD)" || exit 3
if ! git -C "$raiz" merge-base --is-ancestor "$sha" "origin/$rama"; then
  echo "el SHA ${sha:0:12} no está mergeado en origin/$rama: no se etiqueta" >&2
  exit 3
fi
niega_otro_commit() {
  echo "la etiqueta $ETIQUETA ya existe en ${1:0:7} y esta instalación es ${sha:0:7}: sube la etiqueta en ops/lib.sh" >&2
  exit 3
}
if ! sha_etiqueta="$(reintenta 3 sha_publicado "$raiz")"; then
  echo "no pude consultar las etiquetas de origin" >&2
  exit 3
fi
if [ -n "$sha_etiqueta" ]; then
  [ "$sha_etiqueta" = "$sha" ] || niega_otro_commit "$sha_etiqueta"
  echo "etiqueta $ETIQUETA ya existe en origin: se respeta"
else
  if ! reintenta 3 git -C "$raiz" push -q origin "$sha:refs/tags/$ETIQUETA"; then
    sha_etiqueta="$(sha_publicado "$raiz")" && [ -n "$sha_etiqueta" ] \
      || { echo "no pude empujar $ETIQUETA" >&2; exit 3; }
    [ "$sha_etiqueta" = "$sha" ] || niega_otro_commit "$sha_etiqueta"
    echo "etiqueta $ETIQUETA la creó otro mientras tanto: se respeta"
  else
    echo "etiqueta $ETIQUETA creada en ${sha:0:12}"
  fi
fi

if [ -n "$CONF_SHA_PEDIDO" ]; then
  conf_sha="$CONF_SHA_PEDIDO"
else
  salida_conf="$(git ls-remote --symref "$CONF_URL" HEAD)" || { echo "no pude fijar el SHA de la conf" >&2; exit 3; }
  conf_sha="$(printf '%s\n' "$salida_conf" | awk 'NF && $1 !~ /^ref:/ {print $1; exit}')"
  [ -n "$conf_sha" ] || { echo "no pude fijar el SHA de la conf" >&2; exit 3; }
fi
echo "conf fijada en ${conf_sha:0:12}"

campo() { # campo <m> <nombre> [valor]
  if [ $# -eq 3 ]; then printf '%s' "$3" >"$estado/$1.$2"; else cat "$estado/$1.$2" 2>/dev/null || true; fi
}

lee_posix() {
  local m=$1
  en_posix "$m" "$m" <<'REMOTO'
set -euo pipefail
motor="$HOME/motor" conf="$HOME/motor-conf" home="$HOME/.motor"
codigo="-"; confs="-"; tsha="-"; tgit="-"; link=no
if [ -d "$motor/.git" ]; then codigo="$(git -C "$motor" rev-parse HEAD)"; fi
if [ -d "$conf/.git" ]; then
  confs="$(git -C "$conf" rev-parse HEAD)"
  if git -C "$conf" cat-file -e "HEAD:maquinas/$1.toml" 2>/dev/null; then
    tgit="$(git -C "$conf" show "HEAD:maquinas/$1.toml" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
  fi
fi
if [ -f "$home/maquina.toml" ]; then
  tsha="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$home/maquina.toml")"
fi
if [ -L "$home/conf" ] && [ "$(readlink "$home/conf")" = "$conf" ]; then link=ok; fi
echo "codigo $codigo"
echo "conf $confs"
echo "toml_sha $tsha"
echo "toml_git $tgit"
echo "link $link"
if [ -f "$home/instalacion-pendiente.json" ]; then
  python3 - "$home/instalacion-pendiente.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print("pending si")
print("pend_intento", d.get("intento") or "")
print("pend_codigo", (d.get("anterior") or {}).get("codigo") or "")
print("pend_conf", (d.get("anterior") or {}).get("conf") or "")
PY
else
  echo "pending no"
fi
if [ -f "$home/instalacion.json" ]; then
  python3 - "$home/instalacion.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print("comp si")
print("comp_codigo", (d.get("anterior") or {}).get("codigo") or "")
print("comp_conf", (d.get("anterior") or {}).get("conf") or "")
PY
else
  echo "comp no"
fi
if [ -f "$home/etiqueta-anterior" ]; then
  echo "marca si"
  echo "marca_valor $(cat "$home/etiqueta-anterior")"
else
  echo "marca no"
fi
REMOTO
}

lee_pc() {
  local u='%USERPROFILE%' h rc out estado
  h="$(host_de pc)"
  ssh_a "$h" "echo ok" >/dev/null || return 1
  # 0 y vacío: no está. Otro código (128 incluido) no es "ausente": no se anota origen vacío.
  rev_pc() {
    local cmd=$1 crudo
    set +e
    crudo="$(ssh_a "$h" "$cmd")"
    rc=$?
    set -e
    crudo="$(printf '%s' "$crudo" | tr -d '[:space:]')"
    [ "$rc" -eq 255 ] && return 1
    if [ "$rc" -eq 0 ] && [ -n "$crudo" ]; then printf '%s\n' "$crudo"; return 0; fi
    if [ "$rc" -eq 0 ]; then echo -; return 0; fi
    return 1
  }
  out="$(rev_pc "if exist $u\\motor\\.git git -C $u\\motor rev-parse HEAD")" || return 1
  echo "codigo $out"
  out="$(rev_pc "if exist $u\\motor-conf\\.git git -C $u\\motor-conf rev-parse HEAD")" || return 1
  echo "conf $out"
  set +e
  estado="$(py_pc_prog "$ESTADO_TOML_PC_B64")"
  rc=$?
  set -e
  [ "$rc" -eq 0 ] || return 1
  echo "toml_sha $(printf '%s\n' "$estado" | sed -n '1p')"
  echo "toml_git $(printf '%s\n' "$estado" | sed -n '2p')"
  echo "link $(printf '%s\n' "$estado" | sed -n '3p')"
  recibo_pc() {
    local nombre=$1
    set +e
    out="$(lee_archivo_pc "$nombre")"
    rc=$?
    set -e
    [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ] || return 1
    return 0
  }
  recibo_pc instalacion-pendiente.json || return 1
  if [ "$rc" -eq 0 ]; then
    PYTHONPATH="$fuente" python3 -c 'import json,sys
d=json.loads(sys.stdin.read())
print("pending si")
print("pend_intento", d.get("intento") or "")
print("pend_codigo", (d.get("anterior") or {}).get("codigo") or "")
print("pend_conf", (d.get("anterior") or {}).get("conf") or "")' <<<"$out" || return 1
  else
    echo "pending no"
  fi
  recibo_pc instalacion.json || return 1
  if [ "$rc" -eq 0 ]; then
    python3 -c 'import json,sys
d=json.loads(sys.stdin.read())
print("comp si")
print("comp_codigo", (d.get("anterior") or {}).get("codigo") or "")
print("comp_conf", (d.get("anterior") or {}).get("conf") or "")' <<<"$out" || return 1
  else
    echo "comp no"
  fi
  set +e
  out="$(ssh_a "$h" "type $u\\.motor\\etiqueta-anterior" | tr -d '[:space:]')"
  rc=$?
  set -e
  [ "$rc" -eq 255 ] && return 1
  if [ "$rc" -eq 0 ]; then
    echo "marca si"
    echo "marca_valor $out"
  else
    echo "marca no"
  fi
}

guarda_lectura() {
  local m=$1 linea k v
  : >"$estado/$m.raw"
  while IFS= read -r linea; do
    k="${linea%% *}"
    v="${linea#* }"
    [ "$k" = "$linea" ] && v=""
    printf '%s' "$v" >"$estado/$m.$k"
  done
  for k in codigo conf toml_sha toml_git link pending pend_intento pend_codigo pend_conf comp comp_codigo comp_conf marca marca_valor; do
    [ -f "$estado/$m.$k" ] || printf '%s' "" >"$estado/$m.$k"
  done
}

# --- validar en copia, antes de marcas y checkouts
valida_posix() {
  local m=$1
  en_posix "$m" "$m" "$ETIQUETA" "$REPO_URL" "$CONF_URL" "$conf_sha" <<'REMOTO'
set -euo pipefail
m=$1 etiqueta=$2 repo=$3 confurl=$4 confsha=$5
motor="$HOME/motor" conf="$HOME/motor-conf"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if python3 -c 'import tomllib' 2>/dev/null; then py="python3"; else py="uv run --python 3.12 python"; fi
t="$(mktemp -d)"
trap 'rm -rf "$t"' EXIT
if [ -d "$motor/.git" ]; then git -C "$motor" remote set-url origin "$repo"; git -C "$motor" fetch -q --tags --force origin; src="$motor"; else git clone -q --no-checkout "$repo" "$t/src"; src="$t/src"; fi
if [ -d "$conf/.git" ]; then git -C "$conf" remote set-url origin "$confurl"; git -C "$conf" fetch -q origin; csrc="$conf"; else git clone -q --no-checkout "$confurl" "$t/csrc"; csrc="$t/csrc"; fi
git -C "$csrc" cat-file -e "$confsha^{commit}" || { echo "$m: la conf no tiene $confsha" >&2; exit 4; }
mkdir "$t/codigo" "$t/conf"
git -C "$src" archive "refs/tags/$etiqueta" | tar -x -C "$t/codigo"
git -C "$csrc" archive "$confsha" | tar -x -C "$t/conf"
[ -f "$t/conf/maquinas/$m.toml" ] || { echo "$m: la conf no trae maquinas/$m.toml" >&2; exit 4; }
if ! (cd "$t/codigo" && PYTHONPATH=. $py -m motor conf valida "$t/conf" >&2); then
  echo "$m: el código de $etiqueta rechaza la conf que va a quedar" >&2
  exit 4
fi
home_fmt=none
if [ -f "$HOME/.motor/formato" ]; then
  home_fmt="$(python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1], encoding="utf-8"))
    v=d.get("version")
    print(v if type(v) is int else "ilegible")
except Exception:
    print("ilegible")' "$HOME/.motor/formato")"
fi
tiene=no
if git -C "$src" cat-file -e "refs/tags/$etiqueta:motor/formato.py" 2>/dev/null; then tiene=si; fi
echo "motor09-home $home_fmt"
echo "motor09-tiene-formato $tiene"
REMOTO
}

valida_pc() {
  local u='%USERPROFILE%' t="%TEMP%\\motor-valida-$$" rc=0 h tiene home_fmt
  h="$(host_de pc)"
  limpia() { ssh_a "$h" "rmdir /s /q $t" >/dev/null 2>&1 || true; }
  ssh_a "$h" "(if exist $t rmdir /s /q $t) & mkdir $t\\codigo $t\\conf $t\\src $t\\csrc" || return $?
  ssh_a "$h" "git clone -q $REPO_URL $t\\src" || { limpia; return $?; }
  ssh_a "$h" "git clone -q $CONF_URL $t\\csrc" || { limpia; return $?; }
  ssh_a "$h" "git -C $t\\src archive refs/tags/$ETIQUETA | tar -x -C $t\\codigo" || rc=$?
  if [ $rc = 0 ]; then
    ssh_a "$h" "git -C $t\\csrc archive $conf_sha | tar -x -C $t\\conf" || rc=$?
  fi
  if [ $rc = 0 ]; then
    ssh_a "$h" "if not exist $t\\conf\\maquinas\\pc.toml exit /b 1" || { echo "pc: la conf no trae maquinas/pc.toml" >&2; rc=4; }
  fi
  if [ $rc = 0 ]; then
    ssh_a "$h" "cd /d $t\\codigo && $PC_UV run --python 3.12 python -m motor conf valida $t\\conf" >&2 || {
      echo "pc: el código de $ETIQUETA rechaza la conf que va a quedar" >&2
      rc=4
    }
  fi
  tiene=no
  if [ $rc = 0 ] && ssh_a "$h" "git -C $t\\src cat-file -e refs/tags/$ETIQUETA:motor/formato.py"; then tiene=si; fi
  limpia
  [ "$rc" -eq 0 ] || return "$rc"
  if ! home_fmt="$(lee_formato_pc)"; then
    rc=$?
    echo "pc: no pude leer el formato del home" >&2
    return "$rc"
  fi
  echo "motor09-home $home_fmt"
  echo "motor09-tiene-formato $tiene"
}

instala_posix() {
  local m=$1 ant_c=$2 ant_f=$3 intento=$4 limite_m=$5 accion=$6
  en_posix "$m" "$m" "$ETIQUETA" "$REPO_URL" "$CONF_URL" "$conf_sha" "$sha" "$ant_c" "$ant_f" "$intento" "$limite_m" "$accion" "$maquina_programa" "$(campo "$m" pending)" FASE_INSTALA <<'REMOTO'
set -euo pipefail
m=$1 etiqueta=$2 repo=$3 confurl=$4 confsha=$5 codesha=$6 ant_c=$7 ant_f=$8 intento=$9 limite=${10} accion=${11}
maquina_programa=${12} reintento=${13}
motor="$HOME/motor" conf="$HOME/motor-conf" home="$HOME/.motor"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if python3 -c 'import tomllib' 2>/dev/null; then py="python3"; else py="uv run --python 3.12 python"; fi
mkdir -p "$home"
origen_maquina="$(python3 -c 'import base64,sys; programa=base64.b64decode(sys.argv.pop(1)); exec(programa)' "$maquina_programa" captura "$intento" "$reintento")"
pon() { # etapa causa digest
  python3 - "$home/instalacion-pendiente.json" "$intento" "$m" "$1" "$ant_c" "$ant_f" "$codesha" "$confsha" "$limite" "$2" "$3" "$origen_maquina" <<'PY'
import base64, json, os, sys
path, intento, maquina, etapa, ac, af, dc, df, limite, causa, digest = sys.argv[1:12]
fmt = int(limite) if limite not in ("", "none", "-") else None
doc = {
    "intento": intento,
    "maquina": maquina,
    "etapa": etapa,
    "anterior": {"codigo": ac, "conf": af, "maquina": json.loads(base64.b64decode(sys.argv[12]))},
    "destino": {"codigo": dc, "conf": df, "formato": fmt},
    "causa": causa,
    "maquina_sha256": digest,
}
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f, ensure_ascii=False)
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}
falla() {
  pon "$1" "$2" ""
  echo "$m: etapa $1; causa: $2" >&2
  if [ -f "$home/mantenimiento.json" ]; then
    echo "$m: sigue retenida" >&2
  fi
  exit 1
}
if [ "$accion" = niega ]; then
  echo "$m: etapa formato; causa: no se instala este codigo" >&2
  exit 1
fi
pon origen "" ""
if [ -d "$motor/.git" ]; then git -C "$motor" remote set-url origin "$repo"; git -C "$motor" fetch -q --tags --force origin; else git clone -q "$repo" "$motor"; fi
if [ "$accion" = puede_volver ]; then
  if ! (cd "$motor" && PYTHONPATH=. $py -m motor formato --home "$home"); then
    falla origen "el motor instalado no deja volver a un codigo que no declara formato"
  fi
fi
git -C "$motor" checkout -q --detach "refs/tags/$etiqueta" || falla codigo "checkout del codigo"
pon codigo "" ""
if [ -f "$motor/ops/sol_remoto.py" ]; then
  $py -c 'import os, shutil; from pathlib import Path; h = Path.home(); d = h / ".motor/herramientas"; d.mkdir(parents=True, exist_ok=True); t = d / "sol_remoto.py.tmp"; shutil.copy2(h / "motor/ops/sol_remoto.py", t); os.replace(t, d / "sol_remoto.py")'
fi
if [ -d "$conf/.git" ]; then git -C "$conf" remote set-url origin "$confurl"; git -C "$conf" fetch -q origin; else git clone -q "$confurl" "$conf"; fi
git -C "$conf" cat-file -e "$confsha^{commit}" || falla conf "la conf no tiene $confsha"
git -C "$conf" checkout -q --detach "$confsha" || falla conf "checkout de la conf"
pon conf "" ""
[ -f "$conf/maquinas/$m.toml" ] || falla conf "la conf no trae maquinas/$m.toml"
cp "$conf/maquinas/$m.toml" "$home/maquina.toml"
ln -sfn "$conf" "$home/conf"
if ! (cd "$motor" && PYTHONPATH=. $py -m motor conf valida "$conf" >/dev/null && PYTHONPATH=. $py -m motor version >/dev/null); then
  falla observado "la conf efectiva no paso la validacion del codigo instalado"
fi
got_c="$(git -C "$motor" rev-parse HEAD)"
got_f="$(git -C "$conf" rev-parse HEAD)"
[ "$got_c" = "$codesha" ] || falla observado "el codigo quedo en ${got_c:0:12} y el destino es ${codesha:0:12}"
[ "$got_f" = "$confsha" ] || falla observado "la conf quedo en ${got_f:0:12} y el destino es ${confsha:0:12}"
digest="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$home/maquina.toml")"
python3 - "$home/maquina.toml" "$conf/maquinas/$m.toml" <<'PY' || falla observado "maquina.toml no es el de la conf fijada"
import pathlib, sys
a, b = pathlib.Path(sys.argv[1]).read_bytes(), pathlib.Path(sys.argv[2]).read_bytes()
raise SystemExit(0 if a == b else 1)
PY
pon observado "" "$digest"
python3 - "$home/instalacion-pendiente.json" "$home/instalacion.json" "$home/etiqueta-anterior" "$ant_c" <<'PY'
import json, os, shutil, sys
src, dst, marca, ant = sys.argv[1:5]
doc = json.load(open(src, encoding="utf-8"))
doc["etapa"] = "completo"
doc["causa"] = ""
tmp = dst + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f, ensure_ascii=False)
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())
os.chmod(tmp, 0o600)
os.replace(tmp, dst)
os.remove(src)
mt = marca + ".tmp"
with open(mt, "w", encoding="utf-8") as f:
    f.write(ant)
    f.flush()
    os.fsync(f.fileno())
os.replace(mt, marca)
PY
usuario="$(id -un)"
plist=/Library/LaunchDaemons/com.goncloud.motor.plist
{
  echo "# Lo que sigue lo corre una persona, una vez, con su contraseña (el motor no usa sudo)."
  echo
  echo "# 1. Servicio (arranca al prender, sin sesión iniciada):"
  echo "cd $motor && PYTHONPATH=. $py -m motor instala --maquina $m --usuario $usuario --home $home --imprime | sudo tee $plist >/dev/null"
  echo "sudo chown root:wheel $plist && sudo chmod 644 $plist"
  echo "sudo launchctl bootstrap system $plist"
  echo
  echo "# 2. sudoers (sudo visudo -f /etc/sudoers.d/motor):"
  if [ "$m" = mac ]; then
    echo "$usuario ALL=(root) NOPASSWD: /usr/bin/pmset -a disablesleep 1, /usr/bin/pmset -a disablesleep 0"
  else
    echo "$usuario ALL=(root) NOPASSWD: /sbin/shutdown"
  fi
} >"$home/instalar-servicio.txt"
echo "$m: motor $(git -C "$motor" rev-parse --short=12 HEAD) en $etiqueta, conf $(git -C "$conf" rev-parse --short=12 HEAD)"
REMOTO
}

json_recibo() { # etapa causa digest -> json en stdout
  python3 -c 'import base64,json,sys
intento, maquina, etapa, ac, af, dc, df, limite, causa, digest = sys.argv[1:11]
fmt = int(limite) if limite not in ("", "none", "-") else None
print(json.dumps({
    "intento": intento,
    "maquina": maquina,
    "etapa": etapa,
    "anterior": {"codigo": ac, "conf": af, "maquina": json.loads(base64.b64decode(sys.argv[11]))},
    "destino": {"codigo": dc, "conf": df, "formato": fmt},
    "causa": causa,
    "maquina_sha256": digest,
}, ensure_ascii=False))' "$intento" pc "$1" "$ant_c" "$ant_f" "$sha" "$conf_sha" "$limite_m" "$2" "$3" "$origen_maquina"
}

instala_pc() {
  local u='%USERPROFILE%' h b64 origen_maquina
  h="$(host_de pc)"
  origen_maquina="$(maquina_local_pc captura "$intento" "$(campo pc pending)")" || return 1
  json_recibo origen "" "" | escribe_archivo_pc instalacion-pendiente.json || return 1
  ssh_a "$h" "if exist $u\\motor\\.git (git -C $u\\motor remote set-url origin $REPO_URL)" || return 1
  ssh_a "$h" "if exist $u\\motor\\.git (git -C $u\\motor fetch -q --tags --force origin) else (git clone -q $REPO_URL $u\\motor)" || return 1
  if [ "$accion" = puede_volver ]; then
    ssh_a "$h" "cd /d $u\\motor && $PC_UV run --python 3.12 python -m motor formato --home $u\\.motor" || return 1
  fi
  if ! ssh_a "$h" "git -C $u\\motor cat-file -e refs/tags/$ETIQUETA:motor/formato.py"; then
    if [ "$accion" = directo ] && [ -n "$limite_m" ]; then
      echo "pc: etapa formato; causa: la maquina no ve motor/formato.py en la etiqueta" >&2
      return 1
    fi
  fi
  ssh_a "$h" "git -C $u\\motor checkout -q --detach refs/tags/$ETIQUETA" || return 1
  ssh_a "$h" "if exist $u\\motor\\ops\\sol_remoto.py $PC_UV run --python 3.12 python -c \"import os, shutil; from pathlib import Path; h = Path.home(); d = h / '.motor/herramientas'; d.mkdir(parents=True, exist_ok=True); t = d / 'sol_remoto.py.tmp'; shutil.copy2(h / 'motor/ops/sol_remoto.py', t); os.replace(t, d / 'sol_remoto.py')\"" || return 1
  ssh_a "$h" "if exist $u\\motor-conf\\.git (git -C $u\\motor-conf remote set-url origin $CONF_URL)" || return 1
  ssh_a "$h" "if exist $u\\motor-conf\\.git (git -C $u\\motor-conf fetch -q origin) else (git clone -q $CONF_URL $u\\motor-conf)" || return 1
  ssh_a "$h" "git -C $u\\motor-conf checkout -q --detach $conf_sha" || return 1
  ssh_a "$h" "if not exist $u\\.motor mkdir $u\\.motor" || return 1
  ssh_a "$h" "copy /y $u\\motor-conf\\maquinas\\pc.toml $u\\.motor\\maquina.toml" >/dev/null || return 1
  ssh_a "$h" "if not exist $u\\.motor\\conf mklink /j $u\\.motor\\conf $u\\motor-conf" || return 1
  ssh_a "$h" "cd /d $u\\motor && $PC_UV run --python 3.12 python -m motor conf valida $u\\motor-conf" >/dev/null || return 1
  json_recibo completo "" "" | escribe_archivo_pc instalacion.json || return 1
  borra_archivo_pc instalacion-pendiente.json || return 1
  if [ -n "$ant_c" ]; then
    ssh_a "$h" "echo $ant_c> $u\\.motor\\etiqueta-anterior.tmp && move /y $u\\.motor\\etiqueta-anterior.tmp $u\\.motor\\etiqueta-anterior" >/dev/null || return 1
  else
    ssh_a "$h" "type nul > $u\\.motor\\etiqueta-anterior.tmp && move /y $u\\.motor\\etiqueta-anterior.tmp $u\\.motor\\etiqueta-anterior" >/dev/null || return 1
  fi
  tmp="$(mktemp -d)"
  cat >"$tmp/instalar-servicio.txt" <<TXT
# Lo que sigue lo corre una persona, una vez (el motor no pide permisos de administrador).
# Servicio: tarea programada al arrancar, aunque nadie inicie sesión.
cd /d %USERPROFILE%\\motor
$PC_UV run --python 3.12 python -m motor instala --maquina pc --usuario $PC_USUARIO --home %USERPROFILE%\\.motor --imprime > %USERPROFILE%\\.motor\\tarea.xml
schtasks /create /tn $TAREA_PC /xml %USERPROFILE%\\.motor\\tarea.xml /ru $PC_USUARIO /rp *
TXT
  tar -c -C "$tmp" instalar-servicio.txt | ssh_a "$h" "tar -x -C $u\\.motor"
  local rc=$?
  rm -rf "$tmp"
  echo "pc: motor en $ETIQUETA, conf ${conf_sha:0:12}"
  return $rc
}

rechazaron=""
for m in $MAQUINAS; do
  echo "== valida la conf en $m"
  if [ "$m" = pc ]; then cap="$(valida_pc)"; else cap="$(valida_posix "$m")"; fi
  rc=$?
  printf '%s\n' "$cap" | grep -v '^motor09-' || true
  case $rc in
    0) ;;
    255) echo "FALLÓ $m: sin ssh" >&2; falla_lectura "$m" ;;
    *) rechazaron="$rechazaron $m"; continue ;;
  esac
  home_fmt="$(printf '%s\n' "$cap" | awk '/^motor09-home /{print $2}' | tail -n 1)"
  tiene="$(printf '%s\n' "$cap" | awk '/^motor09-tiene-formato /{print $2}' | tail -n 1)"
  [ -n "$home_fmt" ] && [ -n "$tiene" ] || { echo "$m: no pude leer el formato del home" >&2; rechazaron="$rechazaron $m"; continue; }
  limite_m=""
  if [ "$tiene" = si ]; then
    if texto="$(git -C "$raiz" show "$sha:motor/formato.py" 2>/dev/null)"; then
      limite_m="$(PYTHONPATH="$fuente" python3 -c 'import sys; from motor.formato import limite_declarado
v = limite_declarado(sys.stdin.read())
print("" if v is None else v)' <<<"$texto")"
    fi
  fi
  campo "$m" limite "$limite_m"
  campo "$m" home_fmt "$home_fmt"
  hv="$home_fmt"
  [ "$hv" = ilegible ] && hv=-1
  [ "$hv" = none ] && hv=""
  acc="$(accion_formato "$fuente" "$hv" "$limite_m")" || { rechazaron="$rechazaron $m"; continue; }
  campo "$m" accion "$(printf '%s\n' "$acc" | sed -n '1p')"
  campo "$m" causa "$(printf '%s\n' "$acc" | sed -n '2p')"
  if [ "$(campo "$m" accion)" = niega ]; then
    echo "$m: etapa formato; causa: $(campo "$m" causa)" >&2
    rechazaron="$rechazaron $m"
  elif [ "$(campo "$m" accion)" = puede_volver ]; then
    if [ "$m" = pc ]; then
      ssh_a "$(host_de pc)" "cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor formato --home %USERPROFILE%\\.motor" || {
        echo "pc: etapa formato; causa: $(campo "$m" causa)" >&2
        rechazaron="$rechazaron pc"
      }
    else
      en_posix "$m" "$m" <<'REMOTO' || {
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if python3 -c 'import tomllib' 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi
cd "$HOME/motor" && PYTHONPATH=. $py -m motor formato --home "$HOME/.motor"
REMOTO
        echo "$m: etapa formato; causa: $(campo "$m" causa)" >&2
        rechazaron="$rechazaron $m"
      }
    fi
  fi
done
if [ -n "$rechazaron" ]; then
  echo "La conf no pasa (o no se pudo validar) con el código de $ETIQUETA en:$rechazaron. No se cambió el código en ninguna máquina." >&2
  exit 1
fi

for m in $MAQUINAS; do
  echo "== $m"
  if [ "$m" = pc ]; then lectura="$(lee_pc)"; else lectura="$(lee_posix "$m")"; fi || falla_lectura "$m"
  guarda_lectura "$m" <<<"$lectura"
  codigo="$(campo "$m" codigo)"; confs="$(campo "$m" conf)"
  ant_c=""; ant_f=""; intento=""; modo=muta
  if [ "$(campo "$m" pending)" = si ]; then
    intento="$(campo "$m" pend_intento)"
    ant_c="$(campo "$m" pend_codigo)"
    ant_f="$(campo "$m" pend_conf)"
  elif [ "$codigo" = "$sha" ] && [ "$confs" = "$conf_sha" ] \
    && [ "$(campo "$m" toml_sha)" = "$(campo "$m" toml_git)" ] \
    && [ "$(campo "$m" toml_sha)" != "-" ] && [ "$(campo "$m" link)" = ok ]; then
    modo=noop
  else
    intento="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
    # Pareja nueva: el origen es lo que está instalado ahora. El recibo viejo
    # solo se reutiliza cuando el intento pendiente sigue abierto.
    [ "$codigo" = "-" ] && ant_c="" || ant_c="$codigo"
    [ "$confs" = "-" ] && ant_f="" || ant_f="$confs"
  fi
  if [ "$modo" = noop ]; then
    echo "$m: ya estaba en ${sha:0:12} con la conf ${conf_sha:0:12}; el anterior no cambia"
    continue
  fi
  limite_m="$(campo "$m" limite)"
  accion="$(campo "$m" accion)"
  if [ "$m" = pc ]; then
    instala_pc || { echo "FALLÓ $m" >&2; echo "No quedó instalado en: $m. No sigo con las demás; el origen de ese intento se conserva." >&2; exit 1; }
  else
    instala_posix "$m" "$ant_c" "$ant_f" "$intento" "$limite_m" "$accion" \
      || { echo "FALLÓ $m" >&2; echo "No quedó instalado en: $m. No sigo con las demás; el origen de ese intento se conserva." >&2; exit 1; }
  fi
done
echo "Instalado en:$MAQUINAS. Siguiente, si hace falta el servicio: ~/.motor/instalar-servicio.txt en cada una. Esta fase no recupera el servicio de Windows."
