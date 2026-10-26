#!/usr/bin/env bash
# Vuelve cada máquina a la pareja anterior anotada en el recibo de instalación.
# etiqueta-anterior solo se lee si no hay recibo. No rebaja un home en formato 3,
# no borra eventos ni la retención, y no descarga servicios.
# --maquina mac|mini|pc limita la reversa. Sin ella, las tres. No hay atomicidad entre máquinas.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"
fuente="$(cd "$aqui/.." && pwd)"
toma_opciones_e 0 "$@" || exit $?
regla="$(regla_formato_b64 "$fuente")"
maquina_programa="$(origen_maquina_b64)" || exit 1

raiz="${MOTOR_INSTALA_RAIZ:-$(cd "$aqui/.." && pwd)}"
pedido="${MOTOR_SHA:-HEAD}"
if ! entrega="$(git -C "$raiz" rev-parse "$pedido^{commit}" 2>/dev/null)"; then
  echo "no pude saber el commit de esta entrega (MOTOR_SHA='${MOTOR_SHA:-}', raiz=$raiz)" >&2
  exit 2
fi

vuelve_posix() {
  local m=$1
  en_posix "$m" "$m" "$entrega" "$regla" "$maquina_programa" <<'REMOTO'
set -euo pipefail
m=$1 entrega=$2 regla_b64=$3
maquina_programa=$4
motor="$HOME/motor" conf="$HOME/motor-conf" home="$HOME/.motor"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if python3 -c 'import tomllib' 2>/dev/null; then py="python3"; else py="uv run --python 3.12 python"; fi
if [ ! -d "$motor/.git" ]; then
  echo "$m: sin etiqueta anterior anotada; se queda como está"
  exit 0
fi
actual="$(git -C "$motor" rev-parse HEAD)"
leido="$(python3 - "$home" <<'PY'
import base64, json, sys
from pathlib import Path
home = Path(sys.argv[1])
pend, comp, marca = home / "instalacion-pendiente.json", home / "instalacion.json", home / "etiqueta-anterior"

def carga(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

origen = ""
codigo = ""
conf = ""
intento = ""
d = {}
if pend.exists():
    d = carga(pend) or {}
    ant = d.get("anterior") or {}
    origen, codigo, conf = "pendiente", ant.get("codigo") or "", ant.get("conf") or ""
    intento = d.get("intento") or ""
elif comp.exists():
    d = carga(comp) or {}
    ant = d.get("anterior") or {}
    origen, codigo, conf = "completo", ant.get("codigo") or "", ant.get("conf") or ""
    intento = d.get("intento") or ""
elif marca.exists():
    origen, codigo = "marca", marca.read_text(encoding="utf-8").strip()
print(origen)
print(codigo)
print(conf)
print(intento)
ref = (d.get("anterior") or {}).get("maquina")
print(base64.b64encode(json.dumps(ref).encode()).decode() if ref else "")
PY
)"
origen="$(printf '%s\n' "$leido" | sed -n '1p')"
etiqueta="$(printf '%s\n' "$leido" | sed -n '2p')"
conf_ant="$(printf '%s\n' "$leido" | sed -n '3p')"
intento="$(printf '%s\n' "$leido" | sed -n '4p')"
ref_maquina="$(printf '%s\n' "$leido" | sed -n '5p')"
if [ -z "$etiqueta" ]; then
  echo "$m: sin etiqueta anterior anotada; se queda como está"
  exit 0
fi
continuando=0
if [ -f "$home/reversa-pendiente.json" ]; then
  entrega_previa="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1], encoding="utf-8")).get("entrega") or ""))' "$home/reversa-pendiente.json")"
  [ "$entrega_previa" = "$entrega" ] && continuando=1
fi
if [ "$continuando" = 0 ] && [ "$actual" != "$entrega" ]; then
  echo "$m: está en ${actual:0:12}, no en el commit de la entrega ${entrega:0:12}; se queda"
  exit 0
fi
if ! revision="$(git -C "$motor" rev-parse -q --verify "$etiqueta^{commit}")"; then
  git -C "$motor" fetch -q --tags --force origin
  revision="$(git -C "$motor" rev-parse --verify "$etiqueta^{commit}")"
fi
if [ -n "$conf_ant" ] && [ -d "$conf/.git" ]; then
  if ! git -C "$conf" cat-file -e "$conf_ant^{commit}" 2>/dev/null; then
    git -C "$conf" fetch -q origin
  fi
  git -C "$conf" cat-file -e "$conf_ant:maquinas/$m.toml"
fi
texto=""
if git -C "$motor" cat-file -e "$revision:motor/formato.py" 2>/dev/null; then
  texto="$(git -C "$motor" show "$revision:motor/formato.py")"
fi
home_fmt="$(python3 -c 'import json,sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    print("none")
else:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        v = d.get("version")
        print(v if type(v) is int else "ilegible")
    except Exception:
        print("ilegible")' "$home/formato")"
decision="$(printf '%s' "$texto" | $py -c 'import base64,sys
exec(base64.b64decode(sys.argv[1]))
texto = sys.stdin.read()
home = sys.argv[2]
hv = None if home == "none" else (-1 if home == "ilegible" else int(home))
lv = limite_declarado(texto) if texto.strip() else None
accion, causa = destino_compatible(hv, lv)
print(accion)
print(causa)' "$regla_b64" "$home_fmt")"
accion="$(printf '%s\n' "$decision" | sed -n '1p')"
causa="$(printf '%s\n' "$decision" | sed -n '2p')"
anota_rev() {
  python3 - "$home/reversa-pendiente.json" "$1" "$2" "$revision" "$conf_ant" "$entrega" <<'PY'
import json, os, sys
path, etapa, causa, codigo, conf, entrega = sys.argv[1:7]
doc = {"etapa": etapa, "causa": causa, "destino": {"codigo": codigo, "conf": conf}, "entrega": entrega}
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f, ensure_ascii=False)
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}
falla_rev() {
  anota_rev "$1" "$2"
  echo "$m: etapa $1; causa: $2" >&2
  if [ -f "$home/mantenimiento.json" ]; then echo "$m: sigue retenida" >&2; fi
  exit 1
}
if [ "$accion" = niega ]; then
  echo "$m: etapa formato; causa: $causa" >&2
  if [ -f "$home/mantenimiento.json" ]; then echo "$m: sigue retenida" >&2; fi
  exit 1
fi
if [ "$accion" = puede_volver ]; then
  if ! (cd "$motor" && PYTHONPATH=. $py -m motor formato --home "$home"); then
    echo "$m: etapa formato; causa: $causa" >&2
    if [ -f "$home/mantenimiento.json" ]; then echo "$m: sigue retenida" >&2; fi
    exit 1
  fi
fi
if [ -n "$conf_ant" ] && [ ! -d "$conf/.git" ]; then
  echo "$m: etapa conf; causa: falta el repositorio de la conf" >&2
  if [ -f "$home/mantenimiento.json" ]; then echo "$m: sigue retenida" >&2; fi
  exit 1
fi
if [ -n "$ref_maquina" ]; then
  python3 -c 'import base64,sys; programa=base64.b64decode(sys.argv.pop(1)); exec(programa)' "$maquina_programa" valida "$ref_maquina"
fi
anota_rev origen ""
git -C "$motor" checkout -q --detach "$revision" || falla_rev codigo "checkout del codigo"
if [ -n "$conf_ant" ] && [ -d "$conf/.git" ]; then
  git -C "$conf" checkout -q --detach "$conf_ant" || falla_rev conf "checkout de la conf"
  fuente_toml="$conf/maquinas/$m.toml"
  if [ ! -f "$fuente_toml" ] && [ -n "$intento" ] && [ -f "$home/privado/maquina-$intento.toml" ]; then
    fuente_toml="$home/privado/maquina-$intento.toml"
  fi
  if [ -z "$ref_maquina" ]; then
    cp "$fuente_toml" "$home/maquina.toml"
    ln -sfn "$conf" "$home/conf"
    python3 -c 'import pathlib,sys; raise SystemExit(0 if pathlib.Path(sys.argv[1]).read_bytes()==pathlib.Path(sys.argv[2]).read_bytes() else 1)' "$home/maquina.toml" "$fuente_toml" || falla_rev observado "maquina.toml no quedo en la conf anterior"
  fi
fi
if [ -n "$ref_maquina" ]; then
  python3 -c 'import base64,sys; programa=base64.b64decode(sys.argv.pop(1)); exec(programa)' "$maquina_programa" restaura "$ref_maquina" || falla_rev observado "no se pudo restaurar el origen privado de la maquina"
fi
rm -f "$home/reversa-pendiente.json"
if [ "$origen" = pendiente ]; then
  rm -f "$home/instalacion-pendiente.json"
fi
echo "$m: motor $(git -C "$motor" rev-parse --short=12 HEAD) de vuelta en $etiqueta"
REMOTO
}

vuelve_pc() {
  local u='%USERPROFILE%' h anterior="" conf_ant="" origen=marca actual revision raw home_fmt accion causa texto limite ref_maquina=""
  h="$(host_de pc)"
  ssh_a "$h" "echo ok" >/dev/null 2>&1 || { echo "pc: no hay ssh" >&2; return 1; }
  set +e
  raw="$(lee_archivo_pc instalacion-pendiente.json)"
  rc=$?
  set -e
  if [ "$rc" -eq 255 ]; then echo "pc: no hay ssh" >&2; return 1; fi
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
    echo "pc: etapa recibo; causa: no pude leer instalacion-pendiente.json" >&2
    return 1
  fi
  if [ "$rc" -eq 0 ]; then
    origen=pendiente
    anterior="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print((d.get("anterior") or {}).get("codigo") or "")' <<<"$raw")" || return 1
    conf_ant="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print((d.get("anterior") or {}).get("conf") or "")' <<<"$raw")" || return 1
  else
    set +e
    raw="$(lee_archivo_pc instalacion.json)"
    rc=$?
    set -e
    if [ "$rc" -eq 255 ]; then echo "pc: no hay ssh" >&2; return 1; fi
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
      echo "pc: etapa recibo; causa: no pude leer instalacion.json" >&2
      return 1
    fi
    if [ "$rc" -eq 0 ]; then
      origen=completo
      anterior="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print((d.get("anterior") or {}).get("codigo") or "")' <<<"$raw")" || return 1
      conf_ant="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print((d.get("anterior") or {}).get("conf") or "")' <<<"$raw")" || return 1
    else
      anterior="$(ssh_a "$h" "type $u\\.motor\\etiqueta-anterior 2>nul" 2>/dev/null | tr -d '[:space:]')" || anterior=""
      origen=marca
    fi
  fi
  if [ -z "$anterior" ]; then
    echo "pc: sin etiqueta anterior anotada; se queda como está"
    return 0
  fi
  if [ "$origen" != marca ]; then
    ref_maquina="$(python3 -c 'import base64,json,sys; ref=(json.loads(sys.stdin.read()).get("anterior") or {}).get("maquina"); print(base64.b64encode(json.dumps(ref).encode()).decode() if ref else "")' <<<"$raw")" || return 1
  fi
  actual="$(ssh_a "$h" "git -C $u\\motor rev-parse HEAD" | tr -d '[:space:]')" || return 1
  [ -n "$actual" ] || { echo "pc: no pude ver el commit de ~/motor" >&2; return 1; }
  continuando=0
  set +e
  revraw="$(lee_archivo_pc reversa-pendiente.json)"
  revrc=$?
  set -e
  if [ "$revrc" -eq 255 ]; then echo "pc: no hay ssh" >&2; return 1; fi
  if [ "$revrc" -ne 0 ] && [ "$revrc" -ne 2 ]; then
    echo "pc: etapa reversa; causa: no pude leer reversa-pendiente.json" >&2
    return 1
  fi
  if [ "$revrc" -eq 0 ]; then
    entrega_previa="$(python3 -c 'import json,sys; print((json.loads(sys.stdin.read()) or {}).get("entrega") or "")' <<<"$revraw")"
    [ "$entrega_previa" = "$entrega" ] && continuando=1
  fi
  if [ "$continuando" = 0 ] && [ "$actual" != "$entrega" ]; then
    echo "pc: está en ${actual:0:12}, no en el commit de la entrega ${entrega:0:12}; se queda"
    return 0
  fi
  # Primero el objeto (fetch si no está), después el formato de ese objeto, después el checkout.
  set +e
  revision="$(ssh_a "$h" "git -C $u\\motor rev-parse -q --verify $anterior")"
  rc=$?
  set -e
  revision="$(printf '%s' "$revision" | tr -d '[:space:]')"
  if [ "$rc" -ne 0 ] || [ -z "$revision" ]; then
    ssh_a "$h" "git -C $u\\motor fetch -q --tags --force origin" || return 1
    set +e
    revision="$(ssh_a "$h" "git -C $u\\motor rev-parse --verify $anterior")"
    rc=$?
    set -e
    revision="$(printf '%s' "$revision" | tr -d '[:space:]')"
    [ "$rc" -eq 0 ] && [ -n "$revision" ] || return 1
  fi
  if ! home_fmt="$(lee_formato_pc)"; then
    echo "pc: etapa formato; causa: no pude leer el marcador" >&2
    return 1
  fi
  limite=""
  if ssh_a "$h" "git -C $u\\motor cat-file -e ${revision}:motor/formato.py" >/dev/null 2>&1; then
    texto="$(ssh_a "$h" "git -C $u\\motor show ${revision}:motor/formato.py" 2>/dev/null || true)"
    if [ -z "$texto" ]; then
      texto="$(git -C "$raiz" show "$revision:motor/formato.py" 2>/dev/null || true)"
    fi
    if [ -n "$texto" ]; then
      limite="$(PYTHONPATH="$fuente" python3 -c 'import sys; from motor.formato import limite_declarado
v=limite_declarado(sys.stdin.read()); print("" if v is None else v)' <<<"$texto")"
    fi
  fi
  hv="$home_fmt"
  [ "$hv" = ilegible ] && hv=-1
  [ "$hv" = none ] && hv=""
  decision="$(accion_formato "$fuente" "$hv" "$limite")"
  accion="$(printf '%s\n' "$decision" | sed -n '1p')"
  causa="$(printf '%s\n' "$decision" | sed -n '2p')"
  if [ "$accion" = niega ]; then
    echo "pc: etapa formato; causa: $causa" >&2
    return 1
  fi
  if [ "$accion" = puede_volver ]; then
    ssh_a "$h" "cd /d $u\\motor && $PC_UV run --python 3.12 python -m motor formato --home $u\\.motor" || {
      echo "pc: etapa formato; causa: $causa" >&2
      return 1
    }
  fi
  if [ -n "$conf_ant" ]; then
    ssh_a "$h" "git -C $u\\motor-conf cat-file -e ${conf_ant}:maquinas/pc.toml" || return 1
  fi
  anota_pc() {
    python3 -c 'import json,sys; json.dump({"etapa":sys.argv[1],"causa":sys.argv[2],"destino":{"codigo":sys.argv[3],"conf":sys.argv[4]},"entrega":sys.argv[5]}, sys.stdout)' "$1" "$2" "$revision" "$conf_ant" "$entrega" | escribe_archivo_pc reversa-pendiente.json
  }
  if [ -n "$ref_maquina" ]; then
    maquina_local_pc valida "$ref_maquina" || return 1
  fi
  anota_pc origen "" || return 1
  ssh_a "$h" "git -C $u\\motor checkout -q --detach $revision" || { anota_pc codigo "checkout del codigo"; return 1; }
  if [ -n "$conf_ant" ]; then
    ssh_a "$h" "git -C $u\\motor-conf checkout -q --detach $conf_ant" || { anota_pc conf "checkout de la conf"; return 1; }
    if [ -z "$ref_maquina" ]; then
      ssh_a "$h" "copy /y $u\\motor-conf\\maquinas\\pc.toml $u\\.motor\\maquina.toml" >/dev/null || { anota_pc observado "maquina.toml no quedo en la conf anterior"; return 1; }
      ssh_a "$h" "rmdir $u\\.motor\\conf & mklink /j $u\\.motor\\conf $u\\motor-conf" || { anota_pc observado "el enlace de la conf no quedo"; return 1; }
    fi
  fi
  if [ -n "$ref_maquina" ]; then
    maquina_local_pc restaura "$ref_maquina" || { anota_pc observado "no se pudo restaurar el origen privado de la maquina"; return 1; }
  fi
  borra_archivo_pc reversa-pendiente.json || return 1
  if [ "$origen" = pendiente ]; then
    borra_archivo_pc instalacion-pendiente.json || return 1
  fi
  echo "pc: motor de vuelta en $anterior"
}

movidas="" fallaron=""
for m in $MAQUINAS; do
  echo "== $m"
  if [ "$m" = pc ]; then out="$(vuelve_pc)"; else out="$(vuelve_posix "$m")"; fi
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '%s\n' "$out"
    case "$out" in *"de vuelta en"*) movidas="$movidas $m" ;; esac
  else
    printf '%s\n' "$out" >&2
    echo "FALLÓ $m" >&2
    fallaron="$fallaron $m"
  fi
done
if [ -n "$fallaron" ]; then echo "No quedó en la etiqueta anterior:$fallaron" >&2; exit 1; fi
if [ -z "$movidas" ]; then
  echo "nadie se mueve (el servicio sigue cargado en las tres)."
else
  echo "De vuelta en su etiqueta anterior:$movidas (el servicio sigue cargado en las tres)."
fi
