Estado posterior: [E cerrada tras corrección del lead](motor-09-fase-E-cierre.md).

Estado posterior: ver [segunda revisión de E](motor-09-fase-E-ronda-2.md). Los casos generales anteriores pasan; queda la restauración del TOML local efectivo.

# MOTOR 09: revisión de E

**REQUEST_CHANGES. Tres fallos de comportamiento reproducidos; E no aprobada. No corresponde PR ni despliegue.**

Revisión directa del lead sobre el diff local en `recuperacion/motor-09`, base `1b37d63`, sin commit. No se modificó implementación. Se usaron repos, destinos y homes desechables; la única consulta nativa a PC fue un comando inerte que no lee ni modifica la instalación. Las huellas revisadas están en `.motor09/revision-E/lead/huellas.json`.

## 1. Los comandos nuevos de PC no son ejecutables en cmd.exe

Ubicación: `ops/instala-todo.sh:145`, `:154`, `:169`, `:438`, `:442`, y lecturas/borrado equivalentes de `ops/vuelve-a-la-anterior.sh`.

Se envían comandos que empiezan por `MOTOR09_CONF_HEAD`, `MOTOR09_LEE=...` o `MOTOR09_NOMBRE=... MOTOR09_B64=...`. CMD intenta ejecutar esos textos como programas; las asignaciones POSIX no se aplican así en Windows. El ssh falso del test reconoce esos prefijos y simula éxito, por lo que no detecta el defecto de la implementación.

Reproducción inerte en PC:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 pc "MOTOR09_CONF_HEAD if exist %TEMP% echo motor09-diagnostico"
```

Salida 1: `'MOTOR09_CONF_HEAD' is not recognized as an internal or external command`. No instala, no lee secretos, no cambia tareas ni archivos.

En consecuencia las lecturas de recibos y su escritura/borrado no funcionan nativamente. Además, `valida_pc` clona en `%USERPROFILE%\motor` y `motor-conf` antes de aceptar la configuración si no existen; el propio resultado de entrega reconoce esta mutación. La validación debe usar staging temporal y no cambiar un destino antes de aceptarlo. No basta mejorar el simulador para que reconozca más comandos: el mecanismo real tiene que funcionar.

## 2. Cambiar solo configuración guarda un origen de código equivocado

Ubicación: `ops/instala-todo.sh:565–580`.

Cuando el código coincide con el destino pero cambia la configuración, no es un no-op. Sin embargo se toma `anterior.codigo` del último recibo, en vez del código que realmente está instalado al empezar esa nueva operación. Eso mezcla el código de hace dos instalaciones con la configuración inmediatamente anterior.

El lead ejecutó el instalador real con repos locales desechables y un transporte que solo ejecuta Bash bajo el HOME falso. Caso: estado A/F1 → instalar B/F1 → instalar B/F2.

```sh
python3 -B .motor09/revision-E/lead/pareja.py
```

Resultado: primera instalación conserva A correctamente; en la segunda **guarda A/F1 como origen, cuando el origen real es B/F1**. Al revertir termina en A, no en B. El candidato del fixture es mínimo e inocuo: demuestra la selección de origen del shell, no acredita instalación del motor completo.

La regla de conservar el anterior aplica al mismo destino completo y a reintentar el mismo intento parcial. Un cambio de configuración crea otra pareja y debe guardar la pareja efectiva al comenzar.

## 3. La reversa no restaura la configuración efectiva de la máquina

Ubicación: `ops/vuelve-a-la-anterior.sh:118–127` y `:218–224`.

La reversa cambia HEAD de código y conf, pero no restaura `~/.motor/maquina.toml`, su enlace efectivo ni usa la copia privada creada en instalación. Puede informar éxito con una configuración de máquina distinta a la anterior. Tampoco prepara/valida la pareja completa de regreso antes del primer checkout: el cambio de código precede a obtener/comprobar la conf anterior.

La misma reproducción anterior compara los bytes efectivos después de la reversa:

```sh
python3 -B .motor09/revision-E/lead/pareja.py
```

Resultado: `reversa_conf_es_F1=true`, **`maquina_efectiva_restaurada=false`** y `mantenimiento_conservado=true`. El archivo conserva el nombre nuevo mientras el repo está en la configuración anterior. Es independiente de la selección de código del hallazgo 2: aunque la referencia de código fuera correcta, faltaría restaurar el archivo efectivo.

La reversa debe validar todos los componentes antes de mutar, restaurar la configuración efectiva y comprobar el resultado. Si falla a mitad, debe quedar una etapa recuperable y retenida, sin convertir el segundo intento en un no-op por haber cambiado ya el código.

## Comprobación obligatoria que todavía falla

El lead ejecutó el comando del brief:

```sh
python3 -B -m unittest -v tests.test_motor08_efectos.RecuperaEfectosTest.test_caida_tras_lanzar_recupera_resultado_sin_repetir_instalacion
```

Falla con `SystemExit not raised` en la línea 68. El doble de lanzamiento devuelve solo `pid`, mientras que la API actual exige también identidad de inicio para publicar el arranque. No demuestra por sí solo que la recuperación real esté rota, pero tampoco acredita ese criterio de E.

Como diagnóstico, el lead ejecutó en memoria la misma prueba aportando `inicio` y `boot_id` al doble, sin cambiar el archivo ni quitar sus asserts: pasó. Hay que actualizar esa prueba existente al contrato de identidad y conservar la caída posterior al lanzamiento y el conteo de un solo lanzamiento. No saltarla, eliminarla ni inventar otra batería.

## Evidencia y límites

`git diff --check` pasa. El implementador reporta `bash ops/tests/test-instala.sh` verde; no se repitió esa batería antes de corregir los fallos. Sus fixtures usan el HEAD anterior como candidato y simulan PC, por lo que ese verde no acredita instalar el código nuevo de B–E ni los comandos nativos. La corrección debe ejercitar el candidato actual desde un repo de prueba, sin necesitar un commit en la rama real.

No se acreditó instalación/reversa nativa Windows, y el Job del servicio GoncloudMotor/Hermes sigue pendiente. No se pide habilitar servicios productivos para cerrar E. Las comprobaciones nativas deben usar destinos temporales y evidencia conservada.

Siguiente paso manual: [brief de correcciones de E](motor-09-brief-E-correcciones.md). El lead no lanzó otra implementación ni modificó código del producto.
