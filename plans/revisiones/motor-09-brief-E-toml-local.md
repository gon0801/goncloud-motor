# MOTOR 09: restaurar la configuración local anterior al revertir

Usa `pstack:poteto-mode`. Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`, conservando el diff B–E. Implementa solo este ajuste de E si David te envía el brief y detente para revisión.

Lee `plans/revisiones/motor-09-fase-E-ronda-2.md`. El origen de pareja, los comandos de recibos PC y la prueba existente ya pasaron la revisión; no los rehagas.

Reproduce:

```sh
python3 -B .motor09/revision-E/lead/toml-local.py
```

El instalador guarda una copia privada del maquina.toml local anterior; la reversa la ignora y copia el TOML del repo. Aunque código/conf vuelvan a B/F1, la configuración efectiva previa no se recupera. La sonda obtiene copia_privada_guarda_original=true y maquina_local_restaurada=false.

Guarda como parte del origen la referencia privada y huella de la configuración efectiva anterior y los datos necesarios de su enlace. En POSIX y PC, conserva esa copia antes de sobrescribir; el recibo solo contiene metadatos/referencias, nunca secretos ni el contenido privado. Un reintento parcial no debe reemplazar el origen o su copia con los del destino a medias.

La reversa debe restaurar ese origen efectivo cuando exista, sin preferir automáticamente el TOML versionado. Valida las referencias/copia y compatibilidad antes de mutar, y verifica los bytes y el enlace efectivo después. Si falta el origen necesario o no se puede leer, rechaza sin un falso éxito. Mantén formato, retención, etapas y recuperación parcial existentes; no borres eventos ni rebajes marcador.

Extiende la comprobación existente de instalación/reversa para este caso autorizado por AGENTS, con homes y repos desechables. Conserva la prueba de recuperación de una sola instalación. No agregues baterías, pruebas de texto, mutantes ni kit. Ajusta la expectativa de la sonda privada para exigir la restauración y conserva evidencia. No repitas comprobaciones válidas ajenas al cambio.

Actualiza `.motor09/resultado-E.json` con comandos, resultados y límites. No presentes la comprobación de recibos como instalación Windows completa. No implementes F/G ni delegues. No toques `/Users/dn/motor`, homes productivos, servicios, tareas, configuración compartida ni secretos. Nunca SIGCONT. No commit en la rama real, push, PR, merge, instalación/reversa real ni despliegue. Entrega y detente.
