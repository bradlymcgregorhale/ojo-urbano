# Ojo Urbano

API local para clasificar fotos de incidencias urbanas: residuos en la vía pública, escombros, muebles abandonados, contenedores y cestos en mal estado, baches, veredas rotas, vehículos abandonados o mal estacionados, plagas, poda, volquetes y más (44 categorías canónicas, ver [`categorias.json`](categorias.json)).

Combina dos capas:

1. **Modelo propio, gratis y local.** Embeddings de imagen (CLIP + DINOv2 + SigLIP2, todos open source) con un cabezal de regresión logística multi-etiqueta entrenado con miles de fotos callejeras reales etiquetadas a mano, más un regresor de gravedad (1 a 5). Corre 100% en tu máquina, sin ninguna API paga. Las clases sinónimas del modelo se pliegan a una categoría canónica (por ejemplo, todo objeto voluminoso descartado sale como `retiro_muebles`).
2. **Verificación cruzada por IA (opcional).** Con una clave de [OpenRouter](https://openrouter.ai), varios modelos de visión (por defecto **tres**: GPT-5 mini, Gemini Flash Lite y GPT-5.6 luna) analizan la foto siguiendo una rúbrica detallada por categoría, calibrada contra fotos reales. Una categoría queda confirmada cuando la reportan **al menos 2 fuentes** (el modelo local cuenta como una, aunque su voto individual no se publica: ver "Cambios de contrato", v4). Lo que ve una sola fuente **no se confirma**: vuelve en `posibles`, para que el consumidor repregunte en vez de reportar algo dudoso como un hecho. Un **árbitro** de texto (por defecto **DeepSeek**) igual lo revisa y deja su veredicto y su motivo en la respuesta; con `ARBITRO_CONFIRMA=1` puede volver a promover esos hallazgos a `problemas`, pero no es el default y el eval no pudo mostrar que mejore. El subtipo de contenedor de húmedos (lateral vs bilateral) siempre lo decide el modelo local, que es más preciso ahí que los modelos de visión. El costo por foto es de fracciones de centavo.

   Algunas categorías (por ejemplo `vehiculo_mal_estacionado` o `columna_poste_cable`) no existen en el modelo local: las detectan solo los modelos de visión, y quedan confirmadas cuando al menos dos coinciden. Con `CONSENSO_VLM_SOLO=arbitro` esas pasan por el árbitro en vez de confirmarse solas; **no es el default**, ver [`verificador.py`](verificador.py) para por qué (un eval sobre fotos reales de reportes no pudo demostrar el beneficio ni descartar el costo; ver [`eval/`](eval/)).

   Cada verificador devuelve además una **descripción** breve de la foto dentro de su misma respuesta, y la API entrega en `descripcion` una descripción consolidada que respalda las categorías confirmadas: la redacta el árbitro cuando ya interviene por una disputa, y si no hay disputa se elige la descripción del verificador que más coincide con el resultado final. Normalmente no agrega llamadas: sale de las respuestas que ya se pidieron. Las llamadas extra del sistema son tres, y ninguna es fija: la del árbitro cuando hay una disputa que resolver; la del encaminamiento por texto cuando la foto no corresponde al reclamo; y una al árbitro para rehacer la descripción en el caso raro de que todas las disponibles contradigan un subtipo ya resuelto (con `ARBITRO_VE_FOTO` esa última va con la foto adjunta, y cuesta más).

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

> Sin `OPENROUTER_API_KEY` el servicio responde, pero **degradado**: sin verificación cruzada no hay clasificación pública (`problemas: []`, `verificacion_activa: false` con motivo). Desde v4 las predicciones del clasificador interno no se publican solas.

## API

### `POST /clasificar`

`multipart/form-data` con el campo `file`. Campo opcional `contexto` ("contexto vecinal", máx. 500 caracteres): lo que escribe quien reporta. **Tiene peso propio, no es solo una pista.** Los modelos lo usan para interpretar la foto, y además dicen si la foto se corresponde con lo que el vecino contó. Si no se corresponde, lo visual se descarta y el reclamo se arma con el texto (ver `foto_valida` más abajo). Las categorías que el contexto describe pero la foto no confirma vuelven aparte en `categorias_contexto` (por ejemplo, "hay ratas por todos lados" devuelve `desratizacion` ahí aunque no se vea ninguna rata): no suman a `gravedad_maxima` ni a `hay_problema`, pero **sí** hacen `hay_reclamo: true`. El texto que enviás **no se devuelve nunca** en la respuesta (puede traer PII: nombres, patentes). Parámetro opcional `verificar`: `auto` (default: verifica si hay clave), `1` (forzar), `0` (sin verificación: respuesta degradada, sin clasificación).

```bash
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" http://127.0.0.1:8080/clasificar
```

Respuesta: el veredicto primero, y lo que dijo cada modelo de visión en `modelos`.

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

- `hay_problema`: hay al menos un problema **confirmado**. Invariante del contrato: `hay_problema == bool(problemas)`, siempre. `gravedad_maxima` resume solo `problemas`.
- `hay_reclamo`: hay algo que el vecino quiere tramitar, esté confirmado o no: `hay_reclamo == bool(problemas or categorias_contexto)`. El caso `hay_reclamo: true, hay_problema: false` es exactamente "el texto pide algo pero la foto no lo confirma"; una interfaz que quiera deferir al vecino usa este campo junto con `categorias_contexto`.
- `problemas`: lo que se reporta, con gravedad 1-5 y `fuentes`: **cuántas** fuentes del consenso lo sostienen (un conteo; hacen falta al menos 2). El clasificador interno participa del consenso como una fuente más, pero su voto individual no se publica. Si la foto no corresponde al reclamo, acá va lo que pidió el vecino (una sola fuente: su texto). La entrada puede traer `codigo` (una prestación del catálogo completo de la Ciudad) **en lugar de** `key`: leé `p.get("key") or p.get("codigo")`, nunca `p["key"]` a secas. Cuando la escena apunta a `vehiculo_mal_estacionado` o `vehiculo_abandonado` — porque algún modelo de visión lo reporta, el `contexto` del vecino lo señala, o el clasificador interno lo sospecha; confirmado o no: el dato sirve aunque la infracción no se confirme desde la foto — la respuesta puede traer la patente del vehículo: top-level `patente` (formatos argentinos, p. ej. `AB123CD` o `ABC123`), y además copiada en la entrada de `problemas` si el problema quedó confirmado. Aparece **solo** cuando dos lectores independientes leyeron la misma cadena en la chapa del vehículo protagonista y la lectura matchea un formato completo — cualquier lectura válida discrepante, aunque pierda 2 a 1, la suprime. Si hace falta, el sistema relee la foto a mayor resolución solo para la chapa (dos llamadas extra, únicamente en fotos con vehículo reportado). Si la patente no está a la vista o hay cualquier duda, el campo directamente no viene. Campos aditivos, sin cambio de contrato.
- `foto_valida`: si la foto respalda lo que el vecino escribió. `true` = sirve como prueba; `false` = no muestra lo que reclama (el consumidor debería pedirle otra); `null` = no se pudo juzgar. **`null` no quiere decir "la foto está bien".**
- `foto_valida_estado`: por qué `foto_valida` vale lo que vale, para no tener que adivinarlo. `corresponde` / `no_corresponde` acompañan a `true` / `false`; con `null` puede ser `sin_contexto` (el vecino no escribió nada, no había con qué comparar), `empate` (los verificadores se dividieron), `sin_opinion` (contestaron pero no se pronunciaron) o `no_evaluado` (la verificación no corrió: sin clave, desactivada o cuota agotada). Solo `no_corresponde` descarta los hallazgos visuales.
- `posibles`: lo que **podría** ser un reporte pero no está confirmado. Se devuelve siempre, incluso cuando no hay nada definitivo, que es justo cuando más sirve: permite repreguntarle al vecino en vez de devolverle una respuesta vacía. Cada uno trae `origen`:
  - `foto`: lo vio un solo modelo, no alcanza para confirmarlo.
  - `foto_no_relacionada`: se ve de verdad, pero la foto no era del reclamo, así que no es lo que el vecino pidió.
  - `contexto_vecinal`: lo sugiere el texto y la foto no lo confirma.
- `descartados_por_foto`: lo que la foto mostraba cuando `foto_valida` es `false`. No se reporta (el vecino vino a hablar de otra cosa) pero se devuelve con `motivo_descarte` para que se entienda por qué no se abrió nada.
- `descripcion`: la descripción consolidada de la escena (la redacta el árbitro cuando interviene; si no, el verificador que mejor coincide con el resultado). Es `null` sin verificación.
- `categorias_contexto`: lo que el contexto vecinal describe pero la foto no confirma (sugerencias: no suman a `gravedad_maxima` ni a `hay_problema`, pero hacen `hay_reclamo: true`). Cada una trae `respaldo_visual`: `compatible` (la escena encaja con el reclamo sin llegar a confirmarlo: una foto nocturna oscura para "no funciona la luminaria"), `neutral` (la foto no muestra nada al respecto) o `contradice`. Un consumidor que quiera deferir al vecino puede tomar las `compatible` como reportables. Además de las 44 categorías propias (`key`), el reclamo puede mapear a **cualquier prestación del catálogo completo de la Ciudad** ([`prestaciones.json`](prestaciones.json), 453 tipos con el texto de su página oficial): esas entradas vienen con `codigo` en lugar de `key`, por ejemplo `{"codigo": "1441632738519", "nombre": "Reparación de semáforo", "respaldo_visual": "compatible"}`.
- `elementos_detectados`: contenedores visibles en la foto, tengan o no problemas.
- `en_duda`: categorías con una sola fuente que el árbitro no llegó a decidir. Por default el árbitro **no confirma** lo que vio una sola fuente (ver más abajo), así que lo de una sola fuente vive en `posibles`. La excepción son los contenedores (`contenedor_*` de presencia): esos no pasan por el árbitro ni viajan en `posibles`, así que un contenedor que vio una sola fuente queda acá **por diseño** — es su estado final, no un arbitraje incompleto. Con dos fuentes pasa a `elementos_detectados`.
- `modelos`: lo que devolvió **cada modelo de visión**: sus categorías con evidencia, su descripción de la escena, si vio la foto acorde al reclamo. Es todo el "cómo" que se publica: el clasificador interno no aparece acá (participa del consenso, su voto no se expone) y no existe ningún parámetro que devuelva más internals.

### `GET /salud`

Estado del servicio: clases del modelo, si la verificación está activa y con qué modelos.

## Si el proveedor se cuelga

Las llamadas a OpenRouter tienen un tope de reloj **absoluto**, no por operación de socket. La diferencia importa: `urllib` reinicia su timeout con cada byte que llega, así que un proveedor que manda keepalives mientras el modelo genera puede tener un hilo esperando para siempre sin que salte ningún timeout. Cuando vence el tope se hace `shutdown()` del socket, que es lo único que desatasca una lectura bloqueada (`close()` desde otro hilo puede quedarse esperando el lock del buffer).

Esto ya pasó en producción: un hilo quedó tomado sobre un socket abierto y, como el cupo de concurrencia lo suelta el hilo al terminar, con `CONCURRENCIA=1` todo `/clasificar` devolvió 503 hasta reiniciar, mientras `/salud` seguía contestando 200.

Como red de seguridad hay además un `TECHO_TRABAJO` (default 600 s): pasado ese tiempo el trabajo se da por perdido y se devuelve su cupo. El hilo sigue vivo (a un hilo de Python no se lo puede matar) pero el servicio se recupera solo. Hace falta porque no todo se puede interrumpir desde afuera: la resolución DNS ocurre adentro de la conexión y no hay forma de cortarla.

## Cambios de contrato

La respuesta trae `version`. Se sube cuando cambia el **significado** de un campo que ya existía, o cuando deja de venir por default.

### v4: el modelo local deja de ser visible

El clasificador interno sigue corriendo y sigue contando como una fuente del consenso, pero **su voto individual ya no se publica por ninguna vía**: sus números eran ruido para el consumidor (llegó a afirmar `situacion_calle` a 0.9945 sobre una vereda vacía que los tres modelos de visión leyeron bien). Lo visible es lo que devolvió cada modelo de visión y el resultado final.

| Antes (v3) | Ahora (v4) |
|---|---|
| `?detalle=1` devolvía el volcado interno completo | **no existe más.** No hay parámetro ni variable de entorno que devuelva los internals; para diagnóstico están los logs del operador |
| `fuentes`: lista de nombres (`["modelo_local", "openai/..."]`) en `problemas`/`posibles`, número en `categorias_contexto` | **siempre un conteo.** Cuántas fuentes sostienen la entrada, sin nombres |
| `posibles` traía entradas que solo vio el modelo local | se filtran: si nadie más la vio, no se publica |
| `hay_problema`: `true` también por reclamos solo-texto | **`hay_problema == bool(problemas)`**, siempre. Lo solo-texto vive en `hay_reclamo` (nuevo): `hay_reclamo == bool(problemas or categorias_contexto)` |
| `detalle.verificacion.contexto` devolvía eco del texto del vecino | el contexto **no se devuelve nunca** (PII: nombres, patentes, firmas) |
| `?verificar=0` publicaba las predicciones del modelo local como `problemas` | responde **degradado**: `problemas: []`, `verificacion_activa: false` con motivo. Sin verificación no hay clasificación pública |
| motivos del árbitro podían decir "solo el modelo local la reporta" | hablan de evidencia visual, nunca del mecanismo interno; el serializador reemplaza cualquier motivo que nombre modelos o scores |

Campo nuevo en `modelos[]`: `foto_corresponde` (si ese verificador consideró la foto acorde al reclamo).

### v3: la respuesta viene resumida

La respuesta traía el ranking completo del modelo local (29 categorías con su score, casi todas en 0.00x) y un `detalle.verificacion` que repetía seis campos que ya estaban arriba. Una respuesta típica pasó de ~9 KB a ~3 KB.

| Antes (v2) | Ahora (v3) |
|---|---|
| `detalle.modelo_local` con `predichas`, `top5` y `probabilidades` | no viene; el modelo local sigue apareciendo como `"modelo_local"` en las `fuentes` de cada categoría |
| `detalle.verificacion.verificadores` | `modelos`, en la raíz, con `modelo`, `ok`, `sin_problema`, `categorias` (`key`, `gravedad`, `evidencia`) y `descripcion` |
| `detalle.verificacion.activa` / `.motivo` | `verificacion_activa` y, si está apagada, `verificacion_motivo` |
| `detalle.verificacion.arbitro` | no viene; el veredicto y el motivo del árbitro para cada hallazgo dudoso ya están dentro de `posibles[]` |

**En v3 nada se perdió:** `POST /clasificar?detalle=1` devolvía exactamente el volcado de v2, con `detalle` entero. **v4 eliminó esa escotilla** (ver arriba).

### v2

Esto es lo que cambió en **v2** y hay que revisar antes de actualizar:

| Campo | Antes (v1) | Ahora (v2) |
|---|---|---|
| `hay_problema` | solo si la **foto** mostraba algo reportable | también `true` si lo sostiene solo el texto del vecino. Un consumidor que lo leía como "la foto tiene un problema" ahora se equivoca; para eso está `problemas[].fuentes` |
| `problemas` | siempre hallazgos visuales | puede traer entradas con `fuentes: ["contexto_vecinal"]`, sin ninguna fuente visual, cuando la foto no corresponde al reclamo |
| `fuentes` | nombres de modelos (`modelo_local`, `openai/...`) | se agrega el valor literal `"contexto_vecinal"`, que no es un modelo. Un parser que asumía "toda fuente es un modelo" hay que ajustarlo |
| árbitro | podía **confirmar** un hallazgo de una sola fuente y meterlo en `problemas` | ya no: lo de una sola fuente va a `posibles`. Se puede volver al comportamiento anterior con `ARBITRO_CONFIRMA=1`, pero el eval no pudo mostrar que mejore |
| verificadores por defecto | 2 modelos | 3 modelos (más costo por foto). Se fija con `VERIFICADORES` |

Campos **nuevos** en v2, que no rompen nada si los ignorás: `foto_valida`, `foto_valida_estado`, `descartados_por_foto`, `posibles`.

## Configuración

Todo por variables de entorno o `.env` (ver [`.env.example`](.env.example)):

| Variable | Default | Qué hace |
|---|---|---|
| `OPENROUTER_API_KEY` | vacía | Habilita la verificación cruzada. **Nunca la comitees.** |
| `VERIFICADORES` | tres modelos (ver `.env.example`) | Modelos de visión, separados por coma. Podés poner **uno, dos, tres o los que quieras**: la regla es siempre la misma (una categoría se confirma con ≥2 fuentes y el modelo local cuenta como una). Más modelos = más recall y más costo. |
| `ARBITRO` | `deepseek/deepseek-v4-flash` | Modelo que resuelve desacuerdos. Vacío = sin árbitro. Puede ser de texto o con visión. |
| `ARBITRO_VE_FOTO` | apagado | Si el árbitro tiene visión, le pasa también la foto en vez de hacerlo decidir sobre las descripciones ajenas. |
| `ARBITRO_CONFIRMA` | apagado | Si el árbitro puede promover a confirmado lo de una sola fuente. Apagado por medición (ver arriba). |
| `UMBRAL` | `0.5` | Probabilidad mínima del modelo local para proponer una categoría. |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Dónde escucha la API. |
| `VERIFICADOR_TIMEOUT` | `120` | Segundos por llamada a OpenRouter. |
| `VERIFICADOR_DEADLINE` | `180` | Techo total de reintentos por modelo. |

Nota: los modelos de DeepSeek en OpenRouter no aceptan imágenes, por eso participa como árbitro de texto y no como verificador visual.

### Límites de abuso

Clasificar una foto cuesta 25-60 s de CPU y una llamada paga a OpenRouter por verificador (tres por defecto), más las llamadas extra listadas arriba (árbitro por disputa, encaminamiento por texto, corrección de descripción). Por eso `/clasificar` viene con techos puestos de fábrica:

| Variable | Default | Qué hace |
|---|---|---|
| `MAX_BYTES` | `10485760` (10 MB) | Tamaño máximo del upload; más grande devuelve `413`. |
| `MAX_PIXELES` | `25000000` | Megapíxeles máximos; frena bombas de descompresión con `400`. |
| `CONCURRENCIA` | `1` | Clasificaciones en paralelo; por encima devuelve `503`. |
| `RATE_LIMITE` / `RATE_VENTANA` | `60` / `3600` | Pedidos por IP y ventana en segundos; por encima devuelve `429`. `0` desactiva. |
| `CUOTA_DIARIA` | `500` | Techo global de fotos verificadas por día. Pasado el techo la API sigue respondiendo, pero degradada (sin clasificación pública, con motivo). `0` desactiva. |
| `API_TOKEN` | vacío | Si lo ponés, `POST /clasificar` exige el header `X-Api-Token`. |
| `CACHE_MAX` | `128` | Respuestas cacheadas por hash de foto, para no pagar dos veces la misma. |
| `CONFIAR_PROXY` | apagado | Hace que el límite por IP use `X-Forwarded-For`. |

Si publicás la API en internet, además de esto:

- **Detrás de un proxy, activá `CONFIAR_PROXY` y hacé que el proxy PISE el `X-Forwarded-For` que manda el cliente.** Las dos mitades importan. Sin `CONFIAR_PROXY`, todos los visitantes llegan como `127.0.0.1` y comparten una sola cuota: uno solo se la agota y deja afuera a todos los demás. Con `CONFIAR_PROXY` pero sin pisar el header, cualquiera rota su `X-Forwarded-For` y se saltea el límite. Sin proxy adelante, dejalo apagado.
- Poné un límite de tamaño de cuerpo en el proxy (`client_max_body_size` en nginx). La app exige `Content-Length` y lo rechaza por encima del techo, pero el servidor de adelante es el que evita que el cuerpo entero llegue a viajar.
- Poné un tope de gasto mensual en la clave de OpenRouter, con una clave dedicada a este servicio. `CUOTA_DIARIA` acota el gasto del lado de la app, pero es por proceso y se reinicia con el servicio.
- Tené en cuenta que `multipart/form-data` no dispara preflight de CORS: cualquier página puede hacer que el navegador de sus visitantes pegue contra tu endpoint. El límite por IP y el token son lo que lo frena.

El límite por IP y el de concurrencia viven en memoria del proceso: son por instancia y se reinician con el servicio. Para varias instancias hace falta llevarlos al proxy o a un store compartido.

### El reclamo del vecino manda sobre la foto

Si el vecino escribe qué le pasa, eso **pesa más que lo que se vea en la foto**. La lógica:

| Situación | Resultado |
|---|---|
| Hay contexto y la foto lo respalda | Se reporta lo de la foto. `foto_valida: true` |
| Hay contexto y la foto **no** lo respalda | Se reporta **lo que pidió el vecino**, encaminado desde el texto solo. Lo de la foto pasa a `descartados_por_foto`. `foto_valida: false` |
| Hay contexto y no mapea a nada del catálogo | `hay_problema: false`. No se inventa un reporte |
| No hay contexto | Se reporta lo de la foto, como siempre. `foto_valida: null` |

Cuando el reclamo se encamina desde el texto y es ambiguo, va la categoría **genérica**: "mi cuadra está llena de basura" es `recoleccion`, no `retiro_muebles` ni `retiro_escombros`, porque no sabemos cuál es y la genérica es la que no se equivoca.

### Lo que vio una sola fuente no se afirma

Por default el árbitro **no promueve a confirmado** lo que reportó una sola fuente: sale en `posibles`. Es una decisión medida, no una preferencia: se probaron cuatro modelos de árbitro (DeepSeek en texto, y gpt-5-nano, qwen3-vl-32b y Claude Sonnet 5 **viendo la foto**) y dieron 2 rescates correctos sobre 21 confirmaciones, los cuatro por debajo de lo que sacaría rechazar todas las disputas. Se puede volver al comportamiento anterior con `ARBITRO_CONFIRMA=1`.

### Sobre el contexto vecinal y la inyección de prompt

El `contexto` que escribe quien sube la foto, y cualquier texto que aparezca *dentro* de la foto, llegan a los modelos de visión. Son datos no confiables, y se tratan como tales:

- La rúbrica viaja en un mensaje `system` aparte; los datos del usuario van en el `user`. Lo mismo para el árbitro.
- Una categoría se reporta por los OBJETOS que se ven. La cartelería propia del lugar sirve de apoyo cuando además hay un objeto; un texto dirigido a quien analiza (que pide reportar algo, dicta una gravedad o viene con formato de instrucción) no es evidencia. El árbitro rechaza la evidencia que se apoya solo en una frase, sin objeto detrás.
- Las descripciones y evidencias vuelven acotadas y sin caracteres de control.

**Lo que el default NO cubre.** Los verificadores miran la misma foto con el mismo prompt, así que no son fuentes independientes: una sola inyección que funcione en dos de ellos alcanza para el consenso y la categoría **se confirma sin pasar por el árbitro**. Existe `CONSENSO_VLM_SOLO=arbitro`, que manda esas categorías al árbitro, pero **no es el default**: un eval no pudo demostrar que sirviera (ninguna inyección probada logró engañar a los dos a la vez, así que el mecanismo nunca se ejercitó) ni descartar su costo. Ver [`verificador.py`](verificador.py) y [`eval/`](eval/).

Y la frontera entre cartelería real y cartelería falsa no es autenticable: alguien puede sobreimprimir o fotografiar un cartel realista junto a un objeto real, y el texto manipulado aporta justo la propiedad que decide la categoría. El árbitro no ve la imagen.

Nada de esto es una barrera dura: un LLM no tiene un límite real entre instrucciones y datos. `descripcion` es texto generado por un modelo e influido por quien sube la foto, así que **escapalo antes de renderizarlo como HTML** y no abras reportes automáticos sin revisión humana.

## Ejemplo

```bash
python ejemplo.py foto.jpg
```

## Privacidad

El modelo local no envía nada a ningún lado. Con la verificación activa, la foto (reducida a 1024px) se envía a los modelos configurados a través de OpenRouter; revisá sus políticas de datos antes de usarla con fotos sensibles.

El `contexto` que escribe el vecino entra a los modelos pero **no vuelve en la respuesta** (suele traer PII: nombres, patentes, firmas). Excepción deliberada: en los reportes de vehículos (`vehiculo_mal_estacionado`, `vehiculo_abandonado`) la respuesta puede incluir la `patente` del vehículo infractor **leída de la foto** — es el dato que el trámite de la Ciudad exige y la razón de la foto. Sale solo de la chapa visible, nunca del texto del vecino. Un riesgo que no se puede eliminar del todo: las descripciones de los modelos de visión son parte de la respuesta y pueden transcribir texto visible en la foto (una patente en un cartel, por ejemplo). Si eso importa en tu caso, no persistas las respuestas.

## Licencia

[MIT](LICENSE)
