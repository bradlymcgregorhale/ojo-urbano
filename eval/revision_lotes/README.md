# Revisión de fotos por lote

La página muestra una foto por vez y la respuesta original de la API en modo Completo. Permite aprobar las sugerencias o guardar una corrección por categoría, material y ámbito de la escena. El trabajo corresponde al ticket #42; la regla de interiores en producción se sigue por separado en #43.

Para revisar, abrí `revision.html` dentro de la carpeta de entrega. Si el resultado es correcto y la situación está en la calle, usá "Aprobar y confirmar vía pública". Si hay diferencias, editá las sugerencias y guardá la corrección. Una respuesta parcial necesita una confirmación adicional de que revisaste los campos faltantes.

El ámbito describe dónde está el material o el problema. Una foto tomada desde una ventana puede mostrar un problema en la vía pública. El botón "Rechazar por interior y seguir" guarda el rechazo y avanza, sin revisar materiales, categorías ni respuestas parciales. Al volver, muestra el pedido de otra foto en vía pública y oculta las sugerencias. La exportación marca `exclusion=interior` y deja categorías y materiales en `sin_revisar`, para que no se usen como etiquetas negativas. Las revisiones anteriores siguen cargando sin alterar sus decisiones. Cambiar una marca de interior a vía pública vuelve a abrir la revisión de sugerencias sin aprobarlas automáticamente. La respuesta original de la API queda intacta.

Los botones "Falta contexto" y "Mala calidad" piden otra foto y avanzan, sin marcarla como interior ni adjudicar categorías o materiales. La exportación conserva `revision_tecnica=contexto` o `revision_tecnica=calidad`. "Volver a revisar la clasificación" abre una revisión nueva de las sugerencias y mantiene el historial.

La selección de prioridad permite señalar el problema principal entre las categorías confirmadas en la revisión. Guardarla no aprueba la foto ni cambia categorías. Si después quitás la confirmación de ese problema, se elimina su prioridad para evitar una contradicción. Estos datos se conservan en la exportación; todavía no se puntúan automáticamente en el comparador.

Las decisiones se guardan en el navegador. Exportá la revisión al terminar cada tanda; ese JSON permite recuperar el trabajo o llevarlo a otro dispositivo. Cambiar de navegador o limpiar sus datos puede borrar la copia local. "Actualizar resultados" carga las respuestas que vaya completando la cola sin borrar las revisiones.

## Generación y procesamiento

`generar.py BASE` lee `entrega/manifest.json`, los archivos `analisis/H####-alto.json` y `analisis/estado.json`. Genera `entrega/revision.html`. El manifiesto de entrega contiene solamente identificadores neutros, rutas relativas y huellas de las fotos. Los identificadores de reclamos y las ubicaciones quedan en el manifiesto privado, fuera de la entrega.

`node procesar.cjs BASE` requiere un `analisis/plan.json` con autorización explícita para el lote, presupuesto, reserva por foto, huella del manifiesto y rutas de Python y Puppeteer. Usa la API pública de Buenos Vecinos con `modo=alto`, `verificar=1`, contexto vacío y nombre de archivo neutro. El modelo local sigue formando parte de esa API. No se ejecuta una clasificación visual adicional para preparar este lote.

La cola guarda cada respuesta, limita la frecuencia y consulta el identificador de un trabajo pendiente al reanudarse. Un envío sin identificador, consumo incompleto o cambio de versión detiene los envíos. Esos casos requieren conciliar el estado y el consumo antes de continuar. Nunca borres el estado ni los archivos de respuesta para forzar un reintento.

El presupuesto se controla antes de cada foto con el gasto informado más las reservas. La reserva no es un límite de facturación impuesto por OpenRouter: debe alcanzar para una llamada completa y sus verificaciones. Una pausa por presupuesto requiere revisar el saldo y la autorización antes de aumentar el tope.

## Uso de la revisión para mejorar el sistema

La exportación conserva la respuesta original y las decisiones humanas por separado, vinculadas por una huella. Aprobar o corregir no entrena ni cambia automáticamente la API.

El lote de 1.000 fotos tiene una partición fija: 800 para desarrollar mejoras y 200 reservadas para evaluarlas. Las etiquetas de las 200 reservadas no deben usarse para ajustar ejemplos, rúbricas, reglas o modelos. Hay que medir el resultado de los cambios sobre esa partición después de cerrar la versión candidata, incluyendo omisiones, confirmaciones incorrectas, interiores y costo.

La categoría administrativa del reclamo sirve para armar una muestra variada; no es una etiqueta visual verdadera. La revisión muestra las sugerencias antes de pedir una decisión humana, por lo que tampoco debe presentarse como una evaluación ciega. El porcentaje de aprobaciones no equivale por sí solo a exactitud del modelo.

## Pruebas

`pruebas_interfaz.cjs` usa imágenes y respuestas sintéticas para verificar aprobación, corrección de interiores, guardado, importación, exportación y celular. Requiere `OJO_PUPPETEER` y `OJO_PYTHON` con las rutas locales.

`pruebas_cola.cjs` simula la API sin red ni gasto. Verifica presupuesto, autorización, recuperación de trabajos, envíos inciertos, cambios de versión, respuestas sin asiento y fotos alteradas. Usa `OJO_PYTHON` para generar el informe de prueba.

## Registro privado de regresiones (#51)

`regresiones.py` conserva las correcciones de la conversación y las exportaciones v2 de esta página junto con los JSON originales. Usa objetos identificados por SHA-256: importar de nuevo la misma fuente no duplica sus bytes. Cada registro tiene una huella y no se sobreescribe. No guarda fotos adicionales, conserva sus huellas y la carpeta de origen; mantené también la carpeta de fotos. Todo el registro debe quedar dentro de `eval/vision/private`, fuera de git.

```sh
python3 eval/revision_lotes/regresiones.py crear \
  --base eval/vision/private/higiene-1000-20260912 \
  --registro eval/vision/private/regresiones-humanas-51 \
  --revision /ruta/a/la/exportacion-v2.json
```

`--revision` puede repetirse u omitirse para importar solamente las correcciones locales de la conversación. La herramienta no lee el navegador ni recupera decisiones todavía no exportadas. Este adaptador acepta el formato v2 de la revisión por lote; no asume compatibilidad con otros formatos de revisiones históricas. Las fuentes originales, notas, materiales y observaciones se conservan. Solo se puntúan categorías adjudicadas y el rechazo de interiores; prioridad, materiales y prosa todavía no tienen un evaluador equivalente.

El comando devuelve la ruta `registro-<huella>.json`. Para comparar respuestas candidatas ya obtenidas, en archivos `H####-alto.json` con el mismo formato que el lote:

```sh
python3 eval/revision_lotes/regresiones.py comparar \
  --registro /ruta/al/registro-<huella>.json \
  --candidata /ruta/a/respuestas-candidatas \
  --informe /ruta/nueva/para/el-informe
```

Se generan un HTML y un JSON con aciertos conservados, regresiones, correcciones y errores persistentes. Sin `--candidata`, mide la referencia contra las etiquetas humanas y no aprueba una comparación entre versiones. La comparación no llama a modelos ni consulta la red. Una ausencia equivale a `no` solo en una respuesta completa y para una categoría revisada expresamente. En respuestas parciales se puntúan los hallazgos confirmados o posibles que aparecen; las ausencias se registran como cobertura faltante. Un estado inválido o sin verificación no se puntúa. Las categorías posibles se distinguen de las confirmadas. `duda` y `sin_revisar` no se puntúan. Las revisiones contradictorias bloquean la comparación, sin elegir una por fecha. Las claves humanas deben existir en el catálogo.

En los interiores no se crean negativos de cada categoría. Puntuar `interior_rechazado` exige que la respuesta informe explícitamente el rechazo por interior y no conserve reclamos, posibles ni elementos. Una respuesta vacía sin ese motivo no acredita el rechazo por interior.

Por defecto se usa únicamente desarrollo. `--particion evaluacion_reservada` sirve para la evaluación final de una candidata ya fijada; no se usa para ajustar el prompt. La comparación exige mismo modo e imagen y una versión candidata uniforme. Hoy el adaptador del lote es para modo alto. Un contexto omitido en ambos archivos se interpreta como vacío, siguiendo el contrato de la cola; el productor de respuestas debe conservar el contexto real y su procedencia, la herramienta no puede auditar una omisión falsa.

Salida 0 significa que una candidata de versión nueva conservó los aciertos previos evaluados sin falta de cobertura ni conflictos. Salida 1 señala regresiones, datos faltantes, conflictos, ausencia de aciertos previos para proteger, comparación solo de referencia, archivos idénticos o versión sin cambios. Los campos `identicas`, `version_sin_cambio` y `versiones_referencia` distinguen estas condiciones. Salida 2 señala archivos inválidos. Ninguna salida aprueba por sí sola un despliegue ni demuestra exactitud general. Un registro compuesto solo por correcciones de errores necesita también aprobaciones humanas de casos correctos para probar preservación.

### Reproducción acotada de poda (#45)

```sh
.venv/bin/python eval/revision_lotes/reproducir_poda.py \
  /ruta/H0001-alto.json /ruta/H0002-alto.json \
  --salida /ruta/nueva/reproduccion.json
```

Reutiliza las respuestas de revisión guardadas para ejecutar el consenso de alcance y la política real de descarte, con la red bloqueada y sin pesos. La entrada previa al descarte es un escenario mínimo reconstruido: el JSON público no conserva todo el estado interno, por lo que esto no reproduce la API completa. El informe conserva huellas del original y del código. Devuelve 1 mientras esos casos sigan perdiendo la poda; ese fallo esperado muestra una corrección pendiente, no un test exitoso de exactitud. Para comprobar si cambia lo que un prompt ve en la foto siguen haciendo falta inferencias nuevas, en una evaluación separada.

Pruebas sintéticas del registro:

```sh
python3 -m unittest discover -s eval/revision_lotes -p 'test_regresiones.py' -q
```

## Reanálisis individual (#58)

`reanalizar.cjs` mantiene un servicio en `127.0.0.1`. Usa el navegador para enviar una foto a la API pública en modo Completo, con nombre neutro y sin la revisión humana en el pedido. Requiere `analisis/reanalisis-config.json` privado con `url`, `puppeteer`, `puerto`, un `token` aleatorio de al menos 48 caracteres hexadecimales, `modo_version` comprobada después del despliegue y `tope_usd` mayor a cero y menor a cinco.

El botón del informe conserva el original y abre otra página para comparar y revisar la nueva respuesta. Cada intento usa otro identificador de conjunto para que sus decisiones no reemplacen las anteriores. La exportación incluye las huellas de ambas respuestas y de la foto. La aprobación no cambia la API ni entrena automáticamente.

Antes de enviar, el servicio comprueba las huellas, la versión desplegada, el presupuesto y que no haya otro intento pendiente. Reserva un dólar por pedido, cuenta el costo conocido y detiene nuevos envíos si falta información de consumo. Una respuesta perdida no provoca otro POST. Un trabajo con identificador puede recuperarse con consultas al reiniciar. Si no hay identificador, hay que conciliarlo antes de continuar. El importe reservado no es un cargo observado.

Una conciliación externa puede registrar una cota documentada en `reserva_acotada_usd` cuando se conoce el único pedido fallido, sus límites y las tarifas aplicables. Esa reserva sigue descontándose del presupuesto y se muestra separada del consumo conocido. No se libera por el mero paso del tiempo ni convierte un costo desconocido en cero.

La configuración y los intentos son privados. No publiques el token ni expongas este servicio mediante un túnel. El reanálisis no modifica `analisis/estado.json` ni reanuda la cola de fotos. Después de otro despliegue, actualizá la versión de la configuración y regenerá el HTML. Si se abrió desde otro equipo, el servicio loopback de este equipo no estará disponible.

Pruebas sin llamadas pagas:

```sh
node eval/revision_lotes/pruebas_reanalizar.cjs
```

`pruebas_interfaz.cjs` verifica el guardado, importación, rechazo por interior y pedidos de fotos complementarias en un navegador aislado. Nunca uses los guardados reales del usuario como datos de prueba.
