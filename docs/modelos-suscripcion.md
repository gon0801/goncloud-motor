# Modelos de los encargos

Hermes y Claw pueden indicar el implementador y el revisor al entregar un encargo. El motor conserva esa elección. El revisor usa otro modelo, incluso si el mismo modelo está disponible mediante dos planes.

| Familia | Referencia para el encargo | Suscripción |
| --- | --- | --- |
| Claude | `anthropic/claude-sonnet-5-5`, `anthropic/claude-opus-5-5` | Claude |
| Codex | `openai/gpt-6.1-sol`, `openai/gpt-6-astra` | ChatGPT |
| Grok | `xai/grok-4.7` | Grok |
| GLM | `zai/glm-5.3`, `zai/glm-5.3-flash` | ZAI Coding Plan |
| DeepSeek | `opencode-go/deepseek-v4.1-flash` | OpenCode Go |
| Qwen | `qwen/qwen3.8-max` | Alibaba Token Plan |
| Muse | `meta/muse-wonder-1.0` | Meta |
| Kimi | `kimi-code/k3.1-pro` | Kimi Coding Plan |

La configuración vigente determina el orden y el esfuerzo de cada rol. Qwen y Muse quedan disponibles por elección explícita; este catálogo no los agrega a las cadenas predeterminadas.

Los clientes proceden de la generación activa de flota. Muse usa su instalación local. En Windows, Kimi K3.1 utiliza OpenCode contra el mismo endpoint del Coding Plan, con el acceso OAuth de Kimi. Esto evita la incompatibilidad de Git Bash con el aislamiento de Windows. El intento registra tanto el modelo como el cliente utilizado.

Una cuenta pendiente de autorización afecta a su encargo. El supervisor sigue atendiendo los demás. Un límite de cuota del proveedor conserva la sesión y se muestra como disponibilidad de la cuenta.

Las credenciales de planes pueden llamarse `api` o `key` en los clientes. El destino y la cuenta determinan la suscripción. DeepSeek utiliza el endpoint de OpenCode Go; GLM utiliza el Coding Plan de ZAI. El motor no cambia a un servicio facturado por consumo para resolver un error de acceso.
