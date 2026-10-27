#!/bin/sh
set -eu
exec "${MOTOR_PYTHON:-python3}" "$(dirname "$0")/reversa_ensayo.py" "$@"
