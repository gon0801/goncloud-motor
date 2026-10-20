---
name: motor
description: Cómo repartir el trabajo que pide David. Lo que solo lee (investigar, contestar, revisar) lo hacen tus agentes; todo lo que cambie un repo, una configuración o producción se convierte en un plan y se lo entregas al motor de tu máquina. Misma skill para claw (OpenClaw, Mini) y Hermi (Hermes, PC).
---

# Repartir trabajo: tus agentes o el motor

## La regla (una sola pregunta)

**¿El pedido termina cambiando algo?** Un archivo de un repo, una configuración, un despliegue
o una escritura en producción, aunque sea una línea.

- **No** (investigar, buscar, contestar, revisar, resumir): lo repartes a tus agentes de
  lectura (scout, reviewer, verifier) como hoy. Sin plan.
- **Sí**: escribes un plan y se lo entregas al motor. Tú no lo implementas, no lo mergeas y
  no despliegas. Ante la duda, plan.

**Antes de entregar, ¿David pidió implementar?** Si te dio un plan o te pidió hacer el cambio,
entrégalo directo. Si solo pidió investigar y de ahí salió que hay algo que cambiar, NO lo
entregues: cuéntale en dos o tres frases qué encontraste y qué cambiarías, y pregúntale
"¿quieres que lo implemente?". Solo con su sí lo entregas.

Un pedido mixto se parte: lo de leer lo haces ya; lo de cambiar va al plan.

## Cómo entregar un plan

1. Escribe el plan con el formato de abajo en un archivo temporal.
2. `motor valida <plan.md> --home <home>`: si dice que no está listo, corrige lo que diga y repite.
3. `motor entrega <plan.md> --home <home> --json --por <tu nombre>`.
   - `tomado`: dile a David en una frase qué va a pasar ("Lo convertí en plan y ya está
     corriendo; te aviso cuando esté probado").
   - `encolado`: el motor está reiniciando; lo toma solo. Díselo igual.
   - `rechazado`: lee `errores`, corrige y entrega otra vez.
4. Ya no tienes que darle seguimiento: el motor le avisa a David por tu mismo bot de Telegram
   (cada aviso empieza con el nombre de la máquina) cuando entrega, si necesita su mano (login,
   código, captcha, pago) o si se atora.

## Cuando David pregunta "¿cómo va?"

`motor estado --home <home> --json` y contesta en una o dos frases de usuario.

## Cuando David dice "ya quedó" (un paso que solo él podía hacer)

`motor hecho <plan> <bloque> --home <home>`.

## Si David pide modelos ("con DeepSeek como revisor y Qwen como implementer")

Pon en el encabezado `implementa: qwen` y `revisa: deepseek`. Cualquier combinación vale, con
varios separados por coma en orden de relevo. Se usan solo los que pidió: si no tienen cuota, el
plan espera a que vuelvan, no se cambia a otro. Si quiere respaldo, que lo nombre
(`revisa: deepseek, opus`). Si `motor valida` dice "no conozco el modelo X", dile a David cuáles
hay. Sin pedido, no pongas esas líneas: manda la tabla.

## Formato del plan

```
# <título que entienda David>
autopiloto: motor
plan: <id-corto-sin-espacios>
repo: <url del repo>
implementa: <modelo>[, <modelo>...]   # solo si David lo pidió; si no, se omite y se usa la tabla
revisa: <modelo>[, <modelo>...]       # ídem

## Bloque A — <qué entrega, en palabras de David>
lane: gate                       # gate = código; release = código que se despliega; fast = solo documentos
depende: -                       # o los ids de bloques que van antes
aviso: <frase llana para cuando esté listo>
prueba-real: <comando de solo lectura que demuestra que funciona con datos reales>
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | <qué hacer, concreto> | <comando que sale 0 si quedó bien> |
```

Reglas del formato: cada bloque de código trae al menos un `Acepta` y una `prueba-real`; un
paso que solo David puede hacer va en un bloque propio con `lane: fast`,
`humano: login|2fa|captcha|pago <qué hacer>` y, si se puede detectar solo, `sonda-humano: <comando>`.
Nada más le pide nada a David.
