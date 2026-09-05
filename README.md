# Ojo Urbano

API en Python para clasificar fotos de incidencias urbanas: residuos en la vía pública, escombros, voluminosos, contenedores y cestos, baches, veredas, vehículos, plagas, poda, volquetes y el resto de las 44 categorías de [`categorias.json`](categorias.json).

Corre en tu máquina. El modelo local no cobra nada ni manda la foto a ningún lado. Si le das una clave de [OpenRouter](https://openrouter.ai), tres modelos de visión revisan la misma foto y el veredicto público sale de ese cruce.

## Arranque rápido

Hace falta Python 3 y un entorno virtual. Los embeddings ocupan varios GB de disco y se bajan una sola vez. Para una clasificación completa también una clave de OpenRouter.

```bash
git clone https://github.com/bradlymcgregorhale/ojo-urbano.git
cd ojo-urbano
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Editá `.env` y poné `OPENROUTER_API_KEY`. Sin esa clave el servidor arranca igual, pero no publica categorías: `problemas` sale vacío. El modelo local sigue corriendo; lo que falta es la verificación, y sin verificación no hay veredicto público.

```bash
python servidor.py
```

La primera corrida baja CLIP, DINOv2 y SigLIP2. Tarda y pesa: varios GB en disco y varios GB de RAM. En una Mac justa puede ponerse pesada. Cuando terminó de cargar, abrí http://127.0.0.1:8080 y arrastrá una foto.

También por curl:

```bash
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" http://127.0.0.1:8080/clasificar
```

O con el cliente de ejemplo:

```bash
python ejemplo.py foto.jpg
```

Con verificación, una foto tarda 25-60 s. La portada acepta varias y al final deja bajar un CSV.

## Cómo se clasifica una foto

Entra la foto. Si quien reporta escribió algo, también ese texto (`contexto`).

Primero corre el modelo local, en tu máquina: tres extractores de embeddings (CLIP, DINOv2, SigLIP2) y un cabezal de regresión logística multi-etiqueta, entrenado con miles de fotos callejeras etiquetadas a mano. También estima gravedad de 1 a 5.

Si hay clave de OpenRouter, tres modelos de visión (GPT-5 mini, Gemini Flash Lite y GPT-5.6 luna) miran la misma foto con una rúbrica por categoría. Una categoría se confirma cuando la reportan al menos dos fuentes. El modelo local cuenta como una, pero su voto no se publica. Lo que vio una sola fuente vuelve en `posibles`, para que el consumidor repregunte.

Un árbitro de texto (DeepSeek) resuelve desacuerdos y deja el motivo en la respuesta. Por default no promueve lo de una sola fuente; se midió y no mejoraba.

El texto del vecino pesa. Si la foto no muestra lo que reclama, el reclamo se arma con el texto y lo visual pasa a `descartados_por_foto`. Si no escribió nada, se reporta lo de la foto.

Algunas categorías no existen en el modelo local (`vehiculo_mal_estacionado`, `columna_poste_cable` y otras). Las detectan solo los de visión, y se confirman cuando coinciden dos.

## Dónde rinde el modelo local

Identificar contenedores (si hay uno y de qué tipo: húmedos lateral, húmedos bilateral o secos) es donde más se nota. Sobre fotos etiquetadas, presencia y tipo dan F1 0,97. Los de visión confunden lateral con bilateral seguido; el local desempata, y si el desacuerdo es fuerte puede corregir un voto unánime equivocado. También corta contenedores fantasmas, por ejemplo una bolsa verde leída como contenedor de secos al lado de uno real. El estado del contenedor (lleno, tapa trabada, roto) lo miran los de visión, y "lleno/desbordado" es el punto más flojo de precisión.

Escombros embolsados es el otro caso. Bolsas de cascote, sobre todo de noche, sin material a la vista. Se midió con 7 modelos de visión: cero detecciones. El local sí las distingue de bolsas de basura. Si está seguro y los verificadores ya confirmaron una pila (`recoleccion` o `retiro_muebles`), la API puede promover `retiro_escombros` marcada `reclasificado_por: "modelo_local"`. Si el local puntúa cerca de cero, es dato para reentrenar, no para el prompt. Se apaga con `FUSION_ESCOMBROS=0`.

El resto (veredas, vehículos, ocupación, plagas, luminaria) lo cubren sobre todo los de visión.

## Tecnología

El servicio está escrito en Python.

- API: FastAPI y uvicorn, en `servidor.py`. La página de demo va embebida en ese archivo, no hay frontend suelto.
- Modelo local: `model.joblib`. Embeddings CLIP + DINOv2 + SigLIP2, un scaler y un OneVsRest de regresión logística, más un regresor de gravedad. Corre en CPU. Dependencias: PyTorch, transformers, sentence-transformers, scikit-learn, joblib, Pillow.
- Verificación: `verificador.py` llama a OpenRouter. Los de visión van en paralelo. El árbitro es de texto porque DeepSeek no acepta imágenes en OpenRouter.
- Catálogos: `categorias.json` (las 44 propias) y `prestaciones.json` (el catálogo completo de la Ciudad, para mapear el texto del vecino).

## API

### `POST /clasificar`

`multipart/form-data` con el campo `file`. Campo opcional `contexto` (máx. 500 caracteres): lo que escribe quien reporta. Tiene peso propio. Los modelos lo usan para interpretar la foto, y dicen si la foto se corresponde con lo que el vecino contó. Si no se corresponde, lo visual se descarta y el reclamo se arma con el texto. El texto que enviás no se devuelve nunca (puede traer nombres o patentes). Parámetro opcional `verificar`: `auto` (default: verifica si hay clave), `1` (forzar), `0` (respuesta degradada, sin clasificación).

```bash
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" http://127.0.0.1:8080/clasificar
```

Respuesta, veredicto primero:

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

- `hay_problema`: hay al menos un problema confirmado. Siempre `hay_problema == bool(problemas)`. `gravedad_maxima` resume solo `problemas`.
- `hay_reclamo`: hay algo que el vecino quiere tramitar, esté confirmado o no: `hay_reclamo == bool(problemas or categorias_contexto)`. El caso `hay_reclamo: true, hay_problema: false` es "el texto pide algo pero la foto no lo confirma".
- `problemas`: lo que se reporta, gravedad 1-5, y `fuentes` (cuántas fuentes del consenso lo sostienen; hacen falta al menos 2). El clasificador interno participa del consenso pero su voto no se publica. Si la foto no corresponde al reclamo, acá va lo que pidió el vecino. La entrada puede traer `codigo` (una prestación del catálogo de la Ciudad) en lugar de `key`: leé `p.get("key") or p.get("codigo")`. Cada entrada trae `confianza` (`alta` = 3 o más fuentes, `media` = 2). Arriba va `predominante`: la clave del problema que domina la escena (mayor gravedad; a igual gravedad, más fuentes), o `null`.
- `patente`: en escenas de `vehiculo_mal_estacionado` o `vehiculo_abandonado` puede aparecer la chapa (formatos argentinos, p. ej. `AB123CD` o `ABC123`), arriba y copiada en el problema si quedó confirmado. Sale solo cuando dos lectores independientes leyeron la misma cadena en el vehículo protagonista. Cualquier lectura válida discrepante la suprime. Si hace falta, se relee la foto a mayor resolución solo para la chapa. Si no está a la vista o hay duda, el campo no viene.
- `foto_valida`: si la foto respalda lo que el vecino escribió. `true` = sirve como prueba; `false` = no muestra lo que reclama; `null` = no se pudo juzgar. `null` no quiere decir que la foto esté bien.
- `foto_valida_estado`: por qué `foto_valida` vale lo que vale. `corresponde` / `no_corresponde` van con `true` / `false`. Con `null` puede ser `sin_contexto`, `empate`, `sin_opinion` o `no_evaluado`. Solo `no_corresponde` descarta los hallazgos visuales.
- `posibles`: lo que podría ser un reporte y no está confirmado. Se devuelve siempre. Cada uno trae `origen`: `foto` (lo vio un solo modelo), `foto_no_relacionada` (se ve, pero no era el reclamo) o `contexto_vecinal` (lo sugiere el texto).
- `descartados_por_foto`: lo que la foto mostraba cuando `foto_valida` es `false`. No se reporta, pero vuelve con `motivo_descarte`.
- `descripcion`: la escena consolidada. La redacta el árbitro cuando interviene; si no, el verificador que mejor coincide con el resultado. Es `null` sin verificación.
- `categorias_contexto`: lo que el texto describe y la foto no confirma. No suman a `gravedad_maxima` ni a `hay_problema`; sí hacen `hay_reclamo: true`. Cada una trae `respaldo_visual`: `compatible`, `neutral` o `contradice`. Fuera de las 44 categorías propias, el reclamo puede mapear a cualquier prestación de [`prestaciones.json`](prestaciones.json): esas vienen con `codigo` en lugar de `key`.
- `elementos_detectados`: contenedores visibles, tengan o no problemas.
- `en_duda`: categorías con una sola fuente que el árbitro no decidió. Por default el árbitro no confirma lo de una sola fuente, así que eso vive en `posibles`. La excepción son los contenedores (`contenedor_*` de presencia): no pasan por el árbitro ni van a `posibles`, así que un contenedor que vio una sola fuente queda acá a propósito. Con dos fuentes pasa a `elementos_detectados`.
- `calidad_foto`: `lado_menor`, `nitidez`, `luminancia` y `definicion` (`buena` / `limitada`). Es informativo: no filtra y no cambia ninguna decisión. Sobre 200 fotos con etiqueta humana, la calidad global no predice los errores.
- `modelos`: lo que devolvió cada modelo de visión. El clasificador interno no aparece acá.

La fusión de escombros embolsados (arriba) corre solo con la verificación activa. Tiene dos niveles, uno confiado (interno ≥0.95 y recolección local ≤0.2) y uno de rescate (interno ≥0.70 y recolección local ≤0.1). En ambos hace falta una pila confirmada aparte. La poda confirmada veta los dos; el rechazo dirigido de un verificador ("no son escombros") también, salvo en el nivel confiado cuando la corroboración viene solo de `retiro_muebles`. Si se dispara y `recoleccion` estaba confirmada con score local bajo, esa entrada baja a `posibles`. Variables: `FUSION_ESCOMBROS`, `FUSION_ESCOMBROS_UMBRAL`, `FUSION_ESCOMBROS_RECO_BAJA`, `FUSION_ESCOMBROS_UMBRAL_RESCATE`, `FUSION_ESCOMBROS_RECO_RESCATE`.

### `POST /trabajos` y `GET /trabajos/{id}` (asíncrono, para lotes)

La vía sincrónica obliga a sostener la conexión los 25-60 s del análisis. Detrás de un proxy con techo de conexión (Cloudflare corta a los ~100 s) eso limita la cola. Para lotes, `POST /trabajos` recibe el mismo `multipart/form-data` que `/clasificar` y responde al instante:

```json
{ "trabajo": "kJ9vX2...", "estado": "en_cola", "posicion": 1 }
```

Después se consulta `GET /trabajos/{id}` (o `GET /trabajos?id=...`) hasta que el estado sea `listo` (trae `resultado`) o `error` (trae `detail`). Estados: `en_cola` (con `posicion`; 1 = el próximo), `procesando`, `listo`, `error`.

Un trabajo en cola se cancela con `DELETE /trabajos/{id}` (o `DELETE /trabajos?id=...`, o `POST /trabajos/cancelar?id=...`): devuelve `{"estado": "cancelado"}`. Uno que ya está `procesando` no se frena: responde `409`. Sobre un trabajo terminado, `DELETE` borra el registro.

Reglas:

- Si la foto ya está en caché, el `POST` devuelve `{"estado": "listo", "resultado": ...}` directo.
- Los pedidos sincrónicos tienen prioridad sobre los encolados.
- Techos: `TRABAJOS_MAX` pendientes (default 10, por encima `503`) y `TRABAJOS_POR_IP` (default 4, por encima `429`). El resultado se retiene `TRABAJO_TTL` segundos (default 1800). Un trabajo puede esperar su turno hasta `TRABAJO_ESPERA` segundos (default 900).
- Los trabajos viven en memoria del proceso: un reinicio los pierde. Un `404` al consultar significa desconocido o vencido: el cliente reenvía la foto.
- El `POST` pasa por las mismas guardas que `/clasificar`. Consultar el estado no consume cuota.

La portada (`GET /`) usa esta vía.

### `GET /salud`

Estado del servicio: clases del modelo, si la verificación está activa y con qué modelos.

## Configuración

Todo por variables de entorno o `.env` (ver [`.env.example`](.env.example)):

| Variable | Default | Qué hace |
|---|---|---|
| `OPENROUTER_API_KEY` | vacía | Habilita la verificación cruzada. Nunca la commitees. |
| `VERIFICADORES` | tres modelos (ver `.env.example`) | Modelos de visión, separados por coma. Podés poner uno, dos, tres o los que quieras: se confirma con ≥2 fuentes y el local cuenta como una. Las pasadas dirigidas agregan llamadas extra solo cuando se disparan. La repregunta entre modelos corre con 3 o más verificadores. |
| `ARBITRO` | `deepseek/deepseek-v4-flash` | Modelo que resuelve desacuerdos. Vacío = sin árbitro. Puede ser de texto o con visión. |
| `ARBITRO_VE_FOTO` | apagado | Si el árbitro tiene visión, le pasa también la foto. |
| `ARBITRO_CONFIRMA` | apagado | Si el árbitro puede promover a confirmado lo de una sola fuente. Apagado por medición. |
| `UMBRAL` | `0.5` | Probabilidad mínima del modelo local para proponer una categoría. |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Dónde escucha la API. |
| `VERIFICADOR_TIMEOUT` | `120` | Segundos por llamada a OpenRouter. |
| `VERIFICADOR_DEADLINE` | `180` | Techo total de reintentos por modelo. |

Los modelos de DeepSeek en OpenRouter no aceptan imágenes, por eso participa como árbitro de texto.

### Límites si publicás la API

Clasificar una foto cuesta 25-60 s de CPU y una llamada paga a OpenRouter por verificador (tres por defecto), más el árbitro si hay disputa. `/clasificar` viene con techos de fábrica:

| Variable | Default | Qué hace |
|---|---|---|
| `MAX_BYTES` | `10485760` (10 MB) | Tamaño máximo del upload; más grande devuelve `413`. |
| `MAX_PIXELES` | `25000000` | Megapíxeles máximos; frena bombas de descompresión con `400`. |
| `CONCURRENCIA` | `1` | Clasificaciones en paralelo; por encima devuelve `503`. |
| `RATE_LIMITE` / `RATE_VENTANA` | `60` / `3600` | Pedidos por IP y ventana en segundos; por encima `429` con `Retry-After`. `0` desactiva. |
| `CUOTA_DIARIA` | `500` | Techo global de fotos verificadas por día. Pasado el techo responde degradada. `0` desactiva. |
| `API_TOKEN` | vacío | Si lo ponés, `POST /clasificar` exige el header `X-Api-Token`. |
| `CACHE_MAX` | `128` | Respuestas cacheadas por hash de foto. |
| `CONFIAR_PROXY` | apagado | Hace que el límite por IP use `X-Forwarded-For`. |

Si la publicás en internet:

- Detrás de un proxy, activá `CONFIAR_PROXY` y hacé que el proxy pise el `X-Forwarded-For` que manda el cliente. Sin `CONFIAR_PROXY`, todos llegan como `127.0.0.1` y comparten una sola cuota. Con `CONFIAR_PROXY` pero sin pisar el header, cualquiera rota el header y se saltea el límite. Sin proxy, dejalo apagado.
- Poné un límite de tamaño de cuerpo en el proxy (`client_max_body_size` en nginx).
- Poné un tope de gasto mensual en la clave de OpenRouter, con una clave dedicada a este servicio. `CUOTA_DIARIA` es por proceso y se reinicia con el servicio.
- `multipart/form-data` no dispara preflight de CORS: cualquier página puede pegar contra tu endpoint. El límite por IP y el token son lo que lo frena.

El límite por IP y el de concurrencia viven en memoria del proceso. Para varias instancias hay que llevarlos al proxy o a un store compartido.

### El texto del vecino manda sobre la foto

| Situación | Resultado |
|---|---|
| Hay contexto y la foto lo respalda | Se reporta lo de la foto. `foto_valida: true` |
| Hay contexto y la foto no lo respalda | Se reporta lo que pidió el vecino. Lo de la foto pasa a `descartados_por_foto`. `foto_valida: false` |
| Hay contexto y no mapea a nada del catálogo | `hay_problema: false`. No se inventa un reporte |
| No hay contexto | Se reporta lo de la foto. `foto_valida: null` |

Cuando el reclamo se encamina desde el texto y es ambiguo, va la categoría genérica: "mi cuadra está llena de basura" es `recoleccion`, no `retiro_muebles` ni `retiro_escombros`.

### Lo que vio una sola fuente no se afirma

Por default el árbitro no promueve a confirmado lo que reportó una sola fuente: sale en `posibles`. Se probaron cuatro modelos de árbitro y dieron 2 rescates correctos sobre 21 confirmaciones. Se puede volver al comportamiento anterior con `ARBITRO_CONFIRMA=1`.

### El contenedor que no está

Un contenedor publicado es un dato duro, así que inventarlo es caro. Hay dos vetos de presencia, los dos por mirada dirigida y los dos con mayoría:

- Ninguno: los verificadores confirman un contenedor y el clasificador interno está en el piso para todas las claves de contenedor. Se pregunta si hay alguno; mayoría de "ausente" y la presencia baja a `en_duda`. El contenedor municipal es ancho (unos dos metros); un tacho angosto y vertical no lo es.
- Ese: el contenedor fantasma que se publica al lado de uno real (la bolsa verde leída como contenedor de reciclables). Corre por clave, y solo donde el interno es detector confiable: `contenedor_secos` y el bilateral. Para el lateral no se aplica: es el tipo más común y el que el interno más se pierde. Cuando se veta, la prosa pierde solo las frases de ese contenedor.

### Sobre el contexto y la inyección de prompt

El `contexto` que escribe quien sube la foto, y cualquier texto que aparezca dentro de la foto, llegan a los modelos. Se tratan como datos no confiables:

- La rúbrica viaja en un mensaje `system`; los datos del usuario van en el `user`. Lo mismo para el árbitro.
- Una categoría se reporta por los objetos que se ven. Un texto dirigido a quien analiza no es evidencia.
- Las descripciones vuelven acotadas y sin caracteres de control.

Los verificadores miran la misma foto con el mismo prompt, así que no son fuentes independientes: una inyección que funcione en dos de ellos alcanza para el consenso. Existe `CONSENSO_VLM_SOLO=arbitro`, que manda esas categorías al árbitro, pero no es el default: un eval no pudo demostrar que sirviera. Ver [`verificador.py`](verificador.py) y [`eval/`](eval/).

`descripcion` es texto generado por un modelo e influido por quien sube la foto: escapalo antes de renderizarlo como HTML y no abras reportes automáticos sin revisión humana.

## Si el proveedor se cuelga

Las llamadas a OpenRouter tienen un tope de reloj absoluto, no por operación de socket. `urllib` reinicia su timeout con cada byte, así que un proveedor que manda keepalives mientras el modelo genera puede dejar un hilo esperando para siempre. Cuando vence el tope se hace `shutdown()` del socket.

Esto ya pasó: un hilo quedó tomado y, con `CONCURRENCIA=1`, todo `/clasificar` devolvió 503 hasta reiniciar, mientras `/salud` seguía en 200.

Como red de seguridad hay un `TECHO_TRABAJO` (default 600 s): pasado ese tiempo el trabajo se da por perdido y se devuelve su cupo. El hilo sigue vivo (a un hilo de Python no se lo puede matar) pero el servicio se recupera solo.

## Privacidad

El modelo local no envía nada a ningún lado. Con la verificación activa, la foto (reducida a 1024px) se envía a los modelos configurados a través de OpenRouter; revisá sus políticas de datos antes de usarla con fotos sensibles.

El `contexto` del vecino entra a los modelos pero no vuelve en la respuesta. Excepción: en reportes de vehículos la respuesta puede incluir la `patente` leída de la foto, que es el dato que pide el trámite. Sale de la chapa visible, nunca del texto del vecino. Las descripciones de los modelos de visión son parte de la respuesta y pueden transcribir texto visible en la foto. Si eso importa, no persistas las respuestas.

## Licencia

[MIT](LICENSE)
