# Prompts de análisis

Para revisar una categoría, abrí su archivo en `rubrica/categorias/`. Por
ejemplo, [retiro_escombros.txt](rubrica/categorias/retiro_escombros.txt) define
qué cuenta como escombros y qué queda afuera. La pasada dirigida que vuelve a
mirar las bolsas está en [segundas_miradas/escombros.txt](segundas_miradas/escombros.txt).

| Archivos | Dónde se usan |
| --- | --- |
| `rubrica/general.txt` y `rubrica/categorias/*.txt` | Política de la primera pasada, en el mensaje `system` de cada verificador. Incluye gravedad, evidencia y formato de respuesta. |
| `primera_pasada/*.txt` | Mensaje `user` que acompaña la foto, con el contexto vecinal y las prestaciones candidatas cuando corresponde. |
| `segundas_miradas/*.txt` | Auditorías de escombros, presencia, subtipo, base, postes, daño, volcado, desborde y voluminosos. |
| `dirigidos/*.txt` | Lectura de patente, pregunta abierta y búsqueda de un objeto concreto. |
| `arbitro/*.txt` | Sistema del árbitro y bloques del mensaje de usuario para resolver disputas y redactar la descripción. |
| `contexto/*.txt` | Clasificación del reclamo escrito cuando la foto no corresponde. |
| `compartidos/subtipo_humedos.txt` | Regla única del color del contenedor de húmedos, insertada en la rúbrica y en la segunda mirada de subtipo. |

`verificador.py` decide cuándo usar cada prompt, arma los datos del caso y
procesa las respuestas. Los umbrales, vetos y reglas de consenso están ahí.
Las pasadas dirigidas se activan según los votos y la configuración; no todas
corren para cada foto.

## Cómo editar

Los archivos son texto UTF-8. Una barra invertida al final de una línea une
esa línea con la siguiente, sin agregar ni quitar espacios. El espacio al
principio de la continuación separa las palabras:

```text
Primera parte del criterio\
 y continuación del mismo criterio.
```

Eso llega al modelo como `Primera parte del criterio y continuación del mismo criterio.`
Un salto sin barra sí llega al modelo. El cargador quita solo el último salto
del archivo, que es el cierre del archivo de texto. Si el prompt debe terminar
en un salto, dejá una línea vacía al final. Así podemos ajustar el ancho de las
líneas para leer los diffs sin cambiar el texto enviado.

`rubrica/general.txt` tiene dos inserciones: `{{CATEGORIAS}}` y
`{{REGLA_SUBTIPO_HUMEDOS}}`. `__init__.py` arma la rúbrica en el orden explícito
de `RUBRICA_CATEGORIAS`; de esa misma lista sale `_RUBRICA_KEYS`. Para agregar
una categoría detallada hay que agregar su archivo y su entrada en esa lista.
`{RESTANTES}` se completa después con las categorías del catálogo que no
tienen un criterio propio.

Las plantillas que reciben datos usan `str.format`: campos como `{contexto}`,
`{categorias}` o `{tipo}`. En esas plantillas, las llaves literales del JSON
se escriben dobles, `{{` y `}}`. Los archivos que no reciben datos conservan
las llaves simples. La llamada en `verificador.py` muestra qué campos recibe
cada plantilla. El contexto y las evidencias se serializan como JSON antes
de insertarse; su contenido no se vuelve a interpretar como plantilla.

Cuando cambies un criterio, revisá también la pasada dirigida y el bloque
del árbitro que hablen de ese caso. Solo la regla de subtipo de húmedos tiene
una copia compartida; las demás coincidencias de política necesitan revisión.

## Pruebas y despliegue

Corré `.venv/bin/python pruebas.py`. Las pruebas de frases y de comportamiento
siguen leyendo las constantes de `verificador.py`. También hay huellas SHA-256
en `huellas.json` para detectar cambios en el texto completo de los prompts y
en mensajes armados con datos de prueba. Si cambiás la política a propósito,
revisá la diferencia antes de actualizar la huella correspondiente.

Los textos se cargan una vez al importar, con rutas relativas a este paquete.
El proceso puede arrancar desde otro directorio. Un archivo faltante impide
el arranque y muestra su ruta.

Al desplegar, copiá `prompts/` completo junto con `verificador.py` antes de
reiniciar el servicio. Copiar solo `verificador.py` no alcanza. Los cambios
en los textos necesitan reinicio para entrar en uso.
