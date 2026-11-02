# shellcheck shell=bash disable=SC2034
# Lo común de ops/senal-kuma*.sh: el servidor de Kuma, la línea de secretos.env y cómo leerla.
# Se carga con `.` después de lib.sh. Nunca imprime un token ni el contenido de secretos.env.

KUMA_SSH="${MOTOR_SSH_SERVIDOR:-goncloud}"
KUMA_DB="${MOTOR_KUMA_DB:-/mnt/data/appdata/uptime-kuma/data/kuma.db}"
KUMA_PUSH_BASE="${MOTOR_KUMA_PUSH_BASE:-http://10.13.13.1:3001/api/push/}"
KUMA_SERVIDOR_PY="$(dirname "${BASH_SOURCE[0]}")/senal-kuma-servidor.py"
SENAL_OK="señal a Kuma: configurada"

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
  case "$2" in
    .motor|.motor-encargos) ;;
    *) echo "subdirectorio de secretos desconocido: $2" >&2; return 2 ;;
  esac
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
if [ "\$accion" = poner ]; then printf 'MOTOR_KUMA_URL=%s%s\n' '$KUMA_PUSH_BASE' "\$tok" >>"\$t"; fi
if [ "\$accion" = asegurar ] || [ "\$accion" = asegurar_distinto ]; then printf '%s\n' "\$linea" >>"\$t"; fi
chmod 600 "\$t" && mv "\$t" "\$f" || exit 5
if [ "\$accion" = poner ] && [ '$2' = .motor-encargos ]; then : >"\$marca" && chmod 600 "\$marca" || exit 5; fi
if [ "\$accion" = quitar ] && [ '$2' = .motor-encargos ]; then rm -f "\$marca"; fi
SCRIPT
}

escritor_pc() {
  case "$2" in
    .motor|.motor-encargos) ;;
    *) echo "subdirectorio de secretos desconocido: $2" >&2; return 2 ;;
  esac
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
if (\$accion -eq 'poner') { \$lineas += ('MOTOR_KUMA_URL=$KUMA_PUSH_BASE' + \$tok) }
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
  local m=$1 accion=$2 sub=$3 codificado
  if [ "$m" = pc ]; then
    codificado="$(escritor_pc "$accion" "$sub" | python3 -I -c 'import base64,sys; print(base64.b64encode(sys.stdin.read().encode("utf-16-le")).decode())')"
    corre pc x "powershell -NoProfile -NonInteractive -EncodedCommand $codificado"
  elif es_local_kuma "$m"; then
    bash -c "$(escritor_posix "$accion" "$sub")"
  else
    corre "$m" "$(escritor_posix "$accion" "$sub")" x
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
