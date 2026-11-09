"""Candados que impiden debilitar en silencio la publicacion y los secretos.

Uso, igual en local y en el job `gate`:

    python3 tools/candados.py cuerpo
    python3 tools/candados.py secretos

`cuerpo` falla si otro camino edita el cuerpo de un PR fuera de
`_escribir_cuerpo_pr`. `secretos` falla si el cambio agrega una entrada a la
allowlist de gitleaks, quita un hook de `.pre-commit-config.yaml`, permite
saltar la bateria por el mensaje del commit o quita la regla de secretos del
prompt del responsable.

La excepcion se escribe en la misma linea, con razon y fecha (AAAA-MM-DD), y
David la aprueba en el PR. Un `--raiz` distinto permite reproducir un cambio en
un temporal sin tocar el arbol de trabajo.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Lo que hoy tiene el repo. Una entrada nueva exige su excepcion anotada.
ALLOWLIST_GITLEAKS = frozenset(
    {
        r"docs/evidence/correcciones-3-4/componentes-final-mini\.json$",
        r"docs/evidence/correcciones-3-4/componentes-final-pc\.json$",
        r"docs/evidence/correcciones-3-4/final-directores\.json$",
        r"tests/test_pstack\.py$",
    }
)
HOOKS = frozenset(
    {
        "gitleaks",
        "check-merge-conflict",
        "calidad-tipos",
        "calidad-lint",
        "calidad-io",
    }
)
ALLOWLIST_CLAVES = frozenset({"description", "targetRules", "paths", "regexes"})
TARGET_RULES = frozenset(
    {"generic-api-key", "sentry-access-token", "discord-api-token"}
)
REGLA_SECRETOS = "Nunca subas credenciales ni llaves a un repo"
GITLEAKSIGNORE_ACEPTADO = frozenset(
    """
01c939eddadef9af985396c89a20dd59986f427a:tests/test_pstack.py:generic-api-key:968
659c7c552bb154443b278f4058ad55652cbc18bf:docs/evidence/correcciones-3-4/componentes-final-mini.json:sentry-access-token:442
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:discord-api-token:10715
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:discord-api-token:11876
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:discord-api-token:13565
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:discord-api-token:19380
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:discord-api-token:4610
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1014
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10276
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1029
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10429
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10430
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10454
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10455
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10456
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10457
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10589
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10651
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10661
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10731
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10763
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10908
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10923
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10961
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10967
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10968
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10972
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10990
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:10992
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11069
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11140
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11225
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11227
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11228
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11286
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11295
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11304
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11338
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11343
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11344
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11355
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11358
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11391
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11420
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11421
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11422
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11424
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11425
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11426
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11427
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11458
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11509
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11599
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11605
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11661
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11662
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11663
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11701
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11771
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11833
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11835
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11837
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11848
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11849
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11870
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11877
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11893
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11947
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11948
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:11949
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12021
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12035
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12064
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12105
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12112
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12121
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12122
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12123
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12124
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12125
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12126
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12127
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12128
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12129
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12130
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12131
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12132
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12133
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12134
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12135
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12136
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12137
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12138
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12214
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12227
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12228
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12247
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12260
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12326
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12328
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12418
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12450
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1246
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12487
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12505
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12512
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12599
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12600
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12652
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12663
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12705
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12784
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12814
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12837
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12883
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:12897
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13000
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13020
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13098
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13130
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13141
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13145
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13174
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13201
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13340
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13531
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13544
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13619
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13622
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13625
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13626
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13627
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13628
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13652
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1384
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13844
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13919
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13959
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:13964
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14028
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1405
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14081
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14082
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14091
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14092
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14093
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14096
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14115
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14196
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14202
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14222
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14335
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14356
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14474
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14503
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14504
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14569
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14570
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14578
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14620
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14621
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:14776
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15127
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1514
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15145
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1523
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15246
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1525
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1528
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15335
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15356
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15465
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15474
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15476
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15479
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15517
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15518
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15520
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15604
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15607
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1566
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15667
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1567
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1569
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15768
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15769
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15775
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15777
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15781
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15786
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15792
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15793
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15809
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15827
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15839
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15840
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15841
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15844
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15845
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15846
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15847
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15851
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15852
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15853
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15857
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15869
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1591
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15931
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15939
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1596
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15966
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1597
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:15998
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16030
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16042
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16043
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16067
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16068
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16090
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1611
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16112
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16190
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16191
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16192
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16298
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16316
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16318
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16347
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16350
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16351
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16352
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16405
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16406
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16489
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16493
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16518
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16523
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16525
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16526
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16529
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16554
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16556
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16619
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16676
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16701
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16703
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16706
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16708
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16725
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:16985
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17059
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17158
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17159
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1723
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1724
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17250
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17251
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17253
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17318
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17319
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17337
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17338
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1746
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17647
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17898
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17899
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17901
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17949
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:1795
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:17961
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18617
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18774
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18775
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18799
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18800
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18801
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18802
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:18934
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19093
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19107
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19316
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19326
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19396
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19431
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19576
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19591
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19629
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19635
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19636
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19640
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19658
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19660
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19737
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19808
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19893
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19895
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19896
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19954
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:19963
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2082
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2096
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2343
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2344
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2346
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2394
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2473
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2480
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2490
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2539
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2749
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2755
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2775
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2888
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:2909
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3027
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3056
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3057
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3122
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3123
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3131
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3173
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3174
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3276
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3278
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3307
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3310
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3311
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3312
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3365
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3366
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3402
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3403
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3409
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3411
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3415
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3420
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3426
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3427
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3443
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3461
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3462
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3463
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3466
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3467
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3468
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3469
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3473
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3474
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3475
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3549
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3606
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3631
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3633
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3636
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3638
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3655
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3915
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:3989
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4088
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4089
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4180
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4181
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4183
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4248
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4249
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4267
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4268
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4584
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4628
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4643
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4652
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:497
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:4995
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5000
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5012
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5080
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:515
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:516
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:525
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:526
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:527
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:530
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:534
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5439
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5464
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5468
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5493
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5498
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5500
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5501
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5504
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5529
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5531
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5541
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5556
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5557
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5558
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5559
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5563
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5564
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5565
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5574
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5581
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5650
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5653
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5719
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:5760
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6142
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6337
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6338
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6360
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6395
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6561
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6562
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6571
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6572
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6573
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6576
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6595
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:666
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:6709
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:672
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:673
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:677
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:695
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:697
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7060
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7078
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7179
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7276
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7279
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7339
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7440
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7441
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7447
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7449
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7453
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7458
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7464
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7465
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7481
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7499
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7511
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7512
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7513
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7516
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7517
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7518
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7519
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7523
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7524
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7525
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7529
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7541
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7603
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7611
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7638
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7670
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7702
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7714
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7715
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7739
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:774
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7740
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7762
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7780
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7858
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7859
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7860
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7966
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7984
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:7986
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8015
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8018
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8019
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8020
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8073
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8074
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8157
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8161
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8186
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8191
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8193
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8194
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8197
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8222
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8224
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8287
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8344
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8369
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8371
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8374
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8376
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8393
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:845
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8653
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8727
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8826
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8827
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8918
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8919
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8921
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8986
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:8987
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9005
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9006
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:930
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9315
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:932
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:933
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9558
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9559
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9561
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9609
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:9621
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:generic-api-key:975
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:20089
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:20296
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:20478
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:20659
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:20840
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:21029
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:21210
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:21395
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:21576
6cf9e9697b47d217bf2b75f1ebda6f5077cc61fa:docs/evidence/correcciones-3-4/componentes-final-pc.json:sentry-access-token:483
d5002642b47a20de75898e5d6146473db9376fc1:docs/evidence/correcciones-3-4/final-directores.json:generic-api-key:437
d5002642b47a20de75898e5d6146473db9376fc1:docs/evidence/correcciones-3-4/final-directores.json:generic-api-key:545
d5002642b47a20de75898e5d6146473db9376fc1:docs/evidence/correcciones-3-4/final-directores.json:generic-api-key:595
    """.split()
)
_FECHA = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_EXCEPCION = re.compile(r"#\s*excepcion:", re.IGNORECASE)
_CADENA = re.compile(r"'''([^']*)'''|\"([^\"]*)\"|'([^']*)'")


def _leer(raiz: Path, relativa: str) -> str:
    return (raiz / relativa).read_text(encoding="utf-8")


def _anotada(linea: str) -> bool:
    return bool(_EXCEPCION.search(linea)) and bool(_FECHA.search(linea))


def _entradas_de_lista(lineas: list[str], clave: str) -> list[tuple[str, int, str]]:
    """Las cadenas de una lista TOML simple, con su linea y su texto original."""
    entradas: list[tuple[str, int, str]] = []
    dentro = False
    for numero, linea in enumerate(lineas, start=1):
        if not dentro:
            if re.match(rf"\s*{clave}\s*=\s*\[", linea):
                dentro = True
            else:
                continue
        for encontrada in _CADENA.finditer(linea):
            valor = next(g for g in encontrada.groups() if g is not None)
            entradas.append((valor, numero, linea))
        if "]" in linea.split("#", 1)[0]:
            dentro = False
    return entradas


TABLAS_DE_ALLOWLIST = frozenset(
    {"[allowlist]", "[rules.allowlist]", "[[allowlists]]", "[[rules.allowlists]]"}
)


def _claves_de_allowlist(lineas: list[str]) -> list[tuple[str, int, str]]:
    """Cada clave de cada tabla de allowlist que acepta gitleaks, con su línea.

    Cubre la global `[allowlist]` y `[[allowlists]]`, y las de una regla
    `[rules.allowlist]` y `[[rules.allowlists]]`.
    """
    claves: list[tuple[str, int, str]] = []
    dentro = False
    for numero, linea in enumerate(lineas, start=1):
        limpia = linea.split("#", 1)[0].strip()
        if limpia.startswith("["):
            dentro = limpia in TABLAS_DE_ALLOWLIST
            continue
        if not dentro:
            continue
        encontrada = re.match(r"([A-Za-z0-9_.-]+)\s*=", limpia)
        if encontrada:
            claves.append((encontrada.group(1), numero, linea))
    return claves


def _linea_anotada(lineas: list[str], clave: str) -> bool:
    return any(clave in linea and _anotada(linea) for linea in lineas)


CLAVES_CON_EFECTO = frozenset(
    {"paths", "regexes", "stopwords", "commits", "condition", "targetRules"}
)


def _lista(valor) -> list:
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, (list, tuple)):
        return list(valor)
    return []


def _valores_de_allowlist(bloque: dict) -> set[str]:
    valores: set[str] = set()
    for clave in (
        "paths", "regexes", "targetRules", "stopwords", "commits", "condition"
    ):
        valores.update(str(v) for v in _lista(bloque.get(clave)))
    return valores


def _hay_excepcion(lineas: list[str], valores: set[str]) -> bool:
    """Una excepcion anotada en una linea que nombra lo que se agrega."""
    limpios = {v for v in valores if v}
    if not limpios:
        return False
    return any(
        _anotada(linea) and any(v in linea for v in limpios) for linea in lineas
    )


def _allowlists_de(config: dict) -> list[tuple[dict, bool, str]]:
    """Todas las allowlists que tomllib entiende, sin importar su forma.

    Cubre la global `allowlist`/`allowlists` y la de cada regla, escrita como
    tabla, tabla inline o clave con punto.
    """
    salida: list[tuple[dict, bool, str]] = []
    global_uno = config.get("allowlist")
    if isinstance(global_uno, dict):
        salida.append((global_uno, False, ""))
    for bloque in config.get("allowlists") or []:
        if isinstance(bloque, dict):
            salida.append((bloque, False, ""))
    for regla in config.get("rules") or []:
        if not isinstance(regla, dict):
            continue
        ident = str(regla.get("id") or "")
        uno = regla.get("allowlist")
        if isinstance(uno, dict):
            salida.append((uno, True, ident))
        for bloque in regla.get("allowlists") or []:
            if isinstance(bloque, dict):
                salida.append((bloque, True, ident))
    return salida


def _revisar_gitleaks(raiz: Path) -> list[str]:
    try:
        texto = _leer(raiz, ".gitleaks.toml")
    except OSError as exc:
        return [f".gitleaks.toml: no se puede leer ({exc})"]
    lineas = texto.splitlines()
    problemas = []
    for clave, numero, linea in _claves_de_allowlist(lineas):
        if clave in ALLOWLIST_CLAVES or _anotada(linea):
            continue
        problemas.append(
            f".gitleaks.toml:{numero}: clave nueva en la allowlist "
            f"({clave}); anota la excepcion en la misma linea con razon y fecha"
        )
    for valor, numero, linea in _entradas_de_lista(lineas, "paths"):
        if valor in ALLOWLIST_GITLEAKS or _anotada(linea):
            continue
        problemas.append(
            f".gitleaks.toml:{numero}: entrada nueva en la allowlist "
            f"({valor}); anota la excepcion en la misma linea con razon y fecha"
        )
    for valor, numero, linea in _entradas_de_lista(lineas, "regexes"):
        if _anotada(linea):
            continue
        problemas.append(
            f".gitleaks.toml:{numero}: regex nuevo en la allowlist "
            f"({valor}); anota la excepcion en la misma linea con razon y fecha"
        )
    try:
        config = tomllib.loads(texto)
    except tomllib.TOMLDecodeError as exc:
        problemas.append(f".gitleaks.toml: no se puede leer ({exc})")
        return problemas
    extend = config.get("extend") or {}
    if extend.get("useDefault") is not True and not _linea_anotada(
        lineas, "useDefault"
    ):
        problemas.append(
            ".gitleaks.toml: useDefault apagado; las reglas por defecto no "
            "corren. Anota la excepcion en la misma linea con razon y fecha"
        )
    globales = [config.get("allowlist")]
    globales.extend(config.get("allowlists") or [])
    for bloque in globales:
        if not isinstance(bloque, dict):
            continue
        rutas = bloque.get("paths") or []
        reglas = bloque.get("targetRules")
        valores = _valores_de_allowlist(bloque)
        if rutas and not reglas and not _hay_excepcion(lineas, valores):
            problemas.append(
                ".gitleaks.toml: la allowlist tiene rutas sin targetRules, "
                "asi que valen para todas las reglas; exceptua con razon y fecha"
            )
            continue
        extra = set(reglas or ()) - TARGET_RULES
        if extra and not _hay_excepcion(lineas, extra | valores):
            problemas.append(
                ".gitleaks.toml: targetRules suma reglas "
                f"({sorted(extra)}); la allowlist cubre mas de lo aceptado"
            )
    for bloque, es_regla, regla_id in _allowlists_de(config):
        valores = _valores_de_allowlist(bloque) | {regla_id}
        desconocidas = set(bloque) - ALLOWLIST_CLAVES
        if desconocidas and not _hay_excepcion(lineas, desconocidas | valores):
            problemas.append(
                ".gitleaks.toml: clave nueva en la allowlist "
                f"({', '.join(sorted(desconocidas))}); anota la excepcion en la "
                "misma linea con razon y fecha"
            )
        nuevas = set(_lista(bloque.get("paths"))) - ALLOWLIST_GITLEAKS
        if nuevas and not _hay_excepcion(lineas, nuevas | valores):
            problemas.append(
                ".gitleaks.toml: entrada nueva en la allowlist "
                f"({', '.join(sorted(nuevas))}); anota la excepcion en la misma "
                "linea con razon y fecha"
            )
        regexes = _lista(bloque.get("regexes"))
        if regexes and not _hay_excepcion(lineas, set(regexes) | valores):
            problemas.append(
                ".gitleaks.toml: regex nuevo en la allowlist "
                f"({', '.join(sorted(regexes))}); anota la excepcion en la "
                "misma linea con razon y fecha"
            )
        if (
            es_regla
            and set(bloque) & CLAVES_CON_EFECTO
            and not _hay_excepcion(lineas, valores)
        ):
            problemas.append(
                ".gitleaks.toml: allowlist de regla sin excepcion; una allowlist "
                "por regla agrega entradas y la aprueba David en el PR"
            )
    return problemas


def _revisar_gitleaksignore(raiz: Path) -> list[str]:
    ruta = raiz / ".gitleaksignore"
    if not ruta.is_file():
        return []
    try:
        lineas = _leer(raiz, ".gitleaksignore").splitlines()
    except OSError as exc:
        return [f".gitleaksignore: no se puede leer ({exc})"]
    problemas = []
    for numero, linea in enumerate(lineas, start=1):
        limpia = linea.strip()
        if not limpia or limpia.startswith("#"):
            continue
        huella = limpia.split("#", 1)[0].strip()
        if huella in GITLEAKSIGNORE_ACEPTADO or _anotada(linea):
            continue
        problemas.append(
            f".gitleaksignore:{numero}: huella nueva sin excepcion ({huella}); "
            "anota la excepcion en la misma linea con razon y fecha"
        )
    return problemas


def _revisar_precommit(raiz: Path) -> list[str]:
    try:
        texto = _leer(raiz, ".pre-commit-config.yaml")
    except OSError as exc:
        return [f".pre-commit-config.yaml: no se puede leer ({exc})"]
    presentes = set(re.findall(r"^\s*-\s*id:\s*([A-Za-z0-9_.-]+)", texto, re.M))
    faltan = HOOKS - presentes
    problemas = []
    for hook in sorted(faltan):
        excepcion = re.search(
            rf"#\s*excepcion:.*{re.escape(hook)}\b.*\d{{4}}-\d{{2}}-\d{{2}}",
            texto,
            re.IGNORECASE,
        )
        if excepcion:
            continue
        problemas.append(
            f".pre-commit-config.yaml: falta el hook {hook}; deja la "
            "excepcion anotada con razon y fecha"
        )
    return problemas


def _funcion(arbol: ast.AST, nombre: str) -> ast.FunctionDef | None:
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == nombre:
            return nodo  # type: ignore[return-value]
    return None


_RUTAS_DE_PRUEBA = (
    ("motor/encargos/ciclo.py",),
    ("tests/test_menores_repetidos.py",),
    ("ops/tests/correr.sh",),
    ("conf-ejemplo/x.json",),
    ("docs/diseno-flujo-revisiones.md",),
    ("docs/otro.md",),
)
_EXTRAS_DE_PRUEBA = (
    (),
    ("bateria",),
    ("macos",),
    ("windows",),
    ("ops-tests",),
    ("macos", "windows"),
)
_MENSAJES_DE_PRUEBA = (
    "arregla el CLI",
    "[sin-bateria]",
    "[skip ci]",
    "skip",
    "saltar",
    "no-ci",
    "sin-ci",
    "bateria=false",
    "feat: [sin-bateria] arreglo",
)
_RUTAS_DE_CAMBIO = ("motor/encargos/ciclo.py",)


def _cargar_modulo(raiz: Path, relativa: str):
    ruta = raiz / relativa
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    nombre = "candados_" + re.sub(r"\W", "_", relativa)
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    if especificacion is None or especificacion.loader is None:
        raise ImportError(f"no se puede cargar {ruta}")
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def _activos_con_mensaje(modulo, mensaje: str) -> set[str]:
    originales = (
        modulo.mensaje_de_git, modulo.cambios_de_git, modulo._base_y_head
    )
    try:
        modulo.mensaje_de_git = lambda head: mensaje
        modulo.cambios_de_git = lambda base, head: _RUTAS_DE_CAMBIO
        modulo._base_y_head = lambda evento: ("a" * 40, "b" * 40)
        valores, _ = modulo.seleccion({"pull_request": {}})
        return {job for job, corre in valores.items() if corre}
    finally:
        (
            modulo.mensaje_de_git,
            modulo.cambios_de_git,
            modulo._base_y_head,
        ) = originales


def _revisar_bateria(raiz: Path) -> list[str]:
    """La seleccion solo puede sumar jobs por el mensaje del commit.

    Se prueba el comportamiento: para las rutas de muestra, agregar cualquier
    `extra` no puede quitar un job que el diff ya activaba, y `seleccion` con
    un mensaje adversarial tiene que correr al menos lo mismo que con uno
    neutro. Asi cualquier forma de recorte (resta, interseccion, un `or` que
    reemplaza o una rama por el texto) falla.
    """
    ruta = "motor/encargos/trabajos_ci.py"
    try:
        fuente = _leer(raiz, ruta)
        arbol = ast.parse(fuente)
    except (OSError, SyntaxError) as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    try:
        modulo = _cargar_modulo(raiz, ruta)
    except Exception as exc:
        return [f"{ruta}: no se puede cargar para probar la seleccion ({exc})"]
    problemas = []
    extras = list(_EXTRAS_DE_PRUEBA)
    for mensaje in _MENSAJES_DE_PRUEBA:
        extra = modulo._jobs_extra(mensaje)
        if extra not in extras:
            extras.append(extra)
    for rutas in _RUTAS_DE_PRUEBA:
        esperados = set(modulo.trabajos_a_correr(rutas))
        if not esperados:
            continue
        for extra in extras:
            valores, _ = modulo._calcular(rutas, extra)
            activos = {job for job, corre in valores.items() if corre}
            faltan = esperados - activos
            if faltan:
                problemas.append(
                    f"{ruta}: puede quitar jobs por el mensaje del commit "
                    f"(deja de correr {sorted(faltan)} con extra={extra})"
                )
                break
    base = _activos_con_mensaje(modulo, _MENSAJES_DE_PRUEBA[0])
    if base:
        for mensaje in _MENSAJES_DE_PRUEBA[1:]:
            activos = _activos_con_mensaje(modulo, mensaje)
            faltan = base - activos
            if faltan:
                problemas.append(
                    f"{ruta}: puede quitar jobs por el mensaje del commit "
                    f"(con {mensaje!r} deja de correr {sorted(faltan)})"
                )
                break
    conocidos = set(getattr(modulo, "TRABAJOS", ()))
    for mensaje in (*_MENSAJES_DE_PRUEBA, "[ligero]", "jobs_extra=ligero"):
        inventados = set(modulo._jobs_extra(mensaje)) - conocidos
        if inventados:
            problemas.append(
                f"{ruta}: _jobs_extra devuelve jobs que no existen "
                f"({sorted(inventados)}); solo puede sumar jobs de TRABAJOS"
            )
            break
    extra_fn = _funcion(arbol, "_jobs_extra")
    if extra_fn is not None:
        for nodo in ast.walk(extra_fn):
            if not isinstance(nodo, ast.Return) or nodo.value is None:
                continue
            if isinstance(nodo.value, ast.Tuple) and not nodo.value.elts:
                continue
            nombres = {
                hijo.id for hijo in ast.walk(nodo.value) if isinstance(hijo, ast.Name)
            }
            if "TRABAJOS" not in nombres:
                problemas.append(
                    f"{ruta}:{nodo.lineno}: _jobs_extra devuelve algo que no "
                    "filtro TRABAJOS; solo puede sumar jobs conocidos"
                )
                break
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "_git"
            and nodo.args
            and isinstance(nodo.args[0], ast.Constant)
            and nodo.args[0].value == "log"
            and _funcion_que_contiene(arbol, nodo) != "mensaje_de_git"
        ):
            problemas.append(
                f"{ruta}:{nodo.lineno}: lee el historial fuera de "
                "mensaje_de_git; solo `jobs_extra=` puede sumar jobs"
            )
    buscadores = [
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Attribute)
        and isinstance(nodo.func.value, ast.Name)
        and nodo.func.value.id == "re"
        and nodo.func.attr in ("search", "match", "findall", "fullmatch")
    ]
    for nodo in buscadores:
        if _funcion_que_contiene(arbol, nodo) != "_jobs_extra":
            problemas.append(
                f"{ruta}:{nodo.lineno}: lee el mensaje del commit fuera de "
                "_jobs_extra; solo `jobs_extra=` puede sumar jobs"
            )
    llamadas_extra = [
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Name)
        and nodo.func.id == "_jobs_extra"
    ]
    dentro = set()
    for llamada in llamadas_extra:
        for argumento in llamada.args:
            for nodo in ast.walk(argumento):
                dentro.add(id(nodo))
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "mensaje_de_git"
            and id(nodo) not in dentro
        ):
            problemas.append(
                f"{ruta}:{nodo.lineno}: lee el mensaje del commit fuera de "
                "_jobs_extra; solo `jobs_extra=` puede sumar jobs"
            )
    return problemas


def _funcion_que_contiene(arbol: ast.AST, objetivo: ast.AST) -> str:
    """El nombre de la funcion mas interna que contiene la linea del nodo."""
    linea = getattr(objetivo, "lineno", 0)
    mejor: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if nodo.lineno <= linea <= (nodo.end_lineno or nodo.lineno):
            if mejor is None or nodo.lineno > mejor.lineno:
                mejor = nodo
    return mejor.name if mejor is not None else "<module>"


def _revisar_prompt(raiz: Path) -> list[str]:
    ruta = "motor/encargos/comprobacion.py"
    try:
        texto = _leer(raiz, ruta)
        arbol = ast.parse(texto)
    except (OSError, SyntaxError) as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    funcion = _funcion(arbol, "instrucciones_responsable")
    if funcion is not None:
        for nodo in ast.walk(funcion):
            if (
                isinstance(nodo, ast.Constant)
                and isinstance(nodo.value, str)
                and REGLA_SECRETOS in nodo.value
            ):
                return []
    return [
        f"{ruta}: falta la regla de secretos del responsable "
        f"({REGLA_SECRETOS!r}); un comentario no cuenta como prompt"
    ]


def revisar_secretos(raiz: Path = RAIZ) -> list[str]:
    return [
        *_revisar_gitleaks(raiz),
        *_revisar_gitleaksignore(raiz),
        *_revisar_precommit(raiz),
        *_revisar_bateria(raiz),
        *_revisar_prompt(raiz),
    ]


def _llama_a(nodo: ast.AST, nombre: str) -> bool:
    return (
        isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Name)
        and nodo.func.id == nombre
    )


def _argumento(call: ast.Call, indice: int, nombre: str) -> ast.expr | None:
    if len(call.args) > indice:
        return call.args[indice]
    for clave in call.keywords:
        if clave.arg == nombre:
            return clave.value
    return None


def _cuerpo_limitado(nodo: ast.expr | None) -> bool:
    return _llama_a(nodo, "_cuerpo_con_limite") or _llama_a(nodo, "_cuerpo_pr")


def revisar_cuerpo(raiz: Path = RAIZ) -> list[str]:
    ruta = "motor/encargos/ciclo.py"
    try:
        arbol = ast.parse(_leer(raiz, ruta))
    except (OSError, SyntaxError) as exc:
        return [f"{ruta}: no se puede leer ({exc})"]
    problemas = []
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "actualizar_cuerpo"
        ):
            if _funcion_que_contiene(arbol, nodo) != "_escribir_cuerpo_pr":
                problemas.append(
                    f"{ruta}:{nodo.lineno}: edita el cuerpo del PR fuera de "
                    "`_escribir_cuerpo_pr`"
                )
        if _llama_a(nodo, "_escribir_cuerpo_pr"):
            cierre = _argumento(nodo, 4, "cierre")
            vacio = (
                cierre is None
                or isinstance(cierre, ast.Constant)
                and (cierre.value is None or cierre.value == "")
            )
            if len(nodo.args) != 5 or nodo.keywords or vacio:
                problemas.append(
                    f"{ruta}:{nodo.lineno}: llama a `_escribir_cuerpo_pr` sin "
                    "el cierre del motor; la puerta recorta solo el texto"
                )
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "abrir_pr"
        ):
            cuerpo = _argumento(nodo, 4, "cuerpo")
            if not _cuerpo_limitado(cuerpo):
                problemas.append(
                    f"{ruta}:{nodo.lineno}: abre un PR sin el limite del "
                    "cuerpo; usa `_cuerpo_pr` o `_cuerpo_con_limite`"
                )
    permitidos = {
        "_LIMITE_CUERPO_PR": {"_leer_cuerpo_del_espacio", "_cuerpo_con_limite"},
        "_NOTA_CUERPO_RECORTADO": {"_cuerpo_con_limite", "_recorte_es_seguro"},
    }
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Name)
            and isinstance(nodo.ctx, ast.Load)
            and nodo.id in permitidos
        ):
            funciones = permitidos[nodo.id]
            if _funcion_que_contiene(arbol, nodo) not in funciones:
                problemas.append(
                    f"{ruta}:{nodo.lineno}: usa {nodo.id} fuera de la puerta "
                    "del limite `_cuerpo_con_limite`"
                )
    puerta = _funcion(arbol, "_escribir_cuerpo_pr")
    if puerta is None:
        problemas.append(f"{ruta}: falta la puerta unica `_escribir_cuerpo_pr`")
    elif not any(_llama_a(nodo, "_cuerpo_con_limite") for nodo in ast.walk(puerta)):
        problemas.append(
            f"{ruta}: `_escribir_cuerpo_pr` no aplica el limite del cuerpo"
        )
    if _funcion(arbol, "_cuerpo_con_limite") is None:
        problemas.append(f"{ruta}: falta la puerta del limite `_cuerpo_con_limite`")
    return problemas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="candados")
    ap.add_argument("modo", choices=("cuerpo", "secretos"))
    ap.add_argument("--raiz", default=str(RAIZ))
    args = ap.parse_args(argv)
    raiz = Path(args.raiz)
    problemas = (
        revisar_cuerpo(raiz) if args.modo == "cuerpo" else revisar_secretos(raiz)
    )
    if not problemas:
        print(f"{args.modo}: sin debilitamientos")
        return 0
    print(f"{args.modo}: {len(problemas)} problema(s)", file=sys.stderr)
    for problema in problemas:
        print(f"  {problema}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
