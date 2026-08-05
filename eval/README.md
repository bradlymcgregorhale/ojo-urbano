# Eval del árbitro y de la rúbrica (agosto 2026)

Los números que aparecen en los comentarios de `verificador.py` y en los
mensajes de commit salen de acá. Están versionados para que se puedan
auditar y rehacer, no para que haya que creerlos.

**Los scripts que llaman a modelos necesitan las fotos en `eval/fotos_cache/`,
que no está versionado.** Sin ese cache avisan y saltean, no fallan en
silencio. Armarlo requiere la base privada de solicitudes.

**No hay imágenes en el repo, ni URLs a ellas.** Son fotos de reportes
ciudadanos y al menos una es un documento de identidad. Publicar la URL del
CDN creaba un índice permanente hacia ese documento, así que se sacaron: que
el CDN sea público no lo vuelve seguro. Quedan el identificador del reporte y
un `sha256` truncado. También se sacaron las filas de los reportes cuya foto
no era vía pública (documentos, trámites, capturas de cuentas personales): no
alcanzaba con borrar la descripción, porque la fila seguía asociando un
identificador de reporte con "foto de un documento".

Eso fija el alcance: **desde el repo se audita la evidencia congelada y se
rehacen todas las cuentas (`analizar.py`), pero no se pueden volver a bajar
las fotos.** Los scripts que llaman a modelos necesitan las fotos en
`eval/fotos_cache/`, que arma el operador desde su base privada.

## Método

El problema de medir esto es que el pipeline llama a modelos que no repiten
respuesta. Para que un cambio en el código no se confunda con el ruido de los
modelos, casi todo se mide sobre **evidencia congelada**: cada foto se
clasifica UNA vez (modelo local + los dos verificadores) y ese resultado se
guarda. Después se re-corre solo la lógica que cambió, reusando esos
veredictos. Así el diff es atribuible al código.

Dos advertencias que costaron caro:

- **La tasa de cambio del árbitro deriva con el tiempo.** Dos corridas
  idénticas dieron 13,9% y 7,8%. Comparar condiciones en bloques secuenciales
  mezcla la condición con el momento: hay que **intercalar** las condiciones
  foto por foto (`harness/interleaved.py`).
- **Un cambio en la rúbrica NO se ve replayando evidencia congelada**, porque
  la rúbrica es justamente lo que produjo esa evidencia. Hay que volver a
  capturar (`harness/recaptura.py`). Un eval de la rúbrica hecho sobre
  evidencia vieja da "sin cambios" por construcción.

## Qué hay

| archivo | qué es |
|---|---|
| `datos/muestra.json` | las fotos adjudicadas que quedan: id, concepto de la ciudad, estrato, sha256 |
| `datos/adjudicacion.json` | qué se ve en cada foto, a ojo, según la rúbrica |
| `datos/evidencia_congelada.jsonl` | predicción local + veredictos de los 2 verificadores (rúbrica original) |
| `datos/evidencia_rubrica_v1.jsonl` | recaptura de las adjudicadas con la regla estricta |
| `datos/evidencia_rubrica_v2.jsonl` | recaptura de las adjudicadas con la regla revisada |
| `datos/inyecciones_*.jsonl` | ataques + controles limpios, por versión de rúbrica |
| `datos/votacion_pareada.jsonl` | voto1 vs voto3, pareado e intercalado |
| `datos/consenso_replay.jsonl` | `CONSENSO_VLM_SOLO` arbitro vs confirma |

## Reproducir los números

```bash
python eval/analizar.py
```

No llama a ningún modelo ni necesita clave: lee solo `datos/`. Imprime todas
las cifras que aparecen en los comentarios de `verificador.py` y en los
mensajes de commit: las tablas de F1, los conteos de inyección con sus
p-valores de Fisher, el McNemar de la votación y el piso de ruido. **Si un
número publicado no sale de ahí, no está respaldado.**

Para rehacer el eval desde cero (llama a modelos, cuesta plata):

| paso | script | notas |
|---|---|---|
| re-muestrear fotos | `harness/capturar.py` | necesita `SOLICITUDES_DB=/ruta/solicitudes.sqlite`, base privada. Solo para muestras nuevas. |
| recapturar con la rúbrica actual | `harness/recaptura.py` | necesita las fotos en `eval/fotos_cache/`; avisa cuáles faltan |
| cohorte de inyección | `harness/adversario.py` | |
| votación pareada | `harness/interleaved.py` | |
| piso de ruido | `harness/ruido2.py` | |
| calidad contra la adjudicación | `harness/calidad_arbitro.py` | |

Los scripts son reanudables: guardan lo hecho y saltean lo que ya está.

Los conteos exactos los imprime `analizar.py`: acá no se repiten a propósito,
porque quedaron desincronizados cuando se sacaron filas por privacidad.

## Definiciones

- **F1 es micro**, sobre pares (foto, categoría). Las claves `PRESENCIA`
  (contenedores visibles) se excluyen: no son problemas.
- **Positivos de referencia** = las categorías que la adjudicación marca como
  visibles. Al comparar brazos se usa la **intersección** de fotos válidas en
  todos ellos, para que el denominador sea idéntico; si no, el conteo de
  positivos se mueve entre brazos y las cifras no cierran.
- **"Flip"** = replayar la misma foto dos veces con la misma configuración y
  obtener distinto resultado. Se mide por separado sobre el conjunto de
  categorías, `hay_problema`, `gravedad_maxima` y el texto exacto de
  `descripcion`. El del conjunto de categorías es el más grande y el que menos
  le importa al usuario.

## Limitación principal

**La adjudicación la hizo un solo juez, a ciegas de lo que dijeron los
modelos, pero es un modelo de visión.** Puede compartir puntos ciegos con los
verificadores y favorecer al que más se le parezca. Todo lo que acá se llama
"precisión" o "recall" es, con más precisión, **acuerdo con un adjudicador
ciego**, no exactitud contra verdad humana. Para sostener las cifras haría
falta al menos un segundo juez humano y adjudicar los desacuerdos.

Otras: las cohortes de inyección son chicas, demasiado para concluir nada
—los tamaños y los p-valores exactos los imprime `analizar.py`—; un brazo por
captura mezcla el efecto con la variación del proveedor; y las fotos son de
una sola ciudad.
