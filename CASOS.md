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
| U013 | contenedor roto no reportado | abierto | la pregunta por el USO y la pregunta por la POSICIÓN de la tapa dan las dos 2-de-3 "está entera"; la reformulación posicional se midió y se descartó (confirmaba el fantasma T109). Falta la señal visual concreta |
| U014 | dos bolsas opacas sin reportar recolección | fijado | rúbrica de `retiro_poda`: en escena mixta se reportan las dos |
| U022 | bilateral publicado como lateral | fijado | chequeo de los postes citados: mayoría "sin postes" levanta la guardia |
| U030 | foto ilegible, no debería emitirse juicio | abierto | sus métricas caen en la mediana del corpus; suprimir ahí suprime la mitad de las fotos buenas |
| U032 | la descripción inventa una caja de cartón | abierto | familia "la prosa afirma lo que las categorías no sostienen" |
| U035 | escombros afirmados en foto que no lo permite | abierto | ídem; sale de la fusión con el modelo local |
| U042 | vehículo sin evidencia de infracción; cartón como voluminoso | abierto | |
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
- **"2 de 3 alcanza aunque uno contradiga"** en la validación cruzada: se probó
  y se volvió atrás. El "sí" dirigido es sugestionable: en U003 el modelo que
  había dicho dos veces "cartón" contestó "presente" cuando la pregunta nombró
  tablas.
