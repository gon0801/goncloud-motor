# shellcheck shell=bash disable=SC2034
# Lo común de ops/senal-kuma*.sh: el servidor de Kuma, la línea de secretos.env y cómo leerla.
# Se carga con `.` después de lib.sh. Nunca imprime un token ni el contenido de secretos.env.

KUMA_SSH="${MOTOR_SSH_SERVIDOR:-goncloud}"
KUMA_DB="${MOTOR_KUMA_DB:-/mnt/data/appdata/uptime-kuma/data/kuma.db}"
# La directa es la que alcanzan la MacBook y la PC. El túnel es el de la Mini
# (LaunchAgent ai.goncloud.motor-senal-tunel, ssh -L 127.0.0.1:19301:10.13.13.1:3001).
# Cuál se escribe lo decide responde, en la máquina, no esta constante.
KUMA_BASE_DIRECTA="${MOTOR_KUMA_PUSH_BASE:-http://10.13.13.1:3001/api/push/}"
KUMA_BASE_TUNEL="${MOTOR_KUMA_PUSH_TUNEL:-http://127.0.0.1:19301/api/push/}"
KUMA_PUSH_BASE="$KUMA_BASE_DIRECTA"
KUMA_TUNEL_AGENTE="${MOTOR_KUMA_TUNEL_AGENTE:-ai.goncloud.motor-senal-tunel}"
KUMA_SERVIDOR_PY="$(dirname "${BASH_SOURCE[0]}")/senal-kuma-servidor.py"
SENAL_OK="señal a Kuma: configurada"

_kuma_origen() {
  printf '%s\n' "$1" | sed -E 's#(https?://[^/]+).*#\1#'
}
KUMA_ORIGEN_DIRECTA="$(_kuma_origen "$KUMA_BASE_DIRECTA")"
KUMA_ORIGEN_TUNEL="$(_kuma_origen "$KUMA_BASE_TUNEL")"

base_valida() {
  case "$1" in
    *[!a-zA-Z0-9:/._-]*) return 1 ;;
  esac
  case "$1" in
    http://*/api/push/|https://*/api/push/) return 0 ;;
  esac
  return 1
}

# servidor_kuma <alta|baja|estado> [máquina...]: el script de Python por stdin; la salida es del
# script. En alta, las máquinas extra son las que necesitan monitor de encargos.
servidor_kuma() {
  ssh_a "$KUMA_SSH" "$(printf '%q ' python3 - "$KUMA_DB" "$@")" <"$KUMA_SERVIDOR_PY"
}

# es_local_kuma <m>: la MacBook es esta misma máquina, no la Mini ni otra macOS.
# macOS no basta: la Mini también es Darwin. En la flota solo la MacBook y la Mini corren macOS
# (ops/lib.sh, maquinas_canario), así que en Darwin se compara el hostname local con el de la Mini:
# si difieren, esta máquina es la MacBook. Preguntar a la Mini, en vez de a `mac`, deja que la
# MacBook escriba sus secretos sin hacerse ssh a sí misma. Desde la Mini el hostname coincide y la
# Mac se alcanza por ssh como cualquier otra. En la PC o el CI ni se sondea: uname no es Darwin.
# El sondeo se hace una sola vez por proceso.
es_local_kuma() {
  [ "$1" = mac ] || return 1
  [ "$(uname -s 2>/dev/null)" = Darwin ] || return 1
  if [ -z "${_kuma_es_macbook:-}" ]; then
    local propio remoto
    propio="$(hostname 2>/dev/null | tr -d '\r' | tr -d '[:space:]')"
    remoto="$(corre mini hostname </dev/null 2>/dev/null | tr -d '\r' | tr -d '[:space:]')"
    if [ -n "$propio" ] && [ -n "$remoto" ] && [ "$propio" != "$remoto" ]; then
      _kuma_es_macbook=si
    else
      _kuma_es_macbook=no
    fi
  fi
  [ "$_kuma_es_macbook" = si ]
}

# corre_kuma <m> <posix> <pc>: como corre, pero la Mac se ejecuta en local.
corre_kuma() {
  if es_local_kuma "$1"; then bash -c "$2"; else corre "$@"; fi
}

# Escritura atómica de MOTOR_KUMA_URL en el secreto de un proceso:
# `.motor` (motor de planes) o `.motor-encargos` (supervisor). Cada proceso tiene su monitor y su
# token: el mismo token en los dos haría que el proceso vivo tapara la caída del otro.
# poner: el token entra por stdin una sola vez. quitar: no hay token. asegurar: reescribe la línea
# que ya estaba y sale 3 si falta. asegurar_distinto: además sale 6 si esa línea es la misma que la
# de planes; así quien llama crea un monitor propio en vez de dejar los dos procesos en uno. Cada
# archivo queda en 600.
escritor_posix() {
  local base=${3:-$KUMA_BASE_DIRECTA}
  case "$2" in
    .motor|.motor-encargos) ;;
    *) echo "subdirectorio de secretos desconocido: $2" >&2; return 2 ;;
  esac
  if [ "$1" = poner ] && ! base_valida "$base"; then
    echo "base de push desconocida" >&2
    return 2
  fi
  cat <<SCRIPT
umask 077
accion='$1'
f="\$HOME/$2/secretos.env"; t="\$f.nuevo.\$\$"
marca="\$HOME/$2/.senal-kuma-encargos"
tok= linea=
if [ "\$accion" = quitar ] && [ '$2' = .motor-encargos ] && [ ! -f "\$marca" ]; then exit 3; fi
if [ "\$accion" = poner ]; then IFS= read -r tok; [ -n "\$tok" ] || exit 3; fi
if [ "\$accion" = asegurar ] || [ "\$accion" = asegurar_distinto ]; then
  linea=\$(grep '^[[:space:]]*MOTOR_KUMA_URL=.' "\$f" 2>/dev/null | head -n 1)
  [ -n "\$linea" ] || exit 3
  if [ "\$accion" = asegurar_distinto ]; then
    plan=\$(grep '^[[:space:]]*MOTOR_KUMA_URL=.' "\$HOME/.motor/secretos.env" 2>/dev/null | head -n 1)
    tok_de=\$(printf '%s\n' "\$linea" | sed 's#.*/##; s/[?#].*//')
    tok_plan=\$(printf '%s\n' "\$plan" | sed 's#.*/##; s/[?#].*//')
    [ -z "\$plan" ] || [ "\$tok_plan" != "\$tok_de" ] || exit 6
  fi
fi
mkdir -p "\$HOME/$2" || exit 4
if [ -f "\$f" ]; then grep -v '^[[:space:]]*MOTOR_KUMA_URL=' "\$f" >"\$t"; [ \$? -le 1 ] || exit 4; else : >"\$t"; fi
if [ -s "\$t" ] && [ "\$(tail -c1 "\$t" | wc -l)" -eq 0 ]; then echo >>"\$t"; fi
if [ "\$accion" = poner ]; then printf 'MOTOR_KUMA_URL=%s%s\n' '$base' "\$tok" >>"\$t"; fi
if [ "\$accion" = asegurar ] || [ "\$accion" = asegurar_distinto ]; then printf '%s\n' "\$linea" >>"\$t"; fi
chmod 600 "\$t" && mv "\$t" "\$f" || exit 5
if [ "\$accion" = poner ] && [ '$2' = .motor-encargos ]; then : >"\$marca" && chmod 600 "\$marca" || exit 5; fi
if [ "\$accion" = quitar ] && [ '$2' = .motor-encargos ]; then rm -f "\$marca"; fi
SCRIPT
}

escritor_pc() {
  local base=${3:-$KUMA_BASE_DIRECTA}
  case "$2" in
    .motor|.motor-encargos) ;;
    *) echo "subdirectorio de secretos desconocido: $2" >&2; return 2 ;;
  esac
  if [ "$1" = poner ] && ! base_valida "$base"; then
    echo "base de push desconocida" >&2
    return 2
  fi
  cat <<SCRIPT
\$ErrorActionPreference = 'Stop'
\$accion = '$1'
\$sub = '$2'
if (\$accion -eq 'poner') { \$tok = [Console]::In.ReadLine(); if (-not \$tok) { exit 3 } }
\$dir = Join-Path \$env:USERPROFILE \$sub
\$f = Join-Path \$dir 'secretos.env'
\$t = "\${f}.nuevo"
\$marca = Join-Path \$dir '.senal-kuma-encargos'
if (\$accion -eq 'quitar' -and \$sub -eq '.motor-encargos' -and -not (Test-Path -LiteralPath \$marca)) { exit 3 }
\$linea = \$null
if (\$accion -eq 'asegurar' -or \$accion -eq 'asegurar_distinto') {
  if (Test-Path -LiteralPath \$f) { \$linea = Get-Content -LiteralPath \$f -Encoding UTF8 | Where-Object { \$_ -cmatch '^\s*MOTOR_KUMA_URL=.' } | Select-Object -First 1 }
  if (-not \$linea) { exit 3 }
  if (\$accion -eq 'asegurar_distinto') {
    \$plan = Join-Path (Join-Path \$env:USERPROFILE '.motor') 'secretos.env'
    \$lineaPlan = \$null
    if (Test-Path -LiteralPath \$plan) { \$lineaPlan = Get-Content -LiteralPath \$plan -Encoding UTF8 | Where-Object { \$_ -cmatch '^\s*MOTOR_KUMA_URL=.' } | Select-Object -First 1 }
    \$tokDe = (\$linea -split '/')[-1] -replace '[?#].*$', ''
    \$tokPlan = (\$lineaPlan -split '/')[-1] -replace '[?#].*$', ''
    if (\$lineaPlan -and \$tokPlan -ceq \$tokDe) { exit 6 }
  }
}
New-Item -ItemType Directory -Force -Path \$dir | Out-Null
\$lineas = @()
if (Test-Path -LiteralPath \$f) { \$lineas = @(Get-Content -LiteralPath \$f -Encoding UTF8 | Where-Object { \$_ -cnotmatch '^\s*MOTOR_KUMA_URL=' }) }
if (\$accion -eq 'poner') { \$lineas += ('MOTOR_KUMA_URL=$base' + \$tok) }
if (\$accion -eq 'asegurar' -or \$accion -eq 'asegurar_distinto') { \$lineas += \$linea }
[IO.File]::WriteAllLines(\$t, [string[]]\$lineas)
Move-Item -LiteralPath \$t -Destination \$f -Force
if (\$accion -eq 'poner' -and \$sub -eq '.motor-encargos') { Set-Content -LiteralPath \$marca -Value 'creado por ops/senal-kuma.sh' }
if (\$accion -eq 'quitar' -and \$sub -eq '.motor-encargos') { Remove-Item -LiteralPath \$marca -Force }
SCRIPT
}

# linea_en <mac|mini|pc> <poner|quitar|asegurar|asegurar_distinto> <.motor|.motor-encargos>:
# el token (si poner) llega por stdin, nunca en la línea de comandos.
linea_en() {
  local m=$1 accion=$2 sub=$3 base=${4:-$KUMA_BASE_DIRECTA} codificado
  if [ "$accion" = poner ] && ! base_valida "$base"; then
    echo "base de push desconocida" >&2
    return 2
  fi
  if [ "$m" = pc ]; then
    codificado="$(escritor_pc "$accion" "$sub" "$base" | python3 -I -c 'import base64,sys; print(base64.b64encode(sys.stdin.read().encode("utf-16-le")).decode())')"
    corre pc x "powershell -NoProfile -NonInteractive -EncodedCommand $codificado"
  elif es_local_kuma "$m"; then
    bash -c "$(escritor_posix "$accion" "$sub" "$base")"
  else
    corre "$m" "$(escritor_posix "$accion" "$sub" "$base")" x
  fi
}

posix_motor='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; if python3 -c "import tomllib" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi; cd "$HOME/motor" && PYTHONPATH=. $py -m motor'
# -X utf8: sin él, el Python de la PC escribe su salida por ssh en la página ANSI y la ñ llega como 0xF1.
pc_motor="cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -X utf8 -m motor"

# senal_configurada <m>: 0 si `motor doctor --json` de esa máquina dice SENAL_OK. La salida se lee como
# bytes y se decodifica utf-8 con respaldo cp1252: la PC escribe la ñ en su página ANSI (medido en MOTOR 01).
senal_configurada() {
  local m=$1 salida
  salida="$(corre_kuma "$m" "$posix_motor doctor --home \"\$HOME/.motor\" --json" "$pc_motor doctor --home %USERPROFILE%\\.motor --json" </dev/null 2>/dev/null | tr -d '\r')" || true
  printf '%s' "$salida" | python3 -I -c 'import json,sys
try:
    datos = sys.stdin.buffer.read()
    try:
        texto = datos.decode("utf-8")
    except UnicodeDecodeError:
        texto = datos.decode("cp1252")
    sys.exit(0 if json.loads(texto).get("senal") == sys.argv[1] else 1)
except ValueError:
    sys.exit(1)' "$SENAL_OK"
}

# revisa_secretos <mac|mini>: una línea por problema en los dos secretos POSIX (vacío si están bien).
# También exige que apunten a monitores distintos: el mismo token en los dos haría que un proceso
# tapara la caída del otro. Nunca imprime la línea ni el token.
revisa_secretos() {
  local salida
  salida="$(corre_kuma "$1" 'for d in "$HOME/.motor" "$HOME/.motor-encargos"; do
  f="$d/secretos.env"
  [ -f "$f" ] || { echo "falta $f"; continue; }
  grep -q "^[[:space:]]*MOTOR_KUMA_URL=." "$f" || echo "sin MOTOR_KUMA_URL en $f"
  modo=$(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f")
  [ "$modo" = 600 ] || echo "$f no está en 600 (modo: ${modo:-desconocido})"
done
a=$(grep "^[[:space:]]*MOTOR_KUMA_URL=" "$HOME/.motor/secretos.env" 2>/dev/null | head -n 1)
b=$(grep "^[[:space:]]*MOTOR_KUMA_URL=" "$HOME/.motor-encargos/secretos.env" 2>/dev/null | head -n 1)
tok_a=$(printf "%s\n" "$a" | sed "s#.*/##; s/[?#].*//")
tok_b=$(printf "%s\n" "$b" | sed "s#.*/##; s/[?#].*//")
[ -z "$a" ] || [ "$tok_a" != "$tok_b" ] || echo "los dos secretos apuntan al mismo monitor"' x)" || { echo "no pude revisar los secretos"; return; }
  printf '%s\n' "$salida"
}

# responde <m> <origen>: 0 si esa máquina recibe HTTP 1xx–5xx en origen/.
# --max-redirs 0: el 302 de Kuma cuenta aunque el seguimiento no llegue a ningún lado.
# En la PC, curl.exe sale 0 con ese mismo 302 (sin -f); cmd se comería %{http_code}.
responde() {
  local m=$1 origen=$2 codigo c t
  c="${MOTOR_KUMA_CONECTA:-3}"
  t="${MOTOR_KUMA_SONDEO:-5}"
  if [ "$m" = pc ]; then
    corre pc x "curl.exe -sS -o NUL --connect-timeout $c --max-time $t ${origen}/" </dev/null >/dev/null 2>&1
    return $?
  fi
  codigo="$(corre_kuma "$m" "curl -sS -o /dev/null --connect-timeout $c --max-time $t --max-redirs 0 -w '%{http_code}' -- ${origen}/" x </dev/null 2>/dev/null || true)"
  codigo="$(printf '%s' "$codigo" | tr -d '[:space:]')"
  case "$codigo" in
    [1-5][0-9][0-9]) return 0 ;;
    *) return 1 ;;
  esac
}

# base_para <m>: la base que esa máquina alcanza. Primero la IP directa, si no el túnel.
# Si ninguna responde, no imprime base: quien llama no debe escribir.
base_para() {
  local m=$1
  if responde "$m" "$KUMA_ORIGEN_DIRECTA"; then
    printf '%s\n' "$KUMA_BASE_DIRECTA"
    return 0
  fi
  if responde "$m" "$KUMA_ORIGEN_TUNEL"; then
    printf '%s\n' "$KUMA_BASE_TUNEL"
    return 0
  fi
  echo "senal-kuma: $m: ni ${KUMA_ORIGEN_DIRECTA#*://} ni el túnel ${KUMA_ORIGEN_TUNEL#*://} responden desde esa máquina" >&2
  return 1
}

_script_origenes() {
  cat <<'EOS'
for f in "$HOME/.motor/secretos.env" "$HOME/.motor-encargos/secretos.env"; do
  [ -f "$f" ] || continue
  linea=$(grep '^[[:space:]]*MOTOR_KUMA_URL=.' "$f" 2>/dev/null | head -n 1 || true)
  [ -n "$linea" ] || continue
  origen=$(printf '%s\n' "$linea" | sed -nE 's#^[[:space:]]*MOTOR_KUMA_URL=(https?://[^/[:space:]]+)/.*#\1#p' || true)
  if [ -n "$origen" ]; then printf '%s\n' "$origen"; else printf '%s\n' NOSEENTIENDE; fi
done
exit 0
EOS
}

_ORIGENES_PC_PY='import os, pathlib, re
home = pathlib.Path(os.environ.get("USERPROFILE") or os.environ["HOME"])
for sub in (".motor", ".motor-encargos"):
    ruta = home / sub / "secretos.env"
    if not ruta.is_file():
        continue
    try:
        texto = ruta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        print("ILEGIBLE")
        continue
    for linea in texto.splitlines():
        hallado = re.match(r"\s*MOTOR_KUMA_URL=(https?://[^/\s]+)/", linea)
        if hallado:
            print(hallado.group(1))
            break
        if re.match(r"\s*MOTOR_KUMA_URL=.", linea):
            print("NOSEENTIENDE")
            break
'

_origenes_pc() {
  local b64
  b64="$(printf '%s' "$_ORIGENES_PC_PY" | python3 -I -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())')"
  corre pc x "$PC_UV run --python 3.12 python -c \"import base64,sys; exec(base64.b64decode(sys.argv[1]))\" $b64"
}

_origenes_de() {
  local m=$1
  if [ "$m" = pc ]; then
    _origenes_pc
    return $?
  fi
  corre_kuma "$m" "$(_script_origenes)" x </dev/null
}

_kuma_decir() {
  case "${_kuma_dichos:-}" in
    *"|$1|"*) return 0 ;;
  esac
  _kuma_dichos="${_kuma_dichos:-}|$1|"
  printf '%s\n' "$1"
}

# problemas_direccion <m>: una línea por dirección escrita que esa máquina no alcanza.
# En la Mini, si el túnel no responde y es el camino (escrito, o la directa tampoco), lo dice.
# Nunca imprime el token: el remoto solo devuelve scheme://host:port.
problemas_direccion() {
  local m=$1 salida rc origen vimos_tunel=0
  _kuma_dichos=
  salida="$(_origenes_de "$m" 2>/dev/null)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    _kuma_decir "no pude comprobar si la dirección escrita es alcanzable"
    return 0
  fi
  while IFS= read -r origen || [ -n "$origen" ]; do
    [ -n "$origen" ] || continue
    case "$origen" in
      NOSEENTIENDE|ILEGIBLE)
        _kuma_decir "la dirección escrita no se entiende"
        continue ;;
      http://[A-Za-z0-9.:-]*|https://[A-Za-z0-9.:-]*) ;;
      *)
        _kuma_decir "la dirección escrita no se entiende"
        continue ;;
    esac
    [ "$origen" = "$KUMA_ORIGEN_TUNEL" ] && vimos_tunel=1
    if ! responde "$m" "$origen"; then
      _kuma_decir "la dirección escrita ${origen#*://} no es alcanzable desde esa máquina"
    fi
  done < <(printf '%s\n' "$salida")
  if [ "$m" = mini ]; then
    if ! responde mini "$KUMA_ORIGEN_TUNEL"; then
      if [ "$vimos_tunel" = 1 ] || ! responde mini "$KUMA_ORIGEN_DIRECTA"; then
        _kuma_decir "el túnel $KUMA_TUNEL_AGENTE está caído (${KUMA_ORIGEN_TUNEL#*://} no responde)"
      fi
    fi
  fi
}
