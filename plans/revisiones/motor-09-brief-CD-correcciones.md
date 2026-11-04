# MOTOR 09: corregir C+D y detenerse

Usa `pstack:poteto-mode`. Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`, base `1b37d63`. Conserva el diff de B y C+D. No reinicies investigación general ni rehagas lo acreditado. Lee `plans/revisiones/motor-09-fase-CD.md` y el diseño aprobado.

Corrige los tres bloqueantes juntos:

1. **Propiedad del trabajador Windows.** Ejecuta `python3 -B .motor09/revision-CD/lead/separado-job.py`. Un trabajador con fallback sin BREAKAWAY ejecuta y muere al salir el daemon dentro de su Job KILL_ON_JOB_CLOSE. Garantiza la supervivencia requerida o rechaza el entorno antes de permitir ejecutar. No declares separación por haber creado el proceso. No agregues broker ni elimines las demás banderas. Extiende la regresión ya prevista del incidente para cubrir esta combinación; comprobar contenido y supervivencia en contextos distintos no basta.
2. **Fallo de creación del agente.** Ejecuta `python3 -B .motor09/revision-CD/lead/creacion-agente.py`. El wrapper sí arranca, el agente falla al crearse y eso termina como sin_resultado, pausando el modelo A y eligiendo B. Distingue también esta falla confirmada de una falla de modelo: causa saneada y estado operativo visible, sin nuevo consumo ni relevo automático. No borres historial ni conviertas el error en espera muda. Conserva conciliación por token para los casos realmente ambiguos.
3. **Token en error de transporte.** Ejecuta `python3 -B .motor09/revision-CD/lead/transporte-invalido.py`. Una URL sin esquema hace que ValueError llegue al log con el token. Valida/clasifica en la frontera con causa visible y sin secretos, incluidas preparación y recarga del notifier. Ningún error debe registrar la URL del bot con token. No amplíes una captura genérica ocultando fallos de almacenamiento. Usa solo URL local/inválida y token ficticio para comprobarlo.

Las sondas del lead afirman las fallas para reproducirlas: adapta sus expectativas al arreglo, conserva versiones y resultados en evidencia privada. No agregues nuevas baterías, pruebas de texto, mutantes, pytest ni kit. Respeta AGENTS y reutiliza evidencia anterior no invalidada. Comprueba las rutas afectadas al final y conserva los casos de B.

Mantén explícito que la entrega conserva el comportamiento de no enviar Telegram nuevo por aceptación; no añadas esa notificación por suposición. Los avisos de rechazo y de progreso siguen bajo el contrato de recuperación de D.

Actualiza `.motor09/resultado-CD.json` con archivos, decisiones, comandos reproducibles y resultados separados por plataforma. No acredites el servicio real o apertura de Job entre sesiones con una sesión SSH. Si un entorno no cumple propiedad, debe quedar rechazado con causa; no se habilita producción para intentar aprobarlo.

No implementes E–G, no delegues ni lances otros modelos. No toques `/Users/dn/motor`, homes productivos, configuración compartida, tareas, servicios ni secretos. Nunca SIGCONT. No commit, push, PR, merge, instalación, reversa ni despliegue. Entrega las tres correcciones en una sola ronda y detente para revisión.
