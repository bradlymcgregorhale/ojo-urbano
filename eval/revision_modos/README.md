# Revisión humana por modo

Esta herramienta agrega revisión humana a un informe HTML de respuestas guardadas. No ejecuta modelos ni modifica la API. El informe de entrada debe incluir una sección por foto, con su ID y las tarjetas `.mode` y `.review` para revelar resultados.

El directorio privado indicado con `--base` contiene `entrega/manifest.json`, con `fotos` y sus campos `foto`, `archivo` y `sha256`, y `analisis/<foto>-<modo>.json`, con `resultado` para `bajo`, `medio` y `alto`. Las fotos y respuestas permanecen fuera de git. Las rutas de imagen se resuelven desde el HTML de salida.

```sh
python3 eval/revision_modos/revision_humana.py \
  --base /ruta/al/conjunto \
  --entrada /ruta/al/informe-anterior.html \
  --salida /ruta/al/nuevo-informe.html
```

La salida debe ser un archivo nuevo. Para una guía con ejemplos del propio conjunto, `--ejemplos` acepta un JSON privado con objetos `foto` y `descripcion`. Abrir esa guía registra la exposición a esas fotos. Sin ese archivo no se incorporan fotos de ejemplo.

Las decisiones usan el mismo formato de exportación e identidad de conjunto que el informe entregado anteriormente. El cambio de ubicación del código no migra ni reescribe revisiones. Exportá desde el navegador antes de cambiar de dispositivo u origen.

Los puntajes separan servicios de presencia de tipos de contenedor. Excluyen las dudas humanas y muestran respuestas parciales y posibles. No evalúan cantidades, texto libre ni todo el catálogo de la API. Una revisión con sugerencias reveladas no demuestra una evaluación ciega.

## Verificación sin inferencias

```sh
OJO_PUPPETEER=/ruta/al/modulo/puppeteer node eval/revision_modos/pruebas_interfaz.cjs
```

El control crea 100 imágenes sintéticas, respuestas guardadas y un navegador aislado. Comprueba campos sin preselección, completitud, guardado, importación, conflictos, exposición, métricas calculadas de forma independiente y pantalla de celular. No lee ni escribe las decisiones reales del usuario.
