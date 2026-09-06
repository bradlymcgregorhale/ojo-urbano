# Pruebas de visión con OpenRouter

Esta suite envía fotos reales a los verificadores de producción y compara sus respuestas con categorías revisadas por una persona. Usa las funciones y los prompts actuales de `verificador.py`. `run.py` evalúa lecturas individuales sin cargar el clasificador local. `pipeline.py` evalúa el resultado final, con predicciones locales reales guardadas previamente.

## Comprobación de regresiones del resultado final

Estas comprobaciones no hacen llamadas a OpenRouter:

```sh
.venv/bin/python pruebas.py

PYTHONHASHSEED=0 .venv/bin/python eval/vision/pipeline.py run \
  --baseline eval/vision/private/runs/20260906-preservation-core.json

PYTHONHASHSEED=0 .venv/bin/python eval/vision/pipeline.py run \
  --baseline eval/vision/private/runs/20260906-preservation-green.json

PYTHONHASHSEED=0 .venv/bin/python eval/vision/pipeline.py run \
  --baseline eval/vision/private/runs/20260906-preservation-cardboard.json

PYTHONHASHSEED=0 .venv/bin/python eval/vision/pipeline.py run \
  --baseline eval/vision/private/runs/20260906-preservation-cement.json
```

La primera referencia incluye once fotos históricas y de control. La segunda reproduce las tres lecturas originales del reporte del contenedor verde, incluidas las respuestas contradictorias. Las dos últimas protegen cartón con muebles y sacos de cemento. Una lectura nueva que casualmente acierta no reemplaza la reproducción del fallo reportado.

El runner ejecuta `servidor.procesar()` y `_publica()`: consenso, repreguntas, arbitraje, fusión y elegibilidad de escombros, más el contrato público. Solo sustituye la inferencia local por su resultado registrado y el transporte de OpenRouter por respuestas de la petición exacta. También comprueba invariantes del contrato, claves internas en la descripción y recomendaciones explícitas de reparación/reposición sin una incidencia confirmada. Estas comprobaciones de prosa no certifican cada afirmación de una descripción.

Con `--baseline`, el código de salida es `0` cuando no aparecen discrepancias nuevas, `1` cuando hay una regresión y `2` cuando la evidencia está incompleta o no es comparable. El HTML distingue "Discrepancia conocida" de "Regresión nueva" y muestra las mejoras. El JSON conserva `fail` para toda discrepancia con las etiquetas. No se cambian las etiquetas para obtener un resultado verde. T109 publica un contenedor verde de secos y el somier como voluminoso, con descripción coherente y sin reparación. T008, T050, U022 y V049 conservan sus resultados esperados.

`20260906-validation-final.html` reúne las 14 fotos, todas aprobadas. Es un resumen para revisión; para replay se usan los cuatro archivos anteriores. Se validaron 648 checks y 14 cargas en el navegador local. Esta última corrección y sus pruebas dirigidas costaron US$0.024243, para un total de desarrollo de US$0.144117. Los replays finales y las cargas de navegador no hicieron llamadas pagas. La validación de cargas en producción se registra por separado.

La revisión de pesos usa `local_training.py` para conservar las puntuaciones originales sobre fotos de referencia mientras incorpora las correcciones revisadas. Cambia los dos cabezales de escombros y los tres de tipo de contenedor, manteniendo los demás cabezales, el scaler y la gravedad. El ajuste reproduce exactamente sus coeficientes. El clasificador auxiliar para materiales mixtos usa todas las etiquetas de `photo_tags`; usar sólo la etiqueta principal había omitido 106 positivos secundarios en un candidato anterior. Ese candidato quedó descartado y archivado.

No aparecieron errores nuevos frente a los pesos originales en 4267 registros de material ni en 2610 fotos etiquetadas de contenedores. Son comprobaciones retrospectivas, no una medición independiente de precisión. Las 31 fotos de control de septiembre, excluidas del ajuste, conservaron sus resultados de material: 31 correctas al umbral de 0.5. Tampoco son 31 validaciones completas de la API.

La carga en producción agregó una variante: un verificador omite el tipo de contenedor, mientras los otros dos coinciden en húmedos. Esa omisión permite abrir la misma revisión dirigida si las tres lecturas terminaron correctamente; no cambia los requisitos para confirmar verde. La variante lleva el total a 649 checks y conserva los 14 replays sin regresiones ni llamadas pagas.

T109 está incluido como corrección de entrenamiento y como regresión del pipeline, no como prueba independiente de generalización. Un puntaje local fuerte sólo abre la revisión dirigida: hacen falta dos lecturas explícitas de verde y comprobar que no haya otro contenedor húmedo separado. Los controles de contenedores negros, grises y escenas con dos tipos conservan sus resultados. Los datos, scripts reproducibles, costos y auditorías están en `private/experiments/20260906-night-container/`. Las referencias anteriores se conservan para revisar el cambio de pesos de forma explícita.

El replay sin red fija las respuestas históricas guardadas dentro de la referencia, aunque haya vencido la caché normal. Esto prueba cambios de código con evidencia constante. Un cambio de prompt que genera una petición distinta necesita nueva evidencia. Un cambio de pesos, extracción de características o dependencias invalida la predicción local; la comparación también detecta cambios de foto, etiquetas, contexto o lectura local.

Para capturar nuevas predicciones locales y completar peticiones pendientes:

```sh
PYTHONHASHSEED=0 .venv/bin/python eval/vision/pipeline.py local --ids r4-T050 r5-U003
PYTHONHASHSEED=0 .venv/bin/python eval/vision/pipeline.py run --ids r4-T050 r5-U003 --live --budget 0.50
```

`local` carga los pesos reales una sola vez, toma el candado sin terminar otro servidor y no llama a OpenRouter. El modo `run` nunca utiliza un clasificador ficticio para suplir datos faltantes. Por defecto exige las primeras lecturas ya guardadas; `--allow-initial-live` autoriza capturarlas si faltan. Las llamadas del pipeline se serializan para respetar la reserva de gasto. Una respuesta con JSON válido pero esquema inválido se registra como incompleta y se retira de la caché.

Antes de aceptar una nueva referencia, comparar el resultado con la anterior y revisar las diferencias de categorías y descripción. Conservar la referencia posterior a una corrección: volver a una referencia anterior permitiría que reapareciese un fallo ya corregido.

La colección reúne 834 combinaciones de foto y contexto, con 828 fotos: 785 revisiones de las rondas históricas, 40 revisiones de escombros de septiembre, seis variantes con contexto, el contenedor verde TC_03772 y los dos casos de cartón/muebles y sacos de cemento. Hay 86 casos difíciles y una selección habitual de 12, más los dos reportes nuevos. Las fotos, etiquetas individuales, respuestas y reportes quedan en `private/`, excluido de Git.

## Qué ejecutar cuando cambia algo

| Cambio | Comprobación |
| --- | --- |
| Lógica de consenso, filtros o presentación | `pruebas.py`, sin API. Los tests de esta carpeta comprueban también caché, presupuesto y puntuación sin API. |
| Prompt de identificación de contenedores | Fotos de contenedores con `--live`; se reutilizan únicamente peticiones idénticas. |
| Pregunta que distingue un contenedor verde de otro de húmedos | `--stage container-contrast --live`: foto del verde y control con verde y gris. |
| Rúbrica general de visión o cambio de modelo | Las 12 fotos habituales con `--live --fresh`; ampliar después a los casos difíciles afectados. |
| Comprobación periódica más amplia | `--suite difficult` o `--suite all`, en grupos por tema y con presupuesto explícito. |

`initial` evalúa la primera lectura de cada modelo. `container-contrast` llama a la repregunta real con su descriptor canónico y el contraste actual. No son una ejecución completa del consenso, del árbitro o de la descripción final. La gravedad queda como dato histórico, sin puntuarla como si fuese la gravedad del resultado final. Los fallos conocidos se muestran como fallos, sin convertirlos en aciertos esperados.

## Uso

Desde la raíz del proyecto:

```sh
# Sin solicitudes a OpenRouter.
.venv/bin/python -m unittest discover -s eval/vision -p 'test_*.py' -v
.venv/bin/python eval/vision/run.py plan

# Primera ejecución habitual: hasta 36 peticiones, 12 fotos y tres modelos.
.venv/bin/python eval/vision/run.py run --live --budget 0.50

# Repetir con las respuestas guardadas. No hace solicitudes de red.
.venv/bin/python eval/vision/run.py run

# Solo el contraste de contenedores: seis peticiones como máximo.
.venv/bin/python eval/vision/run.py run --stage container-contrast --live --budget 0.10

# Un grupo afectado por un cambio. Sin --limit se selecciona todo el grupo.
.venv/bin/python eval/vision/run.py plan --suite difficult --tags escombros --limit 10
.venv/bin/python eval/vision/run.py run --suite difficult --tags escombros --limit 10 --live --budget 0.50

# Nueva lectura real, aunque exista una respuesta guardada.
.venv/bin/python eval/vision/run.py run --suite all --ids green-TC03772 r4-T044 --live --fresh --budget 0.10

# Comparar dos reportes sin pagar solicitudes adicionales.
.venv/bin/python eval/vision/run.py compare /ruta/before.json /ruta/after.json
```

Los modelos predeterminados son `openai/gpt-5-mini`, `google/gemini-3.5-flash-lite` y `openai/gpt-5.6-luna`. `--models` permite elegir uno o varios. El comando lee la credencial de OpenRouter del `.env` del proyecto cuando existe; no la guarda en los reportes.

Cada ejecución produce JSON, una página HTML con fotos y discrepancias, y un registro de solicitudes. El JSON incluye resultados por modelo, tema y categoría, omisiones, falsos positivos, respuestas originales, proveedor, identificador de respuesta y costo. Los archivos reciben nombres únicos; no se sobrescribe una referencia existente.

El código de salida es `0` si todas las categorías revisadas pasan, `1` si hay discrepancias y `2` si faltan respuestas o fotos, hay errores, o se alcanzó el presupuesto. La comparación detecta errores nuevos aunque una foto ya tuviera otro fallo. Fotos, etiquetas o contextos distintos se marcan como no comparables.

## Cómo se limita el gasto

La ejecución predeterminada es sin red. `--live` habilita OpenRouter. Las llamadas son secuenciales, sin reintentos automáticos, y se registra `usage.cost` de cada respuesta, incluso si el contenido resulta inválido. El campo corresponde al costo informado por OpenRouter en su [documentación de uso](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

El umbral predeterminado es US$0.50. Antes de cada solicitud se reserva US$0.03 con `--reserve`; si el saldo restante no alcanza, no se inicia esa solicitud. Un costo desconocido o mayor que la reserva detiene las siguientes llamadas. Si no se conoce el costo, se contabiliza la reserva y el reporte marca `billing_uncertain`.

Este control limita la admisión de solicitudes; no es un límite de facturación impuesto por OpenRouter. Una solicitud en curso podría superar la reserva. Al elegir modelos más caros, hay que aumentar la reserva y revisar el presupuesto. Para un límite externo estricto, hace falta configurarlo también en la cuenta o clave del proveedor.

La caché se identifica con el modelo, endpoint, mensajes completos, imagen procesada exacta, contexto, temperatura, semilla, configuración de proveedor y límite de tokens. Cambiar cualquiera de esos valores obliga a una nueva lectura con `--live`. Las respuestas vencen a los siete días, configurable con `--max-age-days`. Los errores y respuestas truncadas no se reutilizan. `--fresh` ignora la caché.

Una respuesta reutilizada permite comprobar el procesamiento de evidencia guardada sin pagar otra lectura. No demuestra que el modelo siga respondiendo igual hoy. Los cambios de prompts requieren peticiones nuevas. Cuando una comparación fresca muestra un cambio pequeño o inesperado, conviene repetir esas mismas fotos antes de atribuirlo al cambio de código.

## Casos y etiquetas

Los grupos cubren subtipos y contenedores inventados, daños reales y aparentes, tapas trabadas, desbordes, cartón frente a madera, mantas frente a colchones, mobiliario urbano, objetos secundarios, escombros omitidos o falsos, bolsas opacas, propiedad privada, poda y escenas nocturnas. `plan --suite all` muestra el conjunto seleccionado. Se pueden combinar etiquetas con `--tags`; la selección coincide con cualquiera de ellas.

Solo se puntúan claves marcadas explícitamente `true` o `false` por una persona. Una clave ausente significa que no fue revisada. Las predicciones adicionales quedan visibles como `unreviewed_predictions`, sin asignarles un falso positivo automáticamente. Por eso, un caso que pasa no certifica todas las afirmaciones del modelo.

Las fotos idénticas se agrupan por SHA-256 y contexto. Si dos revisiones discrepan, esa categoría queda fuera de la puntuación. La etiqueta de voluminosos de T053 está en conflicto entre la revisión guardada y el registro posterior. La interpretación incierta de escombros de septiembre T009 tampoco se puntúa. El conjunto inicial conserva estas limitaciones y no utiliza respuestas de los modelos como verdad de referencia.

Para agregar un fallo nuevo, guardar la foto original y un JSON de expectativas revisadas, por ejemplo:

```json
{
  "contenedor_secos": true,
  "contenedor_humedos_lateral": false,
  "recoleccion": true
}
```

```sh
.venv/bin/python eval/vision/run.py add \
  --photo /ruta/foto.jpg --id contenedor-nuevo-001 \
  --expected /ruta/expectativas.json --tags contenedores contenedor_fantasma \
  --note 'Un contenedor verde; el modelo confundió su carga lateral con residuos húmedos.'

.venv/bin/python eval/vision/run.py run --suite all --ids contenedor-nuevo-001 --live
```

`add` no llama a la API. Conserva las altas en `private/additions.json` para futuras importaciones. Rechaza identificadores o fotos/contextos duplicados; una etiqueta existente debe reconciliarse explícitamente. Usar `--smoke` solo para casos representativos que deban integrar cada ejecución habitual. Agregar también una foto de control cuando una corrección pueda ocultar un caso verdadero.

Para corregir etiquetas existentes después de una revisión humana:

```sh
.venv/bin/python eval/vision/run.py review --file eval/vision/private/review-20260906.json
```

El archivo contiene una lista de revisiones con `case`, `photo_sha256`, `context`, `reviewed_at`, `source`, `note` y `labels`. Cada etiqueta es `true`, `false` o `null` (incierta, sin puntuar). Solo se modifican las claves indicadas. Las revisiones quedan en `private/reviews.json`, se reaplican al importar y conservan las etiquetas previas por caso. Las notas no se envían como contexto al modelo. Una foto o contexto distintos invalidan la revisión.

La revisión humana del 6 de septiembre corrige T109 a secos y cama descartada, agrega el negativo de escombros en U022 y deja subtipo/recolección de T050 sin puntuar hasta aclararlos. T008 conserva reparación; V049 conserva escombros y recolección. El reporte previo se mantiene: una comparación con etiquetas distintas se marca no comparable, nunca como mejora de código. La referencia `20260906-pipeline-review-guard.json` usa estas etiquetas revisadas con las mismas respuestas registradas, sin nuevas llamadas.

## Reconstruir la colección privada

```sh
.venv/bin/python eval/vision/run.py prepare \
  --reviews-root ../bacollab-vision \
  --september-root /ruta/ojo-urbano-validacion-escombros-20260905 \
  --green-photo /ruta/TC_03772.JPG
```

La importación solo lee revisiones marcadas como terminadas, copia las fotos y conserva la procedencia y el hash de cada revisión. Informa fuentes faltantes y conflictos. No hace llamadas a OpenRouter. Una instalación sin esas fuentes no tiene la colección completa, aunque pueda ejecutar una selección disponible.
