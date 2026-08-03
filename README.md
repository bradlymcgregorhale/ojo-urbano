# Ojo Urbano

API local para clasificar fotos de incidencias urbanas: residuos en la vía pública, escombros, muebles abandonados, contenedores y cestos en mal estado, baches, veredas rotas, vehículos abandonados o mal estacionados, plagas, poda, volquetes y más (44 categorías canónicas, ver [`categorias.json`](categorias.json)).

Combina dos capas:

1. **Modelo propio, gratis y local.** Embeddings de imagen (CLIP + DINOv2 + SigLIP2, todos open source) con un cabezal de regresión logística multi-etiqueta entrenado con miles de fotos callejeras reales etiquetadas a mano, más un regresor de gravedad (1 a 5). Corre 100% en tu máquina, sin ninguna API paga. Las clases sinónimas del modelo se pliegan a una categoría canónica (por ejemplo, todo objeto voluminoso descartado sale como `retiro_muebles`).
2. **Verificación cruzada por IA (opcional).** Con una clave de [OpenRouter](https://openrouter.ai), dos modelos de visión (por defecto **GPT-5 mini** y **Gemini Flash Lite**, elegidos por bake-off contra fotos reales etiquetadas) analizan la foto de forma independiente siguiendo una rúbrica detallada por categoría, calibrada contra fotos reales. Una categoría queda confirmada cuando la reportan al menos 2 de las 3 fuentes; las que tienen una sola fuente van a un **árbitro** de texto (por defecto **DeepSeek**) que lee los veredictos y las probabilidades del modelo local y decide. El subtipo de contenedor de húmedos (lateral vs bilateral) siempre lo decide el modelo local, que es más preciso ahí que los modelos de visión. El costo por foto es de fracciones de centavo.

   Algunas categorías (por ejemplo `vehiculo_mal_estacionado` o `columna_poste_cable`) no existen en el modelo local: las detectan solo los modelos de visión, y quedan confirmadas cuando ambos las reportan (2 de 3 fuentes). Con un solo reporte van al árbitro, como cualquier otra disputa.

   Cada verificador devuelve además una **descripción** breve de la foto dentro de su misma respuesta, y la API entrega en `descripcion` una descripción consolidada que respalda las categorías confirmadas: la redacta el árbitro cuando ya interviene por una disputa, y si no hay disputa se elige la descripción del verificador que más coincide con el resultado final. Todo sin llamadas extra: el conteo de llamadas por foto no cambia.

## Instalación

```bash
git clone https://github.com/bradlymcgregorhale/ojo-urbano.git
cd ojo-urbano
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completar OPENROUTER_API_KEY (opcional)
python servidor.py
```

La primera ejecución descarga los modelos de embeddings (varios GB, una sola vez). Después abrí http://127.0.0.1:8080 y arrastrá una foto.

> Sin `OPENROUTER_API_KEY` todo funciona igual, solo que la respuesta se basa únicamente en el modelo local (sin verificación).

## API

### `POST /clasificar`

`multipart/form-data` con el campo `file`. Campo opcional `contexto` ("contexto vecinal"): texto de quien reporta, que los modelos de visión y el árbitro usan como pista para interpretar la foto; nunca como evidencia (se reporta solo lo que la foto muestra, máx. 500 caracteres). Las categorías que el contexto describe pero la foto no confirma vuelven aparte en `categorias_contexto` (por ejemplo, "hay ratas por todos lados" devuelve `desratizacion` ahí aunque no se vea ninguna rata); no cuentan para `hay_problema` ni `gravedad_maxima`. Parámetro opcional `verificar`: `auto` (default: verifica si hay clave), `1` (forzar), `0` (solo modelo local).

```bash
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" http://127.0.0.1:8080/clasificar
```

Respuesta: el veredicto primero, el detalle técnico adentro de `detalle`.

```json
{
  "hay_problema": true,
  "gravedad_maxima": 3,
  "problemas": [
    { "key": "recoleccion", "nombre": "Recolección de residuos", "gravedad": 3,
      "fuentes": ["modelo_local", "openai/gpt-5-mini", "google/gemini-3.5-flash-lite"] }
  ],
  "descripcion": "Bolsas de residuos y cajas de cartón acumuladas en la vereda junto a un contenedor negro de húmedos.",
  "categorias_contexto": [
    { "key": "desratizacion", "nombre": "Desratización / control de plagas en la vía pública",
      "respaldo_visual": "neutral" }
  ],
  "elementos_detectados": [
    { "key": "contenedor_humedos_lateral", "nombre": "Contenedor de húmedos, carga lateral" }
  ],
  "en_duda": [],
  "detalle": {
    "modelo_local": { "predichas": [ ... ], "top5": [ ... ], "probabilidades": [ ... ],
                      "gravedad": { "value": 3, "raw": 3.2 } },
    "verificacion": { "activa": true, "contexto": "...",
                      "verificadores": [ { "modelo": "...", "categorias": [ ... ], "descripcion": "..." } ],
                      "arbitro": { "decisiones": [ ... ], "descripcion": "..." },
                      "descripcion_fuente": "deepseek/deepseek-v4-flash" }
  }
}
```

- `hay_problema`: el veredicto en un booleano.
- `problemas`: qué se confirmó, con gravedad 1-5 y las `fuentes` que lo vieron (modelo local y/o modelos de visión). Una foto puede tener varios problemas.
- `descripcion`: la descripción consolidada de la escena (la redacta el árbitro cuando interviene; si no, el verificador que mejor coincide con el resultado). Es `null` sin verificación.
- `categorias_contexto`: lo que el contexto vecinal describe pero la foto no confirma (sugerencias, no cuentan para `hay_problema` ni `gravedad_maxima`). Cada una trae `respaldo_visual`: `compatible` (la escena encaja con el reclamo sin llegar a confirmarlo: una foto nocturna oscura para "no funciona la luminaria"), `neutral` (la foto no muestra nada al respecto) o `contradice`. Un consumidor que quiera deferir al vecino puede tomar las `compatible` como reportables. Además de las 43 categorías propias (`key`), el reclamo puede mapear a **cualquier prestación del catálogo completo de la Ciudad** ([`prestaciones.json`](prestaciones.json), 453 tipos con el texto de su página oficial): esas entradas vienen con `codigo` en lugar de `key`, por ejemplo `{"codigo": "1441632738519", "nombre": "Reparación de semáforo", "respaldo_visual": "compatible"}`.
- `elementos_detectados`: contenedores visibles en la foto, tengan o no problemas.
- `en_duda`: categorías con una sola fuente que el árbitro no llegó a decidir.
- `detalle`: todo lo interno (probabilidades del modelo local, veredicto y descripción de cada modelo de visión, decisiones del árbitro) para quien quiera profundizar.

### `GET /salud`

Estado del servicio: clases del modelo, si la verificación está activa y con qué modelos.

## Configuración

Todo por variables de entorno o `.env` (ver [`.env.example`](.env.example)):

| Variable | Default | Qué hace |
|---|---|---|
| `OPENROUTER_API_KEY` | vacía | Habilita la verificación cruzada. **Nunca la comitees.** |
| `VERIFICADORES` | `openai/gpt-5-mini,google/gemini-3.5-flash-lite` | Modelos de visión (cualquier modelo de OpenRouter con soporte de imagen). |
| `ARBITRO` | `deepseek/deepseek-v4-flash` | Modelo de texto que resuelve desacuerdos. Vacío = sin árbitro (las disputas quedan `en_duda`). |
| `UMBRAL` | `0.5` | Probabilidad mínima del modelo local para proponer una categoría. |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Dónde escucha la API. |
| `VERIFICADOR_TIMEOUT` | `120` | Segundos por llamada a OpenRouter. |
| `VERIFICADOR_DEADLINE` | `180` | Techo total de reintentos por modelo. |

Nota: los modelos de DeepSeek en OpenRouter no aceptan imágenes, por eso participa como árbitro de texto y no como verificador visual.

### Límites de abuso

Clasificar una foto cuesta 25-60 s de CPU y 2-3 llamadas pagas a OpenRouter, así que `/clasificar` viene con techos puestos de fábrica:

| Variable | Default | Qué hace |
|---|---|---|
| `MAX_BYTES` | `10485760` (10 MB) | Tamaño máximo del upload; más grande devuelve `413`. |
| `MAX_PIXELES` | `25000000` | Megapíxeles máximos; frena bombas de descompresión con `400`. |
| `CONCURRENCIA` | `1` | Clasificaciones en paralelo; por encima devuelve `503`. |
| `RATE_LIMITE` / `RATE_VENTANA` | `60` / `3600` | Pedidos por IP y ventana en segundos; por encima devuelve `429`. `0` desactiva. |
| `CUOTA_DIARIA` | `500` | Techo global de fotos verificadas por día. Pasado el techo la API sigue respondiendo, pero solo con el modelo local. `0` desactiva. |
| `API_TOKEN` | vacío | Si lo ponés, `POST /clasificar` exige el header `X-Api-Token`. |
| `CACHE_MAX` | `128` | Respuestas cacheadas por hash de foto, para no pagar dos veces la misma. |
| `CONFIAR_PROXY` | apagado | Hace que el límite por IP use `X-Forwarded-For`. |

Si publicás la API en internet, además de esto:

- **Detrás de un proxy, activá `CONFIAR_PROXY` y hacé que el proxy PISE el `X-Forwarded-For` que manda el cliente.** Las dos mitades importan. Sin `CONFIAR_PROXY`, todos los visitantes llegan como `127.0.0.1` y comparten una sola cuota: uno solo se la agota y deja afuera a todos los demás. Con `CONFIAR_PROXY` pero sin pisar el header, cualquiera rota su `X-Forwarded-For` y se saltea el límite. Sin proxy adelante, dejalo apagado.
- Poné un límite de tamaño de cuerpo en el proxy (`client_max_body_size` en nginx). La app exige `Content-Length` y lo rechaza por encima del techo, pero el servidor de adelante es el que evita que el cuerpo entero llegue a viajar.
- Poné un tope de gasto mensual en la clave de OpenRouter, con una clave dedicada a este servicio. `CUOTA_DIARIA` acota el gasto del lado de la app, pero es por proceso y se reinicia con el servicio.
- Tené en cuenta que `multipart/form-data` no dispara preflight de CORS: cualquier página puede hacer que el navegador de sus visitantes pegue contra tu endpoint. El límite por IP y el token son lo que lo frena.

El límite por IP y el de concurrencia viven en memoria del proceso: son por instancia y se reinician con el servicio. Para varias instancias hace falta llevarlos al proxy o a un store compartido.

### Sobre el contexto vecinal y la inyección de prompt

El `contexto` que escribe quien sube la foto, y cualquier texto que aparezca *dentro* de la foto, llegan a los modelos de visión. Son datos no confiables, y se tratan como tales:

- La rúbrica viaja en un mensaje `system` aparte; los datos del usuario van en el `user`. Lo mismo para el árbitro.
- Los dos verificadores **no cuentan como fuentes independientes**: miran la misma foto con el mismo prompt, así que una sola inyección que funcione en ambos alcanzaría para el "2 de 3". Por eso una categoría sin respaldo del modelo local no se confirma por consenso entre ellos: la decide el árbitro, que solo ve texto y tiene su propia consigna (`CONSENSO_VLM_SOLO`).
- Las descripciones y evidencias vuelven acotadas y sin caracteres de control.

Nada de esto es una barrera dura: un LLM no tiene un límite real entre instrucciones y datos. `descripcion` es texto generado por un modelo e influido por quien sube la foto, así que **escapalo antes de renderizarlo como HTML** y no abras reportes automáticos sin revisión humana.

## Ejemplo

```bash
python ejemplo.py foto.jpg
```

## Privacidad

El modelo local no envía nada a ningún lado. Con la verificación activa, la foto (reducida a 1024px) se envía a los modelos configurados a través de OpenRouter; revisá sus políticas de datos antes de usarla con fotos sensibles.

## Licencia

[MIT](LICENSE)
