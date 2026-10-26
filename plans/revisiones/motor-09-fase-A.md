# MOTOR 09: revisión de la fase A

**Aprobada. Resguardo y revisión realizados por el lead.**

Se conservó estado de Mac, Mini y PC en `/Users/dn/.motor-resguardo/motor09-20261026T002648Z`, carpeta privada fuera del repositorio. Cada archivo contiene una copia SQLite consistente, inbox, avisos, intentos, resultados, worktrees y metadatos de recuperación. No incluye `secretos.env`.

La revisión abrió las bases extraídas del resguardo, sin restaurarlas sobre las originales. Comprobó integridad, JSON de todos los eventos, unicidad de IDs, contenido requerido de los archivos y SHA256 del resguardo.

| Máquina | Integridad | Eventos | Último ID | Código | Configuración |
| --- | --- | --- | --- | --- | --- |
| Mac | ok | 1821 | 1821 | `1b37d63dfbc7` | `d4c110e0aa25` |
| Mini | ok | 988 | 988 | `1b37d63dfbc7` | `d4c110e0aa25` |
| PC | ok | 301 | 301 | `1b37d63dfbc7` | `d4c110e0aa25` |

El inventario actual de procesos se conservó en la evidencia privada. Mac y Mini siguen suspendidas. La tarea de PC se observó `Disabled`. Los servicios de las Mac todavía requieren administrador para descarga persistente en la futura fase F.

La evidencia detallada está en `verificacion/revision-A.json`, dentro del resguardo. No hubo cambios en bases originales, código instalado, credenciales o configuración productiva durante A.

Grok fue iniciado automáticamente para B, pero se interrumpió cuando David cambió el flujo a manual. Al detenerlo no había cambios de código. El siguiente paso es enviar manualmente el [brief B](motor-09-brief-B.md). La aprobación de A no acredita control, formato 3, Windows ni despliegue.
