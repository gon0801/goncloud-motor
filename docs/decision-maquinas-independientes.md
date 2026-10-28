# Decisión: tres máquinas independientes

Estado: aceptada por David el 2026-10-27 en esta conversación
(«sí exacto confirmo»).

## La regla

El sistema es uno y está copiado en tres máquinas. Cada copia trabaja
sola y ninguna depende de otra para funcionar.

- Cada máquina tiene su propia libreta de encargos. La libreta de una
  máquina guarda solo los encargos de esa máquina.
- Cada máquina trabaja en su propia máquina. Si un encargo es de la
  PC y la PC se apaga, el encargo se pausa y sigue cuando la PC
  vuelva. No se pasa solo a otra máquina.
- Nadie continúa ni termina el encargo de otra máquina.
- Una máquina puede entrar a otra por el acceso remoto cuando
  necesita información o cuando un encargo suyo necesita modificar o
  instalar algo ahí. El encargo sigue siendo de la máquina que lo
  pidió. Si la otra está apagada, esa parte espera.
- Las demás pueden ver los encargos de una máquina solo entrando a
  esa máquina a preguntar. Nadie se trae una copia de la libreta a la
  suya.
- Lo compartido es lo de la empresa: el código, las herramientas y
  las reglas de modelos. Eso ya se mantiene igual en las tres.
- Lo de cada máquina se queda en su máquina: sus cuentas, su
  navegador iniciado y sus archivos locales.

## Qué cambia frente a lo desplegado en fase 5

La autoridad única de encargos en Mini contradice esta regla. Es una
dependencia y debe retirarse.

- Mini conserva su libreta con su historial. Ese historial nació ahí
  y en la migración de la fase 5, y no se borra.
- Mac y PC necesitan su propio servicio de encargos local, con su
  propia libreta, instalado como se instaló en Mini.
- Cada director entrega a la libreta de su propia máquina. Hermi a la
  de PC. Claw a la de Mini. El director de la Mac a la de la Mac.
- La Mac ya no consulta a Mini como autoridad. Consulta su propia
  libreta. Entrar a Mini queda solo para buscar información o hacer
  un cambio pedido por un encargo propio.
- La política común de herramientas y modelos no cambia. Esa copia
  automática es lo compartido de la empresa y sigue igual.

## Terminado significa

- Las tres máquinas tienen su servicio de encargos local corriendo y
  levantando solo tras reinicio.
- Con las otras dos apagadas o sin responder, cada máquina recibe un
  encargo de su director, lo guarda en su libreta y lo puede consultar
  y parar.
- Un encargo de una máquina apagada queda pausado en su libreta y
  continúa al volver. Ninguna otra máquina lo toma.
- La reversa de lo desplegado se conserva. Apagar la autoridad de
  Mini no borra su historial ni reactiva escritores viejos sobre
  encargos que no son suyos.
