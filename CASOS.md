# Bitácora de casos

Cada foto reportada en una revisión humana, qué se hizo con ella y dónde quedó
fijada. La regla es que un caso resuelto no vuelve: si tiene prueba en
`pruebas.py`, un cambio futuro que lo rompa hace fallar la suite. Los que no se
pueden fijar con una prueba (techos de percepción, desacuerdos de etiqueta)
quedan acá anotados para no volver a intentar lo mismo.

Estado: **fijado** (hay prueba), **medido** (se midió y se decidió no cambiar),
**abierto** (en cola), **techo** (los modelos no pueden, documentado).

## Ronda 5 (U001-U050, 2026-08-17)

| caso | qué reportó el dueño | estado | dónde |
|---|---|---|---|
| U003 | tablones que probablemente son cartón, con un solo modelo viéndolos | fijado | validación cruzada, `70df7c1`; pruebas "[#V]" |
| U013 | contenedor roto no reportado | techo | la señal es "la tapa está salida del eje del lado derecho" (la dio el dueño mirando la foto). Se midió como pregunta dirigida y NO separa: U013 da 2 de 3 "un extremo salido", pero T160 y T175 (dos fantasmas que el dueño rechazó) dan exactamente el mismo 2 de 3, y T008 (contenedor quemado, roto de verdad) da 2 de 3 "ambos en su eje". La reformulación posicional anterior también falló, confirmando el fantasma T109. Se revirtió incluso el texto de rúbrica: los modelos afirman "torcida" en contenedores sanos, así que invitarlos a buscarla suma fantasmas |
| U014 | dos bolsas opacas sin reportar recolección | fijado | rúbrica de `retiro_poda`: en escena mixta se reportan las dos |
| U022 | bilateral publicado como lateral | fijado | chequeo de los postes citados: mayoría "sin postes" levanta la guardia |
| U030 | foto ilegible, no debería emitirse juicio | fijado | no era calidad: era un fantasma. Un solo modelo votó recolección con la evidencia "bolsas y BULTOS", el local la respaldó con 0,995 y se publicó. Preguntando ABIERTO (sin nombrar el objeto) los modelos se parten y uno dice "ramas o restos de poda": la recolección deja de publicarse. Sus métricas de calidad son las de la mediana del corpus, así que una compuerta por calidad no era el camino |
| U032 | la descripción inventa una caja de cartón | abierto | familia "la prosa afirma lo que las categorías no sostienen" |
| U035 | escombros afirmados en foto que no lo permite | abierto | ídem; sale de la fusión con el modelo local |
| U042 | vehículo sin evidencia de infracción; cartón como voluminoso | techo (vehículo) / abierto (cartón) | El dueño confirmó: las ruedas están sobre el ASFALTO, y su regla es que un auto contra un cordón AMARILLO está en infracción. Las dos cosas entraron a la rúbrica. Pero el falso positivo no se mueve: la pregunta dirigida por dónde pisan las ruedas da 3 de 3 "vereda" y el texto nuevo tampoco lo corrige. Es el mismo techo que U013. El cartón sigue publicándose como voluminoso; ver la nota de la pregunta abierta abajo |
| U049 | la descripción afirma tablas donde hay cajas | abierto | ídem U032/U035 |

## Ronda 4 (T001-T200, 2026-08-16/17)

| caso | qué reportó el dueño | estado | dónde |
|---|---|---|---|
| T005, T044, T082 | subtipo del contenedor equivocado | fijado | adjudicación dirigida del subtipo; pruebas "[#S]" |
| T035, T109, T130 | contenedor verde fantasma al lado de uno real | fijado | veto de presencia por clave, `a272b60`; pruebas "[#PK]" |
| T038 | manta leída como colchón | fijado | firma de identidad del voluminoso |
| T050, T064 | tapas trabadas leídas como desborde | fijado | mirada dirigida del desborde |
| T051 | gravedad 4 donde el dueño ve 5 | fijado | prueba práctica del 5 (dos contenedores unidos por el desparramo) |
| T053 | "tablones que son cartón" | medido | ampliando la zona son tablones REALES: acertaron los modelos, no la etiqueta |
| T055, T068, T175 | el contenedor contado como voluminoso | fijado | exclusión del mobiliario urbano en la firma de identidad |
| T083 | gravedad 3 donde el dueño ve 5 | abierto | subió a 4, no a 5 |
| T097, T159 | desborde en bilateral cerrado | techo | los tres modelos leen "interior lleno" en una tolva que no muestra el interior |
| T109, T183 | reparación fantasma | techo | ningún modelo contesta "usable" ahí, ni preguntado dirigido |
| T132, T173 | escombros no detectados | techo | el modelo local da 0,538 y 0,000: es dato de reentrenamiento, no de prompt |
| T141 | contenedor donde solo hay un tacho | fijado | veto de presencia + regla de la proporción |
| T152 | balde fusionado con el cesto | abierto | |
| T160 | reparación por daño estético | fijado | la vara de la reparación es el uso |

## Cuánto cuesta el saneo de prosa (medido)

Medición pareada sobre 20 fotos de la ronda 4: se graban las respuestas de los
modelos UNA vez y se reproduce `verificar()` dos veces sobre las MISMAS
respuestas, con `SANEO_PROSA` prendido y apagado. (Correr la foto dos veces
contra la API no sirve: los modelos redactan distinto cada vez y cambia el
100% de las descripciones por ruido, que fue el primer intento y no medía
nada.)

- Corroborando por PALABRA exacta: cambia el 65% de las descripciones, y
  varias pierden justo la frase informativa (un modelo dice "cajas" y otro
  "cartones" para la misma pila). Descartado por eso.
- Corroborando por FAMILIA de objeto: cambia el 30%, ningún fallback a la
  línea de categorías, ninguna categoría alterada, y lo que se borra son casi
  siempre frases de AUSENCIA ("no se identifican restos de escombros"), que
  tampoco debería afirmar una sola fuente.

## Lo próximo, ya especificado: la pregunta abierta para el voluminoso

El cartón leído como voluminoso (U003, U042, U049) es lo que más repitió el
dueño: "es extremadamente importante no marcar como voluminoso algo que es
cartón". El instrumento que funcionó para la recolección (preguntar ABIERTO
qué hay en el piso, sin nombrar el objeto, y exigir que la respuesta nombre
la familia que el reclamo necesita) es el candidato natural, porque el que ve
cartón dice cartón cuando nadie le sugiere "tablas".

Lo que hace falta para shippearlo, medido en un intento que se revirtió por no
estar listo: la pregunta abierta tiene que llevar el estado DESCARTADO / EN USO
(si no, muere la protección de la bicicleta estacionada), y hay que re-espectar
ocho pruebas que hoy alimentan la pregunta NOMBRADA, más la verificación en
vivo contra los 74 positivos etiquetados de retiro_muebles, que es donde
murieron los dos intentos anteriores de tocar esta clave.

## Límites conocidos del saneo de prosa (documentados, no bugs sueltos)

La corroboración de objetos entre fuentes es por bolsa de palabras, sin
análisis sintáctico. Eso deja tres esquinas angostas, todas encontradas y
reproducidas en revisión:

- **"restos de obra" contra "frente a una obra en construcción"**: la obra como
  MATERIAL y la obra como SITIO son la misma palabra. La categoría escombros
  igual necesita sus dos fuentes por su propio camino; lo que puede colarse es
  la frase en la prosa.
- **La ventana de adyacencia es de una palabra**: "puerta de vidrio apoyada"
  tiene dos palabras entre el objeto y la señal de descarte y no se sanea.
  Agrandar la ventana reabre el caso inverso ("bolsas apiladas contra la
  ventana"), así que es un canje, no un descuido.
- **"cajón" respalda a "caja" y "silla" respalda a "mesa"**: la corroboración
  es por familia, así que dos modelos que ven objetos distintos de la misma
  familia se respaldan. Es el precio de no borrar el 65% de las descripciones
  (ver la medición de arriba).

Con `SANEO_PROSA=0` se publica la descripción cruda (comportamiento viejo), que
es como se mide qué detalle cuesta el saneo.

## Mediciones que cerraron una idea (no volver a intentarlas sin datos nuevos)

- **Compuerta por calidad de foto**: no sirve. Los falsos positivos por
  predicción dan 5,6 % en fotos de hasta 480 px y 9,2 % en las de más de 900, y
  una compuerta por nitidez tocaba 82 aciertos para evitar 6 errores.
- **Zona del objeto + rasgos observables + métricas del parche**: no separa. En
  T053 los rasgos de madera vinieron con parche 0,633 y un positivo real tenía
  0,844.
- **Autoevaluación de nitidez del modelo**: inútil; alucinan convencidos y
  unánimes.
- **Pregunta posicional por la tapa** (en vez de la del uso): invierte. U013
  sigue rechazado y el fantasma T109 pasaría a confirmado, 3 de 3.
- **Pedir la firma de identidad cuando los dos modelos que votan voluminosos
  NOMBRAN COSAS DISTINTAS** (U042: "alfombra o tapete" contra "asiento o mueble
  tapizado"): no rinde. U042 se sigue publicando igual, porque la firma
  identifica "mueble tapizado", y en los controles se perdió T001, que es un
  positivo etiquetado y estable en todas las corridas anteriores. Un positivo
  real menos y ningún falso menos: revertido.
- **Pregunta por los DOS EXTREMOS del eje de la tapa** (la señal exacta que dio
  el dueño para U013): tampoco separa. U013 y los fantasmas T160 y T175 dan el
  mismo 2 de 3 "un extremo salido", y el contenedor quemado real da 2 de 3
  "ambos en su eje". Con estos modelos, "salida del eje de un lado" no es una
  observación confiable: la afirman igual sobre contenedores sanos.
- **"2 de 3 alcanza aunque uno contradiga"** en la validación cruzada: se probó
  y se volvió atrás. El "sí" dirigido es sugestionable: en U003 el modelo que
  había dicho dos veces "cartón" contestó "presente" cuando la pregunta nombró
  tablas.
