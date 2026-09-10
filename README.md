# Ojo Urbano

Ojo Urbano es una API en Python que clasifica fotos de incidencias urbanas. Reconoce residuos en la vía pública, escombros, objetos voluminosos, contenedores y cestos, baches, veredas, vehículos, plagas, poda, volquetes y las demás categorías definidas en [`categorias.json`](categorias.json), 44 en total.

El clasificador local es un modelo open source que entrenamos: embeddings de CLIP, DINOv2 y SigLIP2 con un cabezal de regresión logística multi-etiqueta, sobre miles de fotos callejeras etiquetadas a mano. Corre en tu máquina y no manda la foto a ningún lado. Con una clave de [OpenRouter](https://openrouter.ai), tres modelos de visión revisan la misma imagen y la API cruza esos resultados con el modelo local antes de publicar una categoría.

## Arranque rápido

Necesitás Python 3, un entorno virtual y varios GB libres para los embeddings. Los modelos se descargan una sola vez.

```bash
git clone https://github.com/bradlymcgregorhale/ojo-urbano.git
cd ojo-urbano
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Para obtener una clasificación completa, editá `.env` y cargá `OPENROUTER_API_KEY`. Sin esa clave el servidor arranca y ejecuta el modelo local, pero no publica categorías: `problemas` queda vacío porque falta la verificación.

```bash
python servidor.py
```

La primera ejecución descarga CLIP, DINOv2 y SigLIP2. Puede tardar y necesita varios GB de disco y de RAM. En una Mac con poca memoria se va a sentir.

Cuando termine de cargar, abrí http://127.0.0.1:8080 y arrastrá una foto. La portada permite subir varias y descargar un CSV al finalizar.

También podés llamar a la API con curl:

```bash
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" http://127.0.0.1:8080/clasificar
```

O usar el cliente de ejemplo:

```bash
python ejemplo.py foto.jpg
```

Una clasificación con verificación tarda entre 25 y 60 segundos.

## Qué pasa con una foto

La API recibe la imagen y, si existe, el texto escrito por quien reporta en el campo `contexto`.

El modelo local procesa la foto en tu máquina. Combina embeddings de CLIP, DINOv2 y SigLIP2 con un clasificador multi-etiqueta de regresión logística, entrenado con miles de fotos callejeras etiquetadas a mano. También estima una gravedad de 1 a 5.

Si configuraste OpenRouter, GPT-5 mini, Gemini Flash Lite y GPT-5.6 luna evalúan la misma imagen con una rúbrica por categoría. La confirmación visual necesita al menos dos fuentes. La aceptación contextual de escombros, explicada abajo, es una excepción. El modelo local participa como una fuente y sus puntuaciones aparecen por separado en `modelo_local`.

Una detección sostenida por una sola fuente vuelve en `posibles`. DeepSeek actúa como árbitro de texto cuando hay desacuerdos y deja el motivo en la respuesta. Por default no convierte una detección aislada en problema confirmado, porque las mediciones no mostraron una mejora.

El contexto también participa en la decisión. Si la foto no muestra lo que la persona describió, el reclamo se arma a partir del texto y los hallazgos visuales pasan a `descartados_por_foto`. Sin contexto, se informa lo que aparece en la imagen.

Escombros tiene una excepción a ese ruteo textual: la foto debe mostrar bolsas chicas o material suelto en espacio público. Una afirmación del vecino puede resolver el contenido oculto, pero no habilita un retiro dentro de propiedad privada ni de bolsones grandes de obra.

Hay categorías que el modelo local no conoce, entre ellas `vehiculo_mal_estacionado` y `columna_poste_cable`. En esos casos la detección depende de los modelos de visión y necesita coincidencia entre dos fuentes.

## Dónde ayuda el modelo local

Su aporte más claro está en la identificación de contenedores. Detecta si hay uno y distingue entre húmedos de carga lateral, húmedos de carga bilateral y secos. En las fotos etiquetadas, la presencia y el tipo alcanzan F1 0,97.

Los modelos de visión confunden seguido los contenedores laterales con los bilaterales. El modelo local ayuda a desempatar y, ante un desacuerdo fuerte, puede corregir incluso un voto unánime equivocado. También reduce falsos contenedores, como una bolsa verde interpretada como uno de secos junto a un contenedor real.

Si dos verificadores ven un contenedor verde y uno solo lo clasifica como húmedos, el modelo local puede precisar la pregunta de seguimiento: se busca un contenedor de húmedos físicamente separado del verde. Esto requiere un puntaje local de secos de al menos 0,95, uno de húmedos de hasta 0,05 y una diferencia de al menos 0,95. Si algún verificador ya vio ambos tipos, se conserva la pregunta habitual. El puntaje no descarta el contenedor: los otros verificadores tienen que revisar la foto. Se usa la misma llamada de seguimiento, sin agregar otra pasada.

La tarjeta muestra el tipo de contenedor detectado antes de la descripción. Si se confirman varios tipos, los enumera; las presencias sin confirmar quedan en el detalle.

El estado del contenedor, por ejemplo si está lleno, roto o tiene la tapa trabada, queda principalmente a cargo de los modelos de visión. "Lleno/desbordado" es el punto con menor precisión.

El otro caso importante son los escombros embolsados, en especial bolsas de cascote fotografiadas de noche y sin material visible. En una prueba con 7 modelos de visión no hubo detecciones. El modelo local sí pudo separarlas de las bolsas de basura.

Cuando el modelo local tiene suficiente confianza y los verificadores ya confirmaron una pila como `recoleccion` o `retiro_muebles`, la API puede promover `retiro_escombros` con `reclasificado_por: "modelo_local"`. Un puntaje local cercano a cero se toma como señal para reentrenar, no para cambiar el prompt. Esta fusión se desactiva con `FUSION_ESCOMBROS=0`.

Después de la fusión y del ruteo textual, `politica_escombros.py` aplica una revisión de alcance. Se activa cuando ya hay una categoría candidata, el puntaje local de escombros llega a 0,70 o el texto menciona escombros, cascotes o restos de obra. Consulta a los verificadores con un prompt corto, sin votos anteriores ni puntajes locales. Hace falta que al menos dos modelos distintos vean una presentación y ubicación elegibles, sin una respuesta que las contradiga. Las bolsas chicas al lado de un bolsón se evalúan por separado.

Si una revisión completa discrepa sobre el material visible de una pila pública y la recolección ya tenía dos fuentes visuales, esa recolección puede conservarse en `posibles`, sin confirmar ninguno de los servicios. `verificacion_escombros.requiere_revision: true` identifica este conflicto y la página pide revisar el tipo de residuos. No habilita retiros automáticos ni evita las exclusiones por bolsas opacas, ubicación privada o bolsones de obra.

Si el vecino afirma que las bolsas contienen escombros y la foto es compatible, puede aceptarse ese dato. No alcanza una pregunta, una suposición, una negación ni una orden de clasificación. Ver cartón en una bolsa tampoco revela el contenido de las demás. Cuando el material sigue oculto, el resultado lleva `origen: "contexto_vecinal"`, una fuente y `confianza: "baja"`; los votos originales siguen disponibles en `modelos`. Una afirmación falsa sobre bolsas opacas puede pasar este control. El sistema no puede comprobar su contenido desde la foto.

Un alcance privado, limitado a bolsones grandes o indeterminado no puede volver a aceptarse por la fusión local ni por el texto. La misma pila tampoco se reasigna a recolección común, muebles o poda. Se conservan otros residuos públicos sólo si la revisión los identifica aparte. Un fallo de la revisión impide guardar la respuesta en caché.

El código de veredas `154014` es otro reclamo: restos o vallados abandonados por obras de empresas de servicios públicos que dificultan el paso. Si aparece como alternativa, una revisión corta del texto comprueba esas condiciones. "Escombros de una refacción" no alcanza. Un reclamo genuino por esa obra se conserva aunque haya un bolsón excluido del retiro de higiene; una sugerencia sin respaldo queda fuera de `problemas` y `categorias_contexto`.

Las veredas, los vehículos, la ocupación, las plagas y la luminaria dependen principalmente de los modelos de visión.

## Tecnología

El servicio está escrito en Python.

- FastAPI y uvicorn exponen la API desde `servidor.py`. La página de demostración está embebida en ese archivo, sin un frontend separado.
- `model.joblib` contiene el modelo local: embeddings de CLIP, DINOv2 y SigLIP2, un scaler, un OneVsRest de regresión logística y un regresor de gravedad. Corre en CPU con PyTorch, transformers, sentence-transformers, scikit-learn, joblib y Pillow.
- `verificador.py` hace las llamadas a OpenRouter. Los modelos de visión trabajan en paralelo. DeepSeek interviene como árbitro de texto porque sus modelos en OpenRouter no aceptan imágenes.
- Los criterios por categoría y las pasadas dirigidas están en [`prompts/`](prompts/README.md), junto con las instrucciones del árbitro y del análisis de texto.
- [`categorias.json`](categorias.json) contiene las 44 categorías propias. [`prestaciones.json`](prestaciones.json) contiene el catálogo completo de la Ciudad usado para interpretar el texto del vecino.

## API

### `POST /clasificar`

Recibe `multipart/form-data` con estos campos:

- `file`: la foto.
- `contexto`: texto opcional de hasta 500 caracteres.
- `verificar`: `auto`, `1` o `0`. El valor por default es `auto`, que verifica cuando hay una clave configurada. `1` fuerza la verificación y `0` devuelve una respuesta degradada, sin clasificación.

Los modelos usan `contexto` para interpretar la imagen y decidir si respalda lo que se describió. La API nunca devuelve ese texto porque puede contener nombres o patentes.

```bash
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" http://127.0.0.1:8080/clasificar
```

Ejemplo de respuesta:

```json
{
  "version": "4",
  "hay_problema": true,
  "hay_reclamo": true,
  "gravedad_maxima": 3,
  "problemas": [
    { "key": "recoleccion", "nombre": "Recolección de residuos", "gravedad": 3,
      "fuentes": 3 }
  ],
  "descripcion": "Bolsas de residuos y cajas de cartón acumuladas en la vereda junto a un contenedor negro de húmedos.",
  "categorias_contexto": [
    { "key": "desratizacion", "nombre": "Desratización / control de plagas en la vía pública",
      "respaldo_visual": "neutral", "fuentes": 2, "de": 3 }
  ],
  "foto_valida": null,
  "foto_valida_estado": "sin_contexto",
  "descartados_por_foto": [],
  "posibles": [
    { "key": "reparacion_cesto", "nombre": "Reparación de cesto papelero", "gravedad": 2,
      "fuentes": 1, "origen": "foto",
      "arbitro": "rechazar", "motivo": "Sin evidencia visual suficiente: las demás descripciones no mencionan ningún cesto." }
  ],
  "elementos_detectados": [
    { "key": "contenedor_humedos_lateral", "nombre": "Contenedor de húmedos, carga lateral" }
  ],
  "en_duda": [],
  "verificacion_activa": true,
  "modelos": [
    { "modelo": "openai/gpt-5-mini", "ok": true, "sin_problema": false, "foto_corresponde": null,
      "categorias": [ { "key": "recoleccion", "gravedad": 3, "evidencia": "bolsas y cajas fuera del contenedor" } ],
      "descripcion": "..." }
  ]
}
```

### Campos de la respuesta

- `hay_problema` indica que hay por lo menos un problema confirmado. Siempre cumple `hay_problema == bool(problemas)`. `gravedad_maxima` resume solamente esas entradas.
- `hay_reclamo` indica que hay algo para tramitar, esté confirmado por la foto o no. Siempre cumple `hay_reclamo == bool(problemas or categorias_contexto)`. Puede ser `true` mientras `hay_problema` es `false` si el texto pide algo que la foto no confirma.
- `problemas` contiene lo que se reporta, con gravedad de 1 a 5 y la cantidad de `fuentes`. La confirmación visual requiere al menos 2 fuentes; el retiro de escombros basado en testimonio puede tener una sola, identificada como contexto. El clasificador local cuenta para el consenso. Sus detecciones aisladas no se convierten en incidencias publicadas.
- Una entrada de `problemas` puede traer `codigo`, correspondiente a una prestación del catálogo de la Ciudad, en lugar de `key`. Para aceptar ambos formatos usá `p.get("key") or p.get("codigo")`.
- Cada problema incluye `confianza`: `alta` con 3 o más fuentes y `media` con 2. La aceptación de escombros basada en contenido informado por el vecino usa una fuente y confianza `baja`. El campo superior `predominante` contiene la clave del problema de mayor gravedad. Si hay empate, elige el que tenga más fuentes. Vale `null` cuando no hay uno.
- `verificacion_escombros`, cuando aparece, informa `estado`, `motivo`, `basado_en_contexto` y `requiere_nueva_foto`. Su estado `apto` significa que la ubicación y presentación sirven para el retiro, no que el material esté confirmado. Para decidir qué reclamo se acepta, usar `problemas`. Una contradicción de contenido puede requerir aclaración aunque `requiere_nueva_foto` sea `false`.
- `patente` puede aparecer en escenas de `vehiculo_mal_estacionado` o `vehiculo_abandonado`. Acepta formatos argentinos como `AB123CD` y `ABC123`. Solo se devuelve cuando dos lectores independientes obtienen la misma cadena del vehículo protagonista. Cualquier lectura válida que discrepe la suprime. Si hace falta, la API vuelve a leer la foto a mayor resolución solo para la chapa. El valor aparece arriba y dentro del problema confirmado.
- `foto_valida` dice si la imagen respalda el contexto. `true` significa que sirve como prueba, `false` que no muestra lo reclamado y `null` que no pudo evaluarse. Un valor `null` no significa que la foto sea correcta.
- `foto_valida_estado` explica ese resultado. `corresponde` y `no_corresponde` acompañan a `true` y `false`. Con `null` puede valer `sin_contexto`, `empate`, `sin_opinion` o `no_evaluado`. Solo `no_corresponde` descarta los hallazgos visuales.
- `posibles` reúne detecciones no confirmadas y siempre está presente. `origen` puede ser `foto`, `foto_no_relacionada` o `contexto_vecinal`.
- `descartados_por_foto` contiene lo que mostraba una imagen marcada con `foto_valida: false`. Esas entradas no se reportan y traen `motivo_descarte`.
- `descripcion` resume la escena. La escribe el árbitro cuando interviene; de lo contrario se usa la descripción del verificador que más coincide con el resultado. Sin verificación vale `null`.
- `categorias_contexto` contiene lo que describe el texto y la foto no confirma. No modifica `gravedad_maxima` ni `hay_problema`, pero sí puede hacer que `hay_reclamo` sea `true`. Cada entrada trae `respaldo_visual`, con valor `compatible`, `neutral` o `contradice`. Un reclamo ajeno a las 44 categorías puede mapear a una prestación de [`prestaciones.json`](prestaciones.json), que usa `codigo` en lugar de `key`.
- `elementos_detectados` enumera los contenedores visibles aunque no tengan problemas.
- `en_duda` contiene categorías con una fuente que el árbitro no resolvió. Las detecciones aisladas normalmente aparecen en `posibles`. La excepción son las claves de presencia `contenedor_*`: no pasan por el árbitro ni por `posibles`. Con una fuente quedan en `en_duda`; con dos pasan a `elementos_detectados`.
- `calidad_foto` informa `lado_menor`, `nitidez`, `luminancia` y `definicion`, que puede ser `buena` o `limitada`. No filtra resultados ni cambia decisiones. Sobre 200 fotos etiquetadas por personas, la calidad global no predijo los errores.
- `modelos` contiene la salida de cada modelo de visión. El clasificador local no aparece ahí.
- `modelo_local`, cuando se ejecutó, contiene `probabilidades` con clave, nombre y `score`, además de `umbral` y `revision_material`. El nombre histórico del campo no implica probabilidades calibradas: son puntuaciones del clasificador, separadas del veredicto. La interfaz las muestra en "Más detalle". No se publican el contexto ni los demás datos internos.

### Respuestas de revisión de materiales

Cuando se ejecuta `alcance_escombros`, `verificacion_escombros` incluye el
diagnóstico con `detalle_version: 1`. Está disponible en `/clasificar` y en
los resultados de `/trabajos`, también al recuperar un análisis de caché.
No agrega consultas a los modelos ni cambia el veredicto.

| Campo | Contenido |
| --- | --- |
| `detalle_estado` | `completo` si todos los participantes respondieron válidamente; `parcial` si algunos fallaron; `sin_respuestas` si todos fallaron; `no_disponible` si faltan datos verificables de un resultado histórico. |
| `revisiones` | Una entrada por modelo participante, en el orden configurado durante ese análisis. Los reintentos no son fuentes adicionales. |
| `decision` | Etapa, reglas aplicadas, explicación determinística y movimientos de categorías. Puede ser `null` en resultados históricos. |

Un diagnóstico `completo` puede tener opiniones contradictorias y
`requiere_revision: true`. Tampoco garantiza que haya suficientes fuentes
para confirmar: describe las respuestas obtenidas, no su certeza.

Cada revisor incluye `modelo`, `estado` (`ok` o `sin_respuesta_valida`),
`respuesta` y `ajustes`. `respuesta` contiene los valores efectivos de
`ubicacion`, `presentacion`, `material`, `hay_bolsas_opacas_o_parciales`,
`afirmacion_vecinal`, `afirma_validada`, `residuos_comunes_independientes` y
`otros_retiros_independientes`, junto con `evidencia_ubicacion`,
`evidencia_presentacion` y `evidencia_material`.

Las evidencias son observaciones del modelo, no hechos confirmados. Se
limitan a 160 caracteres y pueden ser `null` por saneamiento. Eso no
convierte una respuesta válida en fallida. No se publican citas del vecino,
prompts, respuestas crudas, errores del proveedor ni el contexto. Si el pedido
contiene contexto vecinal, las tres evidencias de texto son `null`: no se
puede garantizar que una frase libre no copie o parafrasee un dato del vecino.
Los valores estructurados y los ajustes siguen disponibles. Cuando
falla un revisor, `respuesta` es `null` y `ajustes` es `[]`.

Los ajustes distinguen el valor original del valor aplicado. Por ejemplo,
un modelo puede responder `incompatible_visible` y también indicar bolsas
opacas; el voto efectivo pasa a `oculto_o_ambiguo`. El registro conserva
`campo`, `valor_original`, `valor_aplicado` y uno de estos códigos en `motivo`:

- `presentacion_incompatible_con_bolsas_opacas`.
- `material_incompatible_sin_descartar_bolsas_opacas`.

`decision.reglas` enumera las ramas aplicadas. El catálogo es
`conservar_basura_publica`, `material_contradictorio`, `alcance_excluido`,
`alcance_indeterminado`, `contexto_sin_respaldo`, `escombros_por_contexto`,
`retirar_servicios_sin_residuos_independientes`, `material_publico_disputado`
y `sin_cambios`. Este último aparece solo si no se aplicó otra regla. Una
regla puede haberse aplicado sin mover categorías, por ejemplo al conservar
una recolección que ya estaba confirmada; por eso `efectos` puede ser `[]`.

Cada entrada de `decision.efectos` contiene una `key`, sus ubicaciones
`antes` y `despues` de esa etapa, y las `reglas` que explican el movimiento.
Las ubicaciones posibles son `problemas`, `posibles`, `categorias_contexto`,
`descartados_por_foto`, `elementos_detectados` y `en_duda`. Las listas vacías
indican ausencia pública. Los hallazgos exclusivos del local se filtran
antes de comparar; no se exponen a través de `antes`.

Ejemplo sintético de un efecto dentro de una decisión de conflicto:

```json
{
  "key": "recoleccion",
  "antes": ["problemas"],
  "despues": ["posibles", "en_duda"],
  "reglas": [
    "retirar_servicios_sin_residuos_independientes",
    "material_publico_disputado"
  ]
}
```

`despues` describe el cierre de `alcance_escombros`. Una etapa posterior
puede cambiar la categoría; para consumir la clasificación usá siempre
las colecciones finales del resultado. El diagnóstico no atribuye a esta
revisión decisiones previas del local o del árbitro.

Un identificador de modelo que contiene una URL o parece una credencial se
omite y el detalle se marca `no_disponible`.

Un resultado histórico conserva únicamente las respuestas identificables,
con `ajustes: null` si no se guardaron las normalizaciones. Sus evidencias
de texto se omiten si no se puede verificar el saneamiento del contexto.
Si solo quedó el JSON público anterior, el diagnóstico es:

```json
{
  "detalle_version": 1,
  "detalle_estado": "no_disponible",
  "revisiones": [],
  "decision": null
}
```

No se vuelve a analizar la foto para completar ese detalle. Si la etapa
no se ejecutó, se conserva la ausencia de `verificacion_escombros`.
`tokens_api`, `tokens_api_completos` y `costo_api` siguen describiendo el
análisis original; esta lista de revisores no desglosa todas sus llamadas.

## Reglas de clasificación

### Contexto y foto

| Situación | Resultado |
|---|---|
| Hay contexto y la foto lo respalda | Se reporta lo de la foto. `foto_valida: true` |
| Hay contexto y la foto no lo respalda | Se reporta lo que pidió el vecino. Lo de la foto pasa a `descartados_por_foto`. `foto_valida: false` |
| Hay contexto y no mapea a nada del catálogo | `hay_problema: false`. No se inventa un reporte |
| No hay contexto | Se reporta lo de la foto. `foto_valida: null` |

Cuando el texto es ambiguo, la API elige la categoría genérica. Por ejemplo, "mi cuadra está llena de basura" corresponde a `recoleccion`, no a `retiro_muebles` ni a `retiro_escombros`.

### Detecciones de una sola fuente

Por default, el árbitro no confirma lo que vio una sola fuente. Esas detecciones aparecen en `posibles`. En una prueba con cuatro modelos de árbitro, solo 2 de 21 confirmaciones fueron rescates correctos. `ARBITRO_CONFIRMA=1` recupera el comportamiento anterior.

### Presencia de contenedores

Publicar un contenedor que no existe es un error costoso, por lo que la API aplica dos vetos de presencia mediante revisiones dirigidas y decisión por mayoría.

El veto general corre cuando los verificadores confirman un contenedor pero el clasificador local da valores mínimos para todas las claves de contenedor. Si la revisión dirigida concluye que no hay ninguno, la presencia baja a `en_duda`. Para esta decisión, un contenedor municipal es ancho, de unos dos metros. Un tacho angosto y vertical no cuenta como tal.

El segundo veto revisa una clave concreta cuando un objeto junto a un contenedor real fue interpretado como otro contenedor. Se aplica a `contenedor_secos` y al bilateral, donde el clasificador local es un detector confiable. No se aplica al lateral porque es el tipo más común y el que el modelo local más omite. Cuando este veto se activa, la descripción pierde solamente las frases referidas al contenedor descartado.

Con `revision_contenedores: "contenedores-preservacion-20260906"`, una discrepancia local fuerte puede abrir una revisión de color: secos al menos `0.99`, ambos húmedos como máximo `0.01` y tres lecturas completas e independientes, de las cuales al menos dos identifican el mismo tipo húmedo y ninguna identifica otro tipo. Omitir el tipo no equivale a negar el contenedor. Para cambiarlo a secos, al menos dos revisores deben describir verde explícitamente, sin votos de ausencia ni errores. Después se comprueba que no haya otro contenedor húmedo físicamente separado. La descripción se ajusta al tipo confirmado y las lecturas originales siguen disponibles en el detalle.

### Fusión de escombros embolsados

Esta fusión solo corre con la verificación activa. Tiene un nivel confiado, con puntaje interno mayor o igual a `0.95` y puntaje local de recolección menor o igual a `0.2`, y uno de rescate, con valores de `0.70` y `0.1`.

En ambos niveles debe existir otra pila confirmada. Una poda confirmada bloquea la fusión. También la bloquea el rechazo dirigido de un verificador que indique que no son escombros, salvo en el nivel confiado cuando la corroboración viene solamente de `retiro_muebles`.

Los pesos con `revision_material: "escombros-preservacion-20260906"` permiten además una escena mixta cuando escombros y recolección puntúan al menos `0.95`, un clasificador auxiliar de material también alcanza `0.95` y los verificadores ya confirmaron recolección. Se conservan ambos servicios; poda, rechazo dirigido y exclusiones de alcance siguen bloqueando la promoción. Los pesos anteriores no habilitan esta ruta.

El ajuste parte de los pesos originales y conserva sus puntuaciones sobre fotos de referencia, salvo las correcciones revisadas. Cambia los dos cabezales plegados a escombros y los tres tipos de contenedor; conserva el scaler, la gravedad y los demás cabezales. El auxiliar usa todas las etiquetas de `photo_tags`: una etiqueta principal de recolección no excluye una segunda etiqueta de escombros.

Una respuesta que confirma la existencia de un saco de cemento no demuestra basura común aparte. Tampoco se descarta el cartón por aparecer junto a un mueble: dos respuestas abiertas que identifican cartón separado en el piso pueden corroborar la recolección sin duplicar el voluminoso. La descripción final no puede negar escombros confirmados.

Si la fusión se activa y `recoleccion` estaba confirmada con un puntaje local bajo, esa entrada pasa a `posibles`.

Las variables relacionadas son `FUSION_ESCOMBROS`, `FUSION_ESCOMBROS_UMBRAL`, `FUSION_ESCOMBROS_RECO_BAJA`, `FUSION_ESCOMBROS_UMBRAL_RESCATE` y `FUSION_ESCOMBROS_RECO_RESCATE`.

## Trabajos asíncronos

### `POST /trabajos` y `GET /trabajos/{id}`

Una llamada sincrónica mantiene la conexión abierta durante los 25 a 60 segundos del análisis. Eso complica los lotes y los despliegues detrás de un proxy con tiempo máximo de conexión. Cloudflare corta alrededor de los 100 segundos.

`POST /trabajos` recibe el mismo `multipart/form-data` que `/clasificar` y responde de inmediato:

```json
{ "trabajo": "kJ9vX2...", "estado": "en_cola", "posicion": 1 }
```

Consultá `GET /trabajos/{id}` o `GET /trabajos?id=...` hasta recibir `listo`, que incluye `resultado`, o `error`, que incluye `detail`.

Los estados posibles son:

- `en_cola`, con `posicion`. El valor `1` corresponde al próximo trabajo.
- `procesando`.
- `listo`.
- `error`.

Para cancelar un trabajo en cola, usá `DELETE /trabajos/{id}`, `DELETE /trabajos?id=...` o `POST /trabajos/cancelar?id=...`. La respuesta es `{"estado": "cancelado"}`.

Un trabajo que ya está `procesando` no puede detenerse y responde `409`. Si el trabajo terminó, `DELETE` borra su registro.

Otras reglas:

- Si la foto está en caché, el `POST` responde directamente con `{"estado": "listo", "resultado": ...}`.
- Los pedidos sincrónicos tienen prioridad sobre los trabajos en cola.
- `TRABAJOS_MAX` limita los pendientes. Su default es `10`; por encima devuelve `503`.
- `TRABAJOS_POR_IP` limita los pendientes por IP. Su default es `4`; por encima devuelve `429`.
- `TRABAJO_TTL` conserva el resultado durante `1800` segundos por default.
- `TRABAJO_ESPERA` permite esperar el turno durante `900` segundos por default.
- Los trabajos viven en la memoria del proceso y se pierden al reiniciar.
- Un `404` al consultar significa que el trabajo es desconocido o venció. El cliente debe volver a enviar la foto.
- El `POST` usa las mismas protecciones que `/clasificar`. Consultar el estado no consume cuota.

La portada disponible en `GET /` usa esta vía.

### `GET /salud`

Devuelve el estado del servicio: las clases del modelo, si la verificación está activa y qué modelos usa.

## Configuración

La configuración se lee desde variables de entorno o `.env`. La lista completa está en [`.env.example`](.env.example).

| Variable | Default | Qué hace |
|---|---|---|
| `OPENROUTER_API_KEY` | vacía | Habilita la verificación cruzada. Nunca la commitees. |
| `VERIFICADORES` | tres modelos (ver `.env.example`) | Modelos de visión separados por coma. Podés configurar uno, dos, tres o más. Una categoría necesita al menos 2 fuentes y el modelo local cuenta como una. Las pasadas dirigidas agregan llamadas solo cuando se activan. La repregunta entre modelos corre con 3 o más verificadores. |
| `ARBITRO` | `deepseek/deepseek-v4-flash` | Modelo que resuelve desacuerdos. Vacío desactiva el árbitro. Puede ser de texto o tener visión. |
| `ARBITRO_VE_FOTO` | apagado | Envía la foto al árbitro si este tiene visión. |
| `ARBITRO_CONFIRMA` | apagado | Permite que el árbitro confirme una categoría informada por una sola fuente. Está apagado por los resultados de la medición. |
| `UMBRAL` | `0.5` | Probabilidad mínima para que el modelo local proponga una categoría. |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Dirección y puerto de la API. |
| `VERIFICADOR_TIMEOUT` | `120` | Segundos por llamada a OpenRouter. |
| `VERIFICADOR_DEADLINE` | `180` | Tiempo máximo total de reintentos por modelo. |
| `OPENROUTER_CACHE_PROMPTS` | `0` | Prueba opt-in de afinidad por modelo y prefijo de sistema. Apagada hasta demostrar ahorro consistente. |
| `OPENROUTER_LOG_USO` | `1` | Registra tokens, caché y costo por intento en stderr. `0` lo apaga. |

Los modelos de DeepSeek disponibles en OpenRouter no aceptan imágenes. Por eso el valor configurado por default interviene como árbitro de texto.

Si se activa, la afinidad usa `prompt_cache_key`, calculada con el modelo y los mensajes
iniciales de sistema. La foto y el contexto del vecino no forman parte de esa
clave. Cada pedido conserva todos sus mensajes: no se reutilizan respuestas de
otras fotos ni se recortan reglas, modelos o pasadas dirigidas. Los pedidos sin
prefijo de sistema, como la lectura de patente, mantienen el ruteo automático.
El failover conserva su configuración anterior.

En la prueba del 2026-09-05 comparé nueve fotos, tres modelos y ambos modos
(54 llamadas de primera pasada). El ruteo habitual costó USD 0,06716852 y la
clave estable, USD 0,06657341: 0,9% menos, con resultados inconsistentes entre
la muestra inicial y la segunda tanda. Ambos reutilizaron cerca del 75% de
los tokens de entrada. La muestra es chica, comparte cachés del proveedor y
las respuestas varían entre corridas; no demuestra ahorro atribuible al cambio
ni equivalencia estadística de calidad. Por eso la afinidad sigue apagada.

El ahorro depende de los hits reales del proveedor, la frecuencia de pedidos y
la vigencia de su caché. No se activan breakpoints ni almacenamiento explícito
pago. Los registros `openrouter_uso` permiten medirlo sin llamadas extra:
incluyen etapa, modelo, tokens de entrada/salida/razonamiento, `cached_tokens`,
`cache_write_tokens`, costo, duración, proveedor e identificador de generación.
Un campo ausente queda en `null`, no en cero. Los intentos fallidos o truncados
también quedan registrados; un fallo de red puede tener un costo que la API no
alcanzó a informar. No se registran fotos, contexto, prompts ni respuestas.
En producción van al log privado `arranque.log`; su retención y rotación quedan
a cargo del despliegue, igual que el resto del log del servicio.
Ver [caché de prompts](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
y [contabilidad de uso](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

## Si publicás la API

Cada foto usa entre 25 y 60 segundos de CPU y una llamada paga a OpenRouter por verificador, tres por default. Si hay una disputa, puede sumarse la llamada al árbitro.

`/clasificar` incluye estos límites:

| Variable | Default | Qué hace |
|---|---|---|
| `MAX_BYTES` | `10485760` (10 MB) | Tamaño máximo del archivo. Si lo supera, responde `413`. |
| `MAX_PIXELES` | `25000000` | Cantidad máxima de píxeles. Frena bombas de descompresión y responde `400`. |
| `CONCURRENCIA` | `1` | Clasificaciones simultáneas. Si se supera, responde `503`. |
| `RATE_LIMITE` / `RATE_VENTANA` | `60` / `3600` | Solicitudes permitidas por IP dentro de la ventana en segundos. Si se supera, responde `429` con `Retry-After`. `0` desactiva el límite. |
| `CUOTA_DIARIA` | `500` | Máximo global de fotos verificadas por día. Al superarlo, la API responde en modo degradado. `0` desactiva la cuota. |
| `API_TOKEN` | vacío | Si tiene un valor, `POST /clasificar` exige el encabezado `X-Api-Token`. |
| `CACHE_MAX` | `128` | Cantidad de respuestas guardadas por hash de foto. |
| `CONFIAR_PROXY` | apagado | Usa `X-Forwarded-For` para calcular el límite por IP. |

Si hay un proxy, activá `CONFIAR_PROXY` y configurá el proxy para reemplazar el `X-Forwarded-For` enviado por el cliente. Sin `CONFIAR_PROXY`, todas las conexiones pueden aparecer como `127.0.0.1` y compartir una sola cuota. Si lo activás sin reemplazar el encabezado, un cliente puede rotarlo para evitar el límite. Sin proxy, dejalo apagado.

También conviene configurar un límite de tamaño de cuerpo en el proxy, como `client_max_body_size` en nginx, y un tope mensual de gasto para una clave de OpenRouter dedicada al servicio. `CUOTA_DIARIA` pertenece al proceso y se reinicia junto con él.

Una solicitud `multipart/form-data` no dispara el preflight de CORS. Cualquier página puede llamar al endpoint, por lo que el límite por IP y el token son las barreras disponibles.

El límite por IP y el de concurrencia viven en la memoria del proceso. Si ejecutás varias instancias, tenés que moverlos al proxy o a un almacenamiento compartido.

## Contexto e inyección de prompt

El contenido de `contexto` y cualquier texto visible dentro de la foto llegan a los modelos. Ambos se tratan como datos no confiables.

- La rúbrica se envía en un mensaje `system`. El texto del usuario va en `user`. El árbitro usa la misma separación.
- Las categorías se deciden por los objetos visibles. Un texto dirigido a quien analiza la imagen no cuenta como evidencia.
- Las descripciones tienen longitud limitada y no incluyen caracteres de control.

Los verificadores usan la misma foto y el mismo prompt, así que no son fuentes independientes. Una inyección que funcione en dos modelos alcanza para formar consenso.

`CONSENSO_VLM_SOLO=arbitro` envía esas categorías al árbitro, pero no es el valor por default porque una evaluación no pudo demostrar que ayudara. El comportamiento está implementado en [`verificador.py`](verificador.py) y evaluado en [`eval/`](eval/).

`descripcion` es texto generado por un modelo y puede estar influido por quien subió la foto. Escapalo antes de insertarlo en HTML y no abras reportes automáticos sin revisión humana.

## Si un proveedor se cuelga

Las llamadas a OpenRouter tienen un límite absoluto de tiempo, no solamente un timeout por operación de socket. `urllib` reinicia su timeout con cada byte, por lo que los keepalives enviados mientras un modelo genera podrían dejar un hilo esperando sin límite. Cuando se cumple el plazo, el servicio ejecuta `shutdown()` sobre el socket.

Esto ya pasó: un hilo quedó ocupado y, con `CONCURRENCIA=1`, todas las llamadas a `/clasificar` devolvieron `503` hasta reiniciar. Mientras tanto, `/salud` siguió respondiendo `200`.

`TECHO_TRABAJO` funciona como última protección y tiene un default de `600` segundos. Al superar ese tiempo, el trabajo se considera perdido y libera su cupo. El hilo continúa vivo porque Python no permite matarlo, pero el servicio vuelve a aceptar trabajo.

## Privacidad

Sin verificación, el modelo local procesa la foto en la máquina y no la envía.

Con la verificación activa, la imagen se reduce a 1024 px y se envía a los modelos configurados mediante OpenRouter. Revisá las políticas de datos de esos proveedores antes de procesar fotos sensibles.

El texto de `contexto` también llega a los modelos, pero no vuelve en la respuesta. En reportes de vehículos puede aparecer la `patente` leída de la chapa visible, que es el dato requerido por el trámite. Nunca se toma del texto escrito por el vecino.

Las descripciones de los modelos forman parte de la respuesta y pueden transcribir texto visible en la imagen. Si eso representa un problema, no guardes las respuestas.

## Licencia

[MIT](LICENSE)

### Consumo de tokens por foto

La respuesta de análisis incluye `tokens_api`, la suma de `usage.total_tokens`
informada por OpenRouter en todos los intentos usados para procesar esa foto.
Incluye verificadores, árbitro, revisiones dirigidas y el especialista de
contenedores. Si falta el total y están ambos conteos, suma `prompt_tokens` y
`completion_tokens`. Los detalles de razonamiento y caché no se suman otra vez.

`tokens_api_completos` indica si todos los intentos informaron un conteo válido.
Si vale `false`, `tokens_api` contiene solamente la suma conocida: el consumo de
los intentos sin datos no se puede determinar. Sin llamadas, el total es cero y
el conteo está completo. Cada foto tiene su propio acumulador, incluso cuando
se procesan varias en paralelo.

Una respuesta recuperada de la caché local conserva los tokens del análisis
original; ese valor no representa consumo adicional por consultar la caché.
Estos campos no generan llamadas extra ni modifican las clasificaciones o el
campo `costo_api`. La página sigue sin mostrar importes.

### Inventario especializado de contenedores

`CONTENEDORES_ESPECIALISTA=1` activa una pasada fija para identificar secos,
humedos de carga lateral y humedos de carga bilateral. Usa la misma foto
original, seis referencias privadas y la configuracion validada. La plantilla
se instala fuera de git en `eval/vision/private/serving-contenedores-051.json`;
el modulo verifica su SHA-256 antes de cada solicitud. Sin la bandera, el
comportamiento anterior se conserva. Las cuotas y la opcion de desactivar la
verificacion siguen aplicando.

La respuesta publica agrega `contenedores`: `estado` es `confirmado` o
`revision`; `tipos` es una lista (vacia si no se detectan contenedores) o `null`
cuando hace falta revision; `motivo` explica la revision. Los tipos confirmados
sustituyen solamente esos tres tipos en `elementos_detectados`. Recoleccion,
escombros, danos y los otros reclamos conservan sus reglas. Si una lectura vacia
contradice un reclamo confirmado sobre un contenedor, el inventario publico
queda en revision. La presencia visible sigue siendo informativa cuando la foto
no corresponde al texto del reclamo.

Los fallos de transporte no se cachean. Una respuesta valida pero incierta
puede cachearse y sigue siendo revision, nunca ausencia. La interfaz muestra
ese estado y el CSV agrega `contenedores_estado` y `contenedores_motivo`.

La pasada realiza un solo intento, con un plazo de 40 segundos, y suma su costo
al procesamiento existente. La API conserva `costo_api` para contabilizar el
consumo, pero la pagina no muestra ese importe. El promedio observado en 100 fotos fue USD0.0038
adicionales por foto; no representa el costo de las otras categorias. Tras la
revision humana de cuatro referencias, hubo 98 respuestas automaticas correctas,
un tipo omitido y una revision. Ese resultado no garantiza la exactitud en otras
fotos ni evalua el conteo de contenedores.
