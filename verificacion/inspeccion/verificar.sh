#!/usr/bin/env bash
# Corre TLC sobre FlujoInspeccion.tla con una configuración (por omisión main.cfg).
# Uso: verificacion/inspeccion/verificar.sh [main.cfg | antes-139.cfg | otra.cfg]
# Sale 0 si todas las reglas se cumplen y 12 si TLC encontró una violación.
set -euo pipefail
cd "$(dirname "$0")"
cfg="${1:-main.cfg}"

TLC_TOOL='http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4'
JAVA_TOOL='java@temurin-25'

if [ -n "${TLA2TOOLS:-}" ]; then
  jar="$TLA2TOOLS"; java="${JAVA:-java}"
else
  command -v mise >/dev/null || { echo "falta mise (brew install mise), o exporta TLA2TOOLS y JAVA" >&2; exit 2; }
  export MISE_CEILING_PATHS="$PWD"
  mise install --quiet "$JAVA_TOOL" "$TLC_TOOL" >/dev/null
  jar="$(mise where "$TLC_TOOL")/tla2tools.jar"
  java="$(mise which java --tool "$JAVA_TOOL")"
fi

salida="$(mktemp -d)"
trap 'rm -rf "$salida"' EXIT
"$java" -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers 1 -fp 0 \
  -metadir "$salida" -config "$cfg" FlujoInspeccion.tla
