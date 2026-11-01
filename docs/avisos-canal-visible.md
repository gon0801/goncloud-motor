# Un canal de avisos caído se ve

Decisiones de la fase `canal-visible` (`motor/integraciones/canales.py`).

- **Qué cuenta como canal caído.** El transporte falla (no corre, no responde o sale con error). Una salida ilegible o
  sin recibo es un fallo de ese mensaje y no marca el canal. Un canal cuenta como **caído** cuando lleva al menos
  `UMBRAL_CAIDO_S` (10 minutos) fallando desde el primer fallo.
- **Dónde se guarda.** `base/canales/<canal>.json`, escrito al final de cada pasada de `drenar`:
  `fallando_desde`, `ultimo_intento`, `fallos` y `ultimo_error`. Cuando el canal vuelve a responder, el archivo queda con
  `ultimo_exito` y sin `fallando_desde`.
- **Dónde se ve.** `motor director ver <encargo>` agrega `canales_caidos` (solo si hay alguno). La señal a Kuma sale con
  `status=down` y un mensaje `N planes; canal <canal> caído desde <hora>: <último error>`; sin canales caídos sale
  `status=up` como siempre. Kuma avisa por sus propias notificaciones, no por el canal que está caído.
- **No depende del canal caído.** Los dos leen el archivo local y no usan el transporte.
- **El error se limpia antes de guardarse y de mostrarse.** Se quitan direcciones (`http://…`), valores de
  `Bearer`/`Basic`, de `token`, `api_key`, `password`, `clave`, `authorization` o `cookie` tras `=` o `:`, tokens con
  forma `id:secreto` y cualquier cadena opaca de 32 caracteres o más. Queda en una línea y acotado a 200 caracteres,
  porque un error de transporte puede traer la dirección o la clave del bot.
- **De dónde sale la dirección de Kuma (sin variable nueva).** El supervisor de encargos lee `MOTOR_KUMA_URL` del
  `secretos.env` de su propia base (`reg.base`; en la Mini, `~/.motor-encargos/secretos.env`, con permisos 600), como
  hoy. El daemon del modo planes lee el de `~/.motor/secretos.env`: cada proceso, su archivo, y por eso los dos procesos no se pisan en
  el monitor. El supervisor **nunca** lee `~/.motor` ni hay respaldo allí. No se creó ninguna dirección ni secreto nuevo.
  Ojo con `ops/senal-kuma.sh`: da de alta los monitores `mini` y `pc` y escribe `MOTOR_KUMA_URL` en `~/.motor` (el
  home del motor de planes). Esa es la dirección del daemon, no la del supervisor; la del supervisor es la que ya
  tiene puesta el operador en el `secretos.env` de su base, y este cambio no la crea ni la mueve.
- **"Canal caído" usa esa misma señal.** Con un canal caído, la señal de vida sale con `status=down` y el motivo
  (`N planes; canal <canal> caído desde <hora>: <último error>`), por la misma dirección; sin canal caído, `status=up`.
- **Sin `MOTOR_KUMA_URL` en la base** (o con el archivo ilegible o con permisos abiertos), el supervisor no manda señal:
  lo dice en el log al arrancar ("sin MOTOR_KUMA_URL en el secretos.env de la base, el supervisor de encargos no manda
  señal a Kuma") y, mientras haya un canal caído, lo escribe en el log una vez por hora. `motor director ver` lo
  muestra siempre.
- **Un estado dañado no rompe nada.** Un archivo de estado ilegible se ignora y un `fallos` que no es número cuenta como 0.
- **Límite.** El estado se actualiza cuando hay un aviso que enviar. Si un canal falla y no quedan avisos pendientes,
  sigue figurando caído hasta el siguiente envío; `ultimo_intento` dice cuándo fue la última prueba.
