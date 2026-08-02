# Ojo Urbano

API local para clasificar fotos de incidencias urbanas: residuos en la vía pública, escombros, muebles abandonados, contenedores y cestos en mal estado, baches, veredas rotas, vehículos abandonados o mal estacionados, plagas y más (41 categorías canónicas, ver [`categorias.json`](categorias.json)).

Combina dos capas:

1. **Modelo propio, gratis y local.** Embeddings de imagen (CLIP + DINOv2 + SigLIP2, todos open source) con un cabezal de regresión logística multi-etiqueta entrenado con miles de fotos callejeras reales etiquetadas a mano, más un regresor de gravedad (1 a 5). Corre 100% en tu máquina, sin ninguna API paga. Las clases sinónimas del modelo se pliegan a una categoría canónica (por ejemplo, todo objeto voluminoso descartado sale como `retiro_muebles`).
2. **Verificación cruzada por IA (opcional).** Con una clave de [OpenRouter](https://openrouter.ai), dos modelos de visión (por defecto **GPT-5 mini** y **Gemini Flash Lite**, elegidos por bake-off contra fotos reales etiquetadas) analizan la foto de forma independiente siguiendo una rúbrica detallada por categoría, calibrada contra fotos reales. Una categoría queda confirmada cuando la reportan al menos 2 de las 3 fuentes; las que tienen una sola fuente van a un **árbitro** de texto (por defecto **DeepSeek**) que lee los veredictos y las probabilidades del modelo local y decide. El subtipo de contenedor de húmedos (lateral vs bilateral) siempre lo decide el modelo local, que es más preciso ahí que los modelos de visión. El costo por foto es de fracciones de centavo.

   Algunas categorías (por ejemplo `vehiculo_mal_estacionado` o `columna_poste_cable`) no existen en el modelo local: las detectan solo los modelos de visión, y quedan confirmadas cuando ambos las reportan (2 de 3 fuentes). Con un solo reporte van al árbitro, como cualquier otra disputa.

   Cada verificador devuelve además una **descripción** breve de la foto dentro de su misma respuesta, y la API entrega en `final.descripcion` una descripción consolidada que respalda las categorías confirmadas: la redacta el árbitro cuando ya interviene por una disputa, y si no hay disputa se elige la descripción del verificador que más coincide con el resultado final. Todo sin llamadas extra: el conteo de llamadas por foto no cambia.

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

`multipart/form-data` con el campo `file`. Campo opcional `contexto` ("contexto vecinal"): texto de quien reporta, que los modelos de visión y el árbitro usan como pista para interpretar la foto; nunca como evidencia (se reporta solo lo que la foto muestra, máx. 500 caracteres). Las categorías que el contexto describe pero la foto no confirma vuelven aparte en `final.categorias_contexto` (por ejemplo, "hay ratas por todos lados" devuelve `desratizacion` ahí aunque no se vea ninguna rata); no cuentan para `gravedad_maxima` ni `sin_problema`. Parámetro opcional `verificar`: `auto` (default: verifica si hay clave), `1` (forzar), `0` (solo modelo local).

```bash
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" http://127.0.0.1:8080/clasificar
```

Respuesta (resumida):

```json
{
  "modelo_local": {
    "predichas": [{ "key": "recoleccion", "nombre": "Recolección de residuos", "score": 0.94 }],
    "top5": [ ... ],
    "probabilidades": [ ... ],
    "gravedad": { "value": 3, "raw": 3.2 }
  },
  "verificacion": {
    "activa": true,
    "verificadores": [
      { "modelo": "moonshotai/kimi-k2.5", "ok": true, "categorias": [ ... ],
        "descripcion": "Bolsas de residuos y cajas apiladas junto a un contenedor negro." },
      { "modelo": "qwen/qwen3-vl-8b-instruct", "ok": true, "categorias": [ ... ],
        "descripcion": "Vereda con basura domiciliaria acumulada al pie de un contenedor." }
    ],
    "arbitro": { "modelo": "deepseek/deepseek-v4-flash", "ok": true, "decisiones": [ ... ],
                 "descripcion": "..." },
    "en_duda": [],
    "descripcion": "Bolsas de residuos y cajas de cartón acumuladas en la vereda junto a un contenedor negro de húmedos.",
    "descripcion_fuente": "deepseek/deepseek-v4-flash"
  },
  "final": {
    "categorias": [
      { "key": "recoleccion", "nombre": "Recolección de residuos", "gravedad": 3,
        "fuentes": ["modelo_local", "moonshotai/kimi-k2.5", "qwen/qwen3-vl-8b-instruct"] }
    ],
    "en_duda": [],
    "descripcion": "Bolsas de residuos y cajas de cartón acumuladas en la vereda junto a un contenedor negro de húmedos.",
    "gravedad_maxima": 3,
    "sin_problema": false
  }
}
```

`final.categorias` es el veredicto consolidado; `fuentes` dice quién vio cada problema. Una foto puede tener varias categorías a la vez (una por problema visible). `final.descripcion` describe la foto respaldando esas categorías (`verificacion.descripcion_fuente` dice quién la redactó); es `null` cuando la verificación no corre.

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

Nota: los modelos de DeepSeek en OpenRouter no aceptan imágenes, por eso participa como árbitro de texto y no como verificador visual.

## Ejemplo

```bash
python ejemplo.py foto.jpg
```

## Privacidad

El modelo local no envía nada a ningún lado. Con la verificación activa, la foto (reducida a 1024px) se envía a los modelos configurados a través de OpenRouter; revisá sus políticas de datos antes de usarla con fotos sensibles.

## Licencia

[MIT](LICENSE)
