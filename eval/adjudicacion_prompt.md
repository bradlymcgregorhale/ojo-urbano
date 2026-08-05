# Cómo se hizo la adjudicación

`datos/adjudicacion.json` es la referencia contra la que se miden precisión y
recall. Esto documenta quién la hizo y cómo, porque de eso depende cuánto
valen esas cifras.

## Quién

**Claude Opus 5** (Claude Code, 2026-08-04), repartido en jueces paralelos
con un lote de fotos cada uno. **No es un humano.** Es un modelo de visión, igual que
los dos verificadores que se están evaluando, así que puede compartir puntos
ciegos con ellos y favorecer al que más se le parezca.

Por eso, en todo el eval, lo correcto es decir **"acuerdo con un adjudicador
ciego"** y no "exactitud". Para sostener las cifras haría falta al menos un
segundo juez humano y adjudicar los desacuerdos entre ambos.

## Cegado

Cada juez recibió únicamente:

1. La rúbrica, leída de `verificador.py` (`_RUBRICA`), la misma que se le da a
   los modelos de producción. Se leyó del archivo a propósito, para que no
   pudiera haber deriva entre lo que se juzga y lo que se pide.
2. Una lista de fotos (número y ruta) para mirar una por una.

**No** recibió: qué reportó gpt-5-mini, qué reportó gemini, qué había dicho el
modelo local, el concepto con que el vecino presentó el reclamo, ni de qué
estrato venía la foto. La muestra se mezcló antes de repartirla, así que
tampoco podía inferir si una foto venía del grupo donde los modelos coinciden
o del grupo donde discrepan.

## Consigna exacta

> Sos un adjudicador CIEGO para una evaluación de clasificación de imágenes.
> Juzgás qué se ve realmente en fotos de calle de Buenos Aires. NO se te dice,
> y no debés buscar, qué dijo ningún modelo de IA sobre estas fotos: juzgá
> solo lo que ves.
>
> PASO 1. Leé la rúbrica que define cada categoría y sus criterios en
> `verificador.py` (el string `_RUBRICA`). Aplicá esos criterios de forma
> estricta y literal, sobre todo las reglas negativas (por ejemplo: el vidrio
> roto SIEMPRE es retiro_muebles y nunca barrido; los vehículos NUNCA son
> obstruccion; un volquete paralelo al cordón sin desbordar NO es reportable;
> unas pocas hojas dispersas NO son barrido; las claves PRESENCIA son
> contenedores que simplemente se ven).
>
> PASO 2. Leé el archivo con la lista de fotos ("número<TAB>ruta").
>
> PASO 3. Para CADA foto, abrí la imagen y decidí qué claves de categoría se
> ven de forma genuina e inequívoca. Sé conservador: si no estás seguro de que
> está, no lo pongas. Reportá lo que un humano cuidadoso confirmaría mirando
> solo la foto.
>
> Salida: JSON estricto, un objeto por foto:
> `[{"n": 1, "visible": ["key1"], "confianza": "alta|media|baja", "nota": "máx 12 palabras"}]`
>
> - `visible` usa SOLO claves exactas de la rúbrica. Lista vacía si no hay nada
>   reportable (una calle limpia y normal).
> - Incluí las claves PRESENCIA cuando se vea ese contenedor.
> - `confianza` es TU certeza sobre la foto, no la del modelo.
> - Si una imagen no carga, `{"n": N, "visible": [], "confianza": "baja", "nota": "ilegible"}`.
> - Juzgá todas las fotos del archivo, sin saltear ninguna.

## Marca de basura

Varias fotos quedaron marcadas `basura`, se excluyen de todas las métricas y
después se sacaron del manifiesto: no son escenas de calle. Son el logo institucional de la Ciudad
(el placeholder que devuelve el CDN cuando no hay foto, repetido byte a byte
muchas veces), archivos rotos de unos pocos bytes, capturas de Google Street
View, documentos personales y fotos de interiores. Contarlas hundía artificialmente el recall de `puesto_flores` y
`puesto_diarios`, donde la mayoría de las fotos eran ese placeholder.

## Reproducir

`datos/adjudicacion.json` tiene, por foto, las **claves visibles y la
confianza**. Las notas de una frase que escribieron los jueces **no
sobrevivieron a la consolidación**: se perdieron al unir los lotes, y
prefiero decirlo a dejar el documento afirmando algo que el archivo no tiene.
Para re-adjudicar hace falta volver a correr la consigna de arriba.

**Las fotos no se versionan y tampoco sus URLs.** La primera versión de este
eval publicaba la URL del CDN de cada foto para que se pudieran rebajar. Eso
creaba un índice permanente hacia fotos de vecinos, incluido un documento de
identidad: que el CDN sea público no hace que esté bien publicar un puntero
durable a eso. Se sacaron todas las URLs, se quitaron del manifiesto las
fotos marcadas basura (que incluyen los documentos), y se scrubbearon las
descripciones donde un modelo había descrito un documento personal.

La consecuencia honesta: **desde el repo se puede auditar la evidencia
congelada y rehacer todas las cuentas, pero no volver a bajar las fotos.**
Para eso hace falta la base privada de solicitudes, que tiene el operador.
