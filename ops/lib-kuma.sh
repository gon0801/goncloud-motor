# shellcheck shell=bash disable=SC2034
# Lo común de ops/senal-kuma*.sh: el servidor de Kuma, la línea de secretos.env y cómo leerla.
# Se carga con `.` después de lib.sh. Nunca imprime un token ni el contenido de secretos.env.

KUMA_SSH="${MOTOR_SSH_SERVIDOR:-goncloud}"
KUMA_DB="${MOTOR_KUMA_DB:-/mnt/data/appdata/uptime-kuma/data/kuma.db}"
KUMA_PUSH_BASE="${MOTOR_KUMA_PUSH_BASE:-http://10.13.13.1:3001/api/push/}"
KUMA_SERVIDOR_PY="$(dirname "${BASH_SOURCE[0]}")/senal-kuma-servidor.py"
SENAL_OK="señal a Kuma: configurada"

# servidor_kuma <alta|baja|estado>: el script de Python por stdin; la salida es del script.
servidor_kuma() {
  ssh_a "$KUMA_SSH" "$(printf '%q ' python3 - "$KUMA_DB" "$1")" <"$KUMA_SERVIDOR_PY"
}

# Escritura atómica de la línea MOTOR_KUMA_URL en ~/.motor/secretos.env (temporal y reemplazo).
# poner: el token entra por stdin. quitar: no hay token. Termina con el archivo en 600.
escritor_posix() {
  cat <<SCRIPT
umask 077
accion='$1'
f="\$HOME/.motor/secretos.env"; t="\$f.nuevo.\$\$"
mkdir -p "\$HOME/.motor"
if [ "\$accion" = poner ]; then IFS= read -r tok; [ -n "\$tok" ] || exit 3; fi
if [ -f "\$f" ]; then grep -v '^[[:space:]]*MOTOR_KUMA_URL=' "\$f" >"\$t"; [ \$? -le 1 ] || exit 4; else : >"\$t"; fi
if [ -s "\$t" ] && [ "\$(tail -c1 "\$t" | wc -l)" -eq 0 ]; then echo >>"\$t"; fi
if [ "\$accion" = poner ]; then printf 'MOTOR_KUMA_URL=%s%s\n' '$KUMA_PUSH_BASE' "\$tok" >>"\$t"; fi
chmod 600 "\$t" && mv "\$t" "\$f"
SCRIPT
}

escritor_pc() {
  cat <<SCRIPT
\$ErrorActionPreference = 'Stop'
\$accion = '$1'
if (\$accion -eq 'poner') { \$tok = [Console]::In.ReadLine(); if (-not \$tok) { exit 3 } }
\$dir = Join-Path \$env:USERPROFILE '.motor'
\$f = Join-Path \$dir 'secretos.env'
\$t = "\${f}.nuevo"
New-Item -ItemType Directory -Force -Path \$dir | Out-Null
\$lineas = @()
if (Test-Path -LiteralPath \$f) { \$lineas = @(Get-Content -LiteralPath \$f | Where-Object { \$_ -notmatch '^\s*MOTOR_KUMA_URL=' }) }
if (\$accion -eq 'poner') { \$lineas += ('MOTOR_KUMA_URL=$KUMA_PUSH_BASE' + \$tok) }
Set-Content -LiteralPath \$t -Value \$lineas -Encoding Ascii
Move-Item -LiteralPath \$t -Destination \$f -Force
SCRIPT
}

# linea_en <mini|pc> <poner|quitar>: el token (si poner) llega por stdin, nunca en la línea de comandos.
linea_en() {
  local m=$1 accion=$2 codificado
  if [ "$m" = pc ]; then
    codificado="$(escritor_pc "$accion" | python3 -I -c 'import base64,sys; print(base64.b64encode(sys.stdin.read().encode("utf-16-le")).decode())')"
    corre pc x "powershell -NoProfile -NonInteractive -EncodedCommand $codificado"
  else
    corre "$m" "$(escritor_posix "$accion")" x
  fi
}

posix_motor='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; if python3 -c "import tomllib" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi; cd "$HOME/motor" && PYTHONPATH=. $py -m motor'
pc_motor="cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor"

# senal_configurada <m>: 0 si `motor doctor --json` de esa máquina dice SENAL_OK.
senal_configurada() {
  local m=$1 salida
  salida="$(corre "$m" "$posix_motor doctor --home \"\$HOME/.motor\" --json" "$pc_motor doctor --home %USERPROFILE%\\.motor --json" </dev/null 2>/dev/null | tr -d '\r')" || true
  printf '%s' "$salida" | python3 -I -c 'import json,sys
try:
    sys.exit(0 if json.load(sys.stdin).get("senal") == sys.argv[1] else 1)
except ValueError:
    sys.exit(1)' "$SENAL_OK"
}
