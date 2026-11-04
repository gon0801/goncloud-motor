# Brief a Grok: fase B de MOTOR 09

Usa la skill `poteto-mode` en `/Users/dn/.agents/skills/poteto-mode/SKILL.md`. Eres el implementador. El lead revisará tu entrega. Implementa solamente B y detente; David coordina el envío manual de la siguiente fase.

Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`, base `1b37d63dfbc7157ffa42b5680505ed8e8962f4a1`. No uses `/Users/dn/motor`, que es la instalación. La fase A ya está aprobada; no repitas resguardos ni investigación general. No hay código previo de B que rehacer.

Lee `plans/motor-09.md`, su fase B, y `plans/revisiones/motor-09-diseno.md`. El diseño ya tiene revisión cruzada aprobada de Astra. Lee solo el código pertinente para implementarlo, sin reiniciar how, why, arena ni brainstorming.

Entrega estos comportamientos:

1. Retención durable del home, leída antes de configuración, con generación e identidad de solicitud. Camino local mínimo de retención, estado y recibos aun con configuración inválida.
2. Pausa, reanudación y cancelación por plan conforme al contrato. Registrar primero; detener árboles propios y confirmar después. El operador no gasta intentos. Nota o reentrega no reactivan un plan cancelado. Efecto remoto incierto no se repite.
3. Formato 3 con elevación real del marcador existente y protección de todos los escritores, incluido `aparte`. Conservar eventos, resultados e intenciones anteriores.
4. `Store.append_batch` para controles y recibos atómicos, reutilizable por D. No implementes todavía admisión y avisos de D.

Puedes modificar los archivos de B listados en el plan y actualizar su contrato en `docs/diseno.md`. Conserva Store, fold, decide y Effects; no agregues otro motor ni un framework universal.

Comprueba el comportamiento en un home desechable, con procesos inocuos y verificaciones existentes. Demuestra que la retención persiste al reiniciar, una liberación vieja no la borra, pausar/cancelar impide relanzamientos y el trabajo permanece. No ejecutes producción, no envíes Telegram real ni presentes skips en Mac como pruebas Windows. Respeta AGENTS: sin baterías, pruebas de texto, mutantes, pytest ni kit nuevo.

No avances a C, D, E, F o G. No toques servicios, tareas, procesos, secretos o homes productivos. Nunca `SIGCONT`. No hagas push, PR, merge, instalación ni despliegue. No delegues esta implementación ni lances otros modelos. Conserva cualquier trabajo local.

Entrega el diff y `.motor09/resultado-B.json` con archivos cambiados, decisiones, comandos, resultados y lo no verificado. No declares B comprobada solo porque compila. Termina después de esa entrega para que el lead revise.
