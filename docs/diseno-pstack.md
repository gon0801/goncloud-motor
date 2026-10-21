# El motor obliga a trabajar con pstack y poteto-mode

Fecha: 2026-10-21. Pedido de David: que todo agente que lance el motor (implementar y revisar, en las
tres máquinas, con cualquier modelo) trabaje con pstack/poteto-mode, y que el motor lo haga cumplir.
Arena de tres candidatos en `docs/arena/pstack/` (c1 Opus, c2 Fable, c3 Sonnet) y hechos medidos en
`docs/arena/pstack/hechos.md`.

## Lo que se encontró

pstack solo está en la Mac, y ni ahí lo usan los agentes del motor: Sonnet y Kimi no cargaron
poteto-mode teniéndolo, y las sesiones de opencode lanzadas por el motor tienen cero llamadas. En la
Mini y la PC no hay pstack. Ninguna ruta de `rutas.toml` pasa nada de pstack.

## Decisión de síntesis

Base **c1**: una copia fija de pstack dentro de la conf compartida, una exigencia por (papel, carril)
que escribe el encargo y juzga el registro real del agente (la "huella"), y una tabla de formatos por
CLI. Injertos:

- De **c2**: comprobar resultados, no solo la carga de la skill. (a) En un arreglo de bug, el agente
  entrega una prueba que falla en la base y pasa en su punta; el motor la corre él mismo. (b) El motor
  corre el `repro` de cada bloqueante del revisor; si no reproduce, baja a nota.
- De **c3**: si el registro de un CLI no se puede leer, la entrega pasa con aviso (una vez por CLI y
  día) en vez de atorar el plan; `min_subagentes` arranca en 0 y se sube después de medir.

Rechazado: que el motor dirija los pasos de poteto-mode uno por uno (lo central de c2): multiplica
por 2–3 las corridas por bloque y mete el riesgo del traspaso entre delegados. Queda como idea para
pedirla en un plan grande.

## Decisiones de David (2026-10-21)

1. El motor exige desde el primer día (sin modo "observar"). Para no rechazar trabajo bueno, antes de
   encender la exigencia se mide el registro real de cada CLI en cada máquina y queda como prueba.
2. Corrección de David el mismo día: poteto-mode pide paneles de modelos distintos (interrogate, arena,
   swarm, how, why: "una segunda opinión es el mismo encargo con otro modelo"), así que un agente SÍ
   puede llamar a otros CLIs de modelos dentro de su encargo. Va contado: cada llamada pasa por un shim
   del motor que la registra, ocupa un lugar de los topes (4 por máquina, por cuenta) y respeta las
   pausas de cuota; la llamada anidada no puede llamar a otra (profundidad 1).
3. El revisor trabaja con poteto-mode más `interrogate` en carriles de código; en fast solo poteto-mode.

## Forma

- `goncloud-motor-conf/pstack/`: copia exacta de cursor/plugins en un commit fijo, generada por
  `scripts/trae_pstack.py <sha>` (`--comprueba` la regenera y compara byte a byte). Al lado,
  `pstack-motor/`: `motor.md` (lo que manda sobre poteto-mode dentro del motor: el agente no abre ni
  mergea PRs, solo commit y push; no hay pausas ni preguntas; desplegar es del motor) y un mapa por CLI.
- `pstack.toml`: el commit fijo y la tabla `[exigencia]` por carril × papel (skill, playbook,
  subagentes mínimos, skills extra como `interrogate` para revisar en código).
- `motor/pstack.py` (puro): `huella(log, formato)`, `juzga(huella, exigencia)`, `puerta(...)` (corta
  en vivo si a la acción N no cargó poteto-mode) e `instrucciones(exigencia, cli)` (la sección "Cómo
  trabajas" del encargo). La tabla `FORMATOS` dice cómo se ve cada acción en el registro de cada CLI,
  con fixtures reales medidos.
- `effects`: materializa la copia por id de árbol (inmutable mientras dura un intento), pasa
  `{pstack}` y `{sesion}` a las rutas (`--plugin-dir`, `--skills-dir`, sesión fija para leer el
  transcript correcto), escribe el encargo, y en `collect` juzga la huella: si falta algo, kind
  `sin_pstack` con la lista en llano; cuenta como falla del modelo (relevo a las 2).
- `runwrap`: aplica la puerta en vivo; pone en el PATH del agente shims de los CLIs de modelos que
  dejan pasar la llamada contada (lugar en los topes, cuota de la cuenta, profundidad 1).

## Plan

`plans/pstack-01.md` (A conf, B motor, C medición real por CLI y máquina, D instalar, E encender).
