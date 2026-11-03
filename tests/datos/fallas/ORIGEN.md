# De dónde sale cada captura (2026-10-20, Mac de David)

Cada `.txt` es stdout + stderr de un CLI real, tal como lo recibe el envoltorio del motor. Se
provocó la falla sin gastar cuota: sin credenciales (`HOME` vacío), con una llave falsa o contra un
servidor local que contesta el error del proveedor (para los casos de cuota y freno, que no se pueden
pedir a un proveedor real). El CLI es el real; solo cambia quién le contesta.

| Archivo | Cómo se provocó |
| --- | --- |
| claude-auth.txt | `claude -p hola --output-format json` con `HOME` vacío (sin sesión) |
| claude-auth2.txt | igual, con `ANTHROPIC_API_KEY` falsa contra api.anthropic.com (401) |
| claude-cuota.txt | `ANTHROPIC_BASE_URL` local que responde 429 "weekly usage limit … resets at …" |
| claude-rate_limit.txt | servidor local que responde 429 `rate_limit_error` por minuto |
| claude-modelo_no_disponible.txt | servidor local que responde 404 `not_found_error` del modelo |
| claude-transitorio.txt | `ANTHROPIC_BASE_URL=http://127.0.0.1:9` (conexión rechazada) |
| deepseek-auth.txt | el CLI de Claude con el entorno de la ruta deepseek y un servidor local que responde 401 |
| deepseek-cuota.txt | igual, con 402 "Insufficient Balance" (como responde DeepSeek) |
| kimi-auth.txt | `kimi -p hola --output-format stream-json` con `HOME` vacío |
| kimi-falla.txt | `kimi -p hola --auto`: "Cannot combine --prompt with --auto" |
| opencode-modelo_no_disponible.txt | `opencode run -m zai-coding-plan/no-existe-9 hola` |
| opencode-transitorio.txt | `opencode run` con `HOME` vacío: el servicio administrado no arranca (puerto ocupado) |
| grok-auth.txt | `grok --prompt-file … --output-format json` con `HOME` vacío |
| grok-falla.txt | `grok --prompt hola`: argumento inexistente |
| muse-auth.txt | `muse exec --yolo --json hola` con `HOME` vacío |
| qwen-auth.txt | `qwen --approval-mode yolo --output-format json hola` con `HOME` vacío |
| qwen-falla.txt | `qwen --approval-mode nope hola` (recortado a sus primeras 6 líneas) |

Patrones comunes: `plugins/goncloud-team/subscriptions/adapters/__init__.py` de goncloud-hermes
(rama `feat/hermes-team`); `kimi` envuelve su límite semanal como `provider.auth_error` (visto en
`tests/test_subscription_opencode.py` de Hermes).
