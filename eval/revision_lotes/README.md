# Revisión de fotos por lote

La página muestra una foto por vez y la respuesta original de la API en modo Completo. Permite aprobar las sugerencias o guardar una corrección por categoría, material y ámbito de la escena. El trabajo corresponde al ticket #42; la regla de interiores en producción se sigue por separado en #43.

Para revisar, abrí `revision.html` dentro de la carpeta de entrega. Si el resultado es correcto y la situación está en la calle, usá "Aprobar y confirmar vía pública". Si hay diferencias, editá las sugerencias y guardá la corrección. Una respuesta parcial necesita una confirmación adicional de que revisaste los campos faltantes.

El ámbito describe dónde está el material o el problema. Una foto tomada desde una ventana puede mostrar un problema en la vía pública. El botón "Rechazar por interior y seguir" guarda el rechazo y avanza, sin revisar materiales, categorías ni respuestas parciales. Al volver, muestra el pedido de otra foto en vía pública y oculta las sugerencias. La exportación marca `exclusion=interior` y deja categorías y materiales en `sin_revisar`, para que no se usen como etiquetas negativas. Las revisiones anteriores siguen cargando sin alterar sus decisiones. Cambiar una marca de interior a vía pública vuelve a abrir la revisión de sugerencias sin aprobarlas automáticamente. La respuesta original de la API queda intacta.

Los botones "Falta contexto" y "Mala calidad" piden otra foto y avanzan, sin marcarla como interior ni adjudicar categorías o materiales. La exportación conserva `revision_tecnica=contexto` o `revision_tecnica=calidad`. "Volver a revisar la clasificación" abre una revisión nueva de las sugerencias y mantiene el historial.

La selección de prioridad permite señalar el problema principal entre las categorías confirmadas en la revisión. Guardarla no aprueba la foto ni cambia categorías. Si después quitás la confirmación de ese problema, se elimina su prioridad para evitar una contradicción. El comparador puntúa esa elección por separado, incluso cuando la revisión queda como borrador. Exige la constancia de "Guardar solo la prioridad" en el historial de la exportación; un campo sin ese registro queda conservado pero sin puntuar. Las sugerencias de categorías y materiales del borrador no se convierten en etiquetas humanas.

Las decisiones y la foto actual se guardan en el navegador. Exportá la revisión al terminar cada tanda; ese JSON permite recuperar el trabajo o llevarlo a otro dispositivo. Cambiar de navegador o limpiar sus datos puede borrar la copia local. Si el catálogo incorpora una categoría, las revisiones guardadas la reciben como `sin_revisar`, sin perder su posición ni convertirla en un negativo. "Actualizar resultados" carga las respuestas que vaya completando la cola sin borrar las revisiones.

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

Se generan un HTML y un JSON con aciertos conservados, regresiones, correcciones y errores persistentes. Sin `--candidata`, mide la referencia contra las etiquetas humanas y no aprueba una comparación entre versiones. La comparación no llama a modelos ni consulta la red. Una ausencia equivale a `no` solo en una respuesta completa y para una categoría revisada expresamente. En respuestas parciales se puntúan los hallazgos confirmados o posibles que aparecen; las ausencias se registran como cobertura faltante. Un estado inválido o sin verificación no se puntúa. Las categorías posibles se distinguen de las confirmadas. Una categoría que aparece solamente en `en_duda` queda como cobertura pendiente, incluso cuando el análisis está completo. Los campos de contexto y descarte no confirman evidencia visual. Si la duda está en la referencia, ese campo tampoco acredita un acierto previo ni una comparación completa: queda como cobertura faltante aunque la candidata lo decida. La herramienta no reconstruye una decisión que la referencia nunca tomó. `duda` y `sin_revisar` no se puntúan. Las revisiones contradictorias bloquean la comparación, sin elegir una por fecha. Las claves humanas deben existir en el catálogo.

La comparación de prioridad usa `problema_principal`. `conteos_prioridad` separa sus aciertos y errores; `abstenciones_prioridad` cuenta las respuestas explícitamente indeterminadas. Una selección de una categoría no confirmada es `seleccion_invalida` y puede ser una regresión. Un campo ausente, `no_evaluado` o un criterio desconocido es cobertura faltante, no una abstención; `faltantes_prioridad` cuenta esos campos. Los problemas de identidad o de respuesta se informan en `faltantes` generales. Sin `--candidata`, ambos contadores de abstención corresponden a la misma referencia y no representan dos ejecuciones. La elección humana "No se distingue una prioridad" permite comprobar que el modelo tampoco invente una selección. No se evalúa el motivo escrito ni se convierte una prioridad propuesta en la conversación en una adjudicación definitiva.

La prioridad guarda relación con la respuesta original por su huella. El cambio no reescribe registros anteriores: para incorporar una exportación, creá otra instantánea. Un conflicto de prioridad bloquea el caso completo, como los otros conflictos; no se elige una fuente por fecha. Si cambiás o retirás una prioridad entre tandas e incorporás ambas exportaciones, queda pendiente de adjudicación. No se elimina la fuente anterior para ocultar el desacuerdo. Si una referencia antigua no tiene evaluación de prioridad, queda cobertura pendiente y no acredita preservación global, aunque existan aciertos de categorías.

En los interiores no se crean negativos de cada categoría. Puntuar `interior_rechazado` exige que la respuesta informe explícitamente el rechazo por interior y no conserve reclamos, posibles ni elementos. Una respuesta vacía sin ese motivo no acredita el rechazo por interior.

Por defecto se usa únicamente desarrollo. `--particion evaluacion_reservada` sirve para la evaluación final de una candidata ya fijada; no se usa para ajustar el prompt. La comparación exige mismo modo e imagen y una versión candidata uniforme. Hoy el adaptador del lote es para modo alto. Un contexto omitido en ambos archivos se interpreta como vacío, siguiendo el contrato de la cola; el productor de respuestas debe conservar el contexto real y su procedencia, la herramienta no puede auditar una omisión falsa.

Salida 0 significa que una candidata de versión nueva conservó los aciertos previos evaluados sin falta de cobertura ni conflictos. Salida 1 señala regresiones, datos faltantes, conflictos, ausencia de aciertos previos para proteger, comparación solo de referencia, archivos idénticos o versión sin cambios. Los campos `identicas`, `version_sin_cambio` y `versiones_referencia` distinguen estas condiciones. Salida 2 señala archivos inválidos. Ninguna salida aprueba por sí sola un despliegue ni demuestra exactitud general. Un registro compuesto solo por correcciones de errores necesita también aprobaciones humanas de casos correctos para probar preservación.

### Importación de calidad y contexto (#46, #52)

La página separada de revisión técnica exporta `ojo-urbano-calidad-contexto-v1`.
Para incorporarla, necesitás también el `manifest.json` entregado con esa página:

```sh
python3 eval/revision_lotes/regresiones.py crear \
  --base /ruta/al/lote \
  --registro /ruta/privada/al/banco \
  --manifest-tecnico /ruta/a/la/revision-tecnica/manifest.json \
  --revision-tecnica /ruta/a/la/exportacion-tecnica.json \
  --revision /ruta/a/la/exportacion-v2-anterior.json
```

Incluí todas las exportaciones anteriores que deban conservarse. Tanto `--revision`
como `--revision-tecnica` pueden repetirse; crear una instantánea no incorpora
automáticamente las fuentes de una instantánea anterior. No sobrescribas originales
ni retires una fuente para ocultar un desacuerdo.

El importador comprueba conjunto, foto, bytes de la imagen de entrada API,
partición de desarrollo, valores admitidos y constancia de guardado explícito.
El nombre `sha256_foto` de este formato corresponde a los bytes de `entradas-api`,
que son los que muestra esa página. Se guardan los bytes originales de la
exportación y su manifest. No se incorporan fotos reservadas ni borradores.
Si hay un borrador posterior a una decisión guardada, solo se conserva la decisión
que efectivamente se exportó.

Con `--revision-tecnica`, el comparador evalúa las marcas suficientes e
insuficientes contra `evaluacion_foto.calidad_suficiente` y
`contexto_visual.suficiente`, por separado. También lee las marcas técnicas
estructuradas anteriores de la conversación para detectar contradicciones.
Sin esa opción se conserva el alcance anterior de categorías, interior y prioridad.
Las marcas técnicas no aprueban
categorías, ámbito ni el JSON original. `sin_revisar` e `indeterminado` no se
convierten en negativos. Las decisiones contrarias de dos exportaciones o de una
marca estructurada anterior quedan pendientes de adjudicación, sin elegir por
fecha. Las descripciones libres anteriores de calidad se conservan sin inferir
una etiqueta; requieren adjudicación explícita si se quiere puntuarlas.

Para puntuar cada campo, la API debe devolver un booleano y su estado `evaluado`.
Valores nulos, estados indeterminados, campos ausentes o formatos incompatibles
son cobertura faltante. Un rechazo nuevo de una foto previamente considerada
suficiente aparece como regresión del campo de calidad. Esto no valida por sí solo
el comportamiento de rechazo de la interfaz ni la exactitud del prompt.

Las pruebas de este importador usan datos sintéticos. Nunca las incorpores como
revisión humana del lote real.

### Reproducción acotada de poda y voluminosos (#45)

```sh
.venv/bin/python eval/revision_lotes/reproducir_poda.py \
  /ruta/H0001-alto.json /ruta/H0002-alto.json \
  --salida /ruta/nueva/reproduccion.json
```

Por defecto reproduce poda. Para comprobar voluminosos, agregá `--retiro retiro_muebles`. Para una escena mixta, usá `--retiro retiro_poda --retiro retiro_muebles`: cada retiro debe tener al menos dos lectores distintos válidos, sin voto anulado y con evidencia explícita no vacía guardada. Esta condición es más estricta que la primera versión, que contaba votos sin verificar esos campos. `cumple_retiros` informa cada resultado por separado. Una segunda ejecución de la misma política, desactivando únicamente la regla de preservación de retiros visibles, comprueba si esa regla fue necesaria para conservar cada retiro. `regla_preservacion_necesaria` distingue ese aporte de un servicio que ya sobrevivía por otra razón; solo se devuelve éxito si la regla fue necesaria para todos los retiros solicitados. Las fuentes reconstruidas ya satisfacen la corroboración inicial: esto comprueba la etapa de descarte y su alcance, no la veracidad visual de esos votos. El campo anterior `cumple_poda_humana` se conserva, con valor nulo si no se evaluó poda. No uses casos de evaluación reservada para ajustar la política a partir de esta reproducción.

Reutiliza las respuestas de revisión guardadas para ejecutar el consenso de alcance y la política real de descarte, con la red bloqueada y sin pesos. La entrada previa al descarte es un escenario mínimo reconstruido: el JSON público no conserva todo el estado interno, por lo que esto no reproduce la API completa. El informe conserva huellas del original y del código. Devuelve 1 mientras esos casos sigan perdiendo la poda; ese fallo esperado muestra una corrección pendiente, no un test exitoso de exactitud. Para comprobar si cambia lo que un prompt ve en la foto siguen haciendo falta inferencias nuevas, en una evaluación separada.

Pruebas sintéticas del registro:

```sh
python3 -m unittest discover -s eval/revision_lotes -p 'test_regresiones.py' -q
```

## Reanálisis individual (#58)

`reanalizar.cjs` mantiene un servicio en `127.0.0.1`. Usa el navegador para enviar una foto a la API pública en modo Completo, con nombre neutro y sin la revisión humana en el pedido. Requiere `analisis/reanalisis-config.json` privado con `url`, `puppeteer`, `puerto`, un `token` aleatorio de al menos 48 caracteres hexadecimales, `modo_version` comprobada después del despliegue y `tope_usd` mayor a cero y menor a cinco.

El botón del informe conserva el original y abre otra página para comparar y revisar la nueva respuesta. Cada intento usa otro identificador de conjunto para que sus decisiones no reemplacen las anteriores. La exportación incluye las huellas de ambas respuestas y de la foto. La aprobación no cambia la API ni entrena automáticamente.

Antes de enviar, el servicio comprueba las huellas, la versión desplegada, el presupuesto y que no haya otro intento pendiente. Reserva un dólar por pedido, cuenta el costo conocido y detiene nuevos envíos si falta información de consumo. Una respuesta perdida no provoca otro POST. Un trabajo con identificador puede recuperarse con consultas al reiniciar. Si no hay identificador, hay que conciliarlo antes de continuar. El importe reservado no es un cargo observado.

Una conciliación externa puede registrar una cota documentada en `reserva_acotada_usd` cuando se conoce el único pedido fallido, sus límites y las tarifas aplicables. Esa reserva sigue descontándose del presupuesto y se muestra separada del consumo conocido. No se libera por el mero paso del tiempo ni convierte un costo desconocido en cero. La interfaz mantiene disponible el reanálisis mientras el consumo conocido, la reserva acotada y la reserva de USD 1 para el pedido nuevo no superen el tope, igual que el servicio local.

La configuración y los intentos son privados. No publiques el token ni expongas este servicio mediante un túnel. El reanálisis no modifica `analisis/estado.json` ni reanuda la cola de fotos. Después de otro despliegue, actualizá la versión de la configuración y regenerá el HTML. Si se abrió desde otro equipo, el servicio loopback de este equipo no estará disponible.

Pruebas sin llamadas pagas:

```sh
node eval/revision_lotes/pruebas_reanalizar.cjs
```

`pruebas_interfaz.cjs` verifica el guardado, importación, rechazo por interior y pedidos de fotos complementarias en un navegador aislado. Nunca uses los guardados reales del usuario como datos de prueba.


## Bandeja de fotos recientes y análisis manual

La mesa de revisión muestra miniaturas y filtros de pendientes, fotos sin analizar, revisadas y todas. La búsqueda admite el identificador neutro o la fecha. Podés navegar con las flechas del teclado cuando el foco no está en un formulario. En celular, la bandeja se desplaza horizontalmente.

Prepará una carpeta nueva desde un archivo SQLite privado que contenga `message_history(photo_path, created_at)`. El preparador abre la base en lectura, comprueba que cada foto esté dentro de la carpeta permitida, elimina duplicados por bytes y puede excluir las huellas del lote anterior. Conserva la fecha de recepción, que no demuestra cuándo se tomó la foto. Las fotos recientes se destinan a desarrollo; no reemplazan la partición reservada del conjunto anterior.

```sh
python3 eval/revision_lotes/preparar_recientes.py \
  --db /ruta/privada/archivo.sqlite \
  --fotos /ruta/privada/fotos \
  --destino /ruta/privada/bandeja-nueva \
  --limite 50 --dias 14 \
  --excluir-manifest /ruta/privada/lote-anterior/entrega/manifest.json
```

La preparación no hace inferencias. Las rutas originales solo se incluyen en `manifest-privado.json`. Conservá toda la carpeta fuera de git. Las imágenes enviadas se normalizan con orientación EXIF, hasta 2048 píxeles, sin metadatos EXIF.

Agregá `analisis/reanalisis-config.json` con la configuración individual documentada arriba y `python`, la ruta del intérprete. Iniciá `node eval/revision_lotes/reanalizar.cjs BASE`. La página de la bandeja está en la raíz local del servicio, detrás de su token. No publiques ese token ni expongas el servicio. Abrir o navegar no envía fotos: solamente el botón Analizar registra un pedido.

El primer resultado se guarda en `analisis/R####-alto.json` con su huella; sigue disponible después de recargar. Los reanálisis conservan ese original y se revisan por separado. La aprobación requiere indicar calidad y ámbito; una respuesta parcial conserva su confirmación adicional. El archivo de exportación sigue siendo v2 y mantiene el historial. Exportá al terminar cada tanda, porque las decisiones pertenecen al navegador.

El lote automático anterior conserva el modo Completo y su intervalo mínimo de 61 segundos, correspondiente al límite público de 60 pedidos por hora. La bandeja manual no lo reanuda. Tener más crédito no aumenta ese límite ni elimina las verificaciones que requiere cada foto.

Pruebas del flujo manual, sin inferencias pagas:

```sh
python3 -m unittest discover -s eval/revision_lotes -p test_recientes.py
node eval/revision_lotes/pruebas_recientes_navegador.cjs
```


La revisión principal tiene dos acciones: **Está bien** y **Corregir**. Confirmar indica expresamente que la foto se puede evaluar y muestra una situación en la vía pública. Una respuesta parcial sigue requiriendo una confirmación adicional visible.

Para corregir, usá **Quitar** junto a lo que no se ve. Buscá un problema faltante por su nombre y agregalo. La decisión del reclamo se deriva de esos cambios; no requiere otro selector. La nota es opcional y los materiales no revisados quedan como `sin_revisar`. Al guardar, la pantalla identifica tu corrección por separado de la respuesta original. El análisis no se vuelve a ejecutar.

**Interior o foto insuficiente** abre las alternativas de interior, falta de entorno o falta de detalle. Estos pedidos no convierten las categorías en negativos. **Opciones avanzadas** conserva los controles de ámbito, calidad, materiales, prioridad y explicación para una revisión detallada, junto con el formato de exportación v2 y su historial.
