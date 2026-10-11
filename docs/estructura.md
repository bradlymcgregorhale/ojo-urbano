# Estructura del repositorio

## Ubicaciones

| Ubicación | Contenido |
| --- | --- |
| Raíz | API y módulos de producción, configuración, catálogos y modelo local. |
| `tests/` | Pruebas de la aplicación y ejecutor de integración sin pesos. |
| `tools/` | Herramientas manuales de generación y servidor de revisión. |
| `examples/` | Cliente de ejemplo que llama a una API en ejecución. |
| `eval/` | Evaluación, entrenamiento y revisión de lotes, con sus propias pruebas. |
| `prompts/` | Rúbrica y preguntas dirigidas; su versión participa en las huellas. |
| `deploy/` | Puente PHP e instrucciones de despliegue. |
| `docs/` | Documentación de estructura y mantenimiento. |

Las fotos y respuestas privadas de `eval/vision/private/` siguen ignoradas. No se trasladan al código ni se publican en GitHub.

## Módulos de producción

- `especialista_contenedores.py`: Inventario y estado de contenedores. Dependencias internas directas: ninguna.
- `evaluacion_foto.py`: Ámbito, calidad y encuadre de la foto. Dependencias internas directas: ninguna.
- `guard_modelo.py`: Candado del proceso que carga los pesos. Dependencias internas directas: ninguna.
- `modos_analisis.py`: Perfiles, límites y trazabilidad de los modos. Dependencias internas directas: ninguna.
- `observaciones_higiene.py`: Publicación de observaciones de higiene. Dependencias internas directas: ninguna.
- `politica_escombros.py`: Decisiones de material, ámbito y retiro. Dependencias internas directas: `revision_escombros_publica`.
- `prioridad.py`: Problema principal y solicitudes sugeridas. Dependencias internas directas: ninguna.
- `revision_fragmentos.py`: Distingue fragmentos del daño y pilas independientes. Dependencias internas directas: `prompts`, `modos_analisis`, `verificador`.
- `revision_cordon.py`: Corroboración dirigida del cordón. Dependencias internas directas: `prompts`.
- `revision_escombros_publica.py`: Salida pública de la revisión de material. Dependencias internas directas: ninguna.
- `revision_omisiones.py`: Recuperación de objetos, residuos y cubierta. Dependencias internas directas: `prompts`.
- `revision_vereda.py`: Geometría y corroboración de daños en vereda. Dependencias internas directas: `prompts`.
- `servidor.py`: API, modelo local, cola y página de análisis. Dependencias internas directas: `especialista_contenedores`, `evaluacion_foto`, `modos_analisis`, `observaciones_higiene`, `politica_escombros`, `prioridad`, `revision_cordon`, `revision_fragmentos`, `revision_escombros_publica`, `revision_omisiones`, `revision_vereda`, `verificador`.
- `verificador.py`: Lectores, transporte, consenso y arbitraje. Dependencias internas directas: `evaluacion_foto`, `modos_analisis`, `observaciones_higiene`, `prioridad`, `prompts`, `revision_escombros_publica`.

Los módulos de producción permanecen juntos en la raíz. `servidor.py`, `verificador.py`, `prompts/` y los evaluadores usan rutas e imports desde esa ubicación. Trasladarlos ahora a un paquete agregaría cambios en arranque, recursos y huellas sin reducir código duplicado. La separación de pruebas y herramientas deja 15 Python en la raíz: 14 de producción y el comando compatible de pruebas. No se eliminó un módulo por su nombre o por la cantidad de archivos.

## Comandos

Pruebas de la aplicación, HTTP local y consenso:

```bash
python3 -m venv .venv-tests
.venv-tests/bin/pip install -r requirements-test.txt
.venv-tests/bin/python pruebas.py
.venv-tests/bin/python -m unittest tests.test_intermediario -v
```

`pruebas.py` es una entrada pequeña a `tests.pruebas`. Conserva el comando anterior. El ejecutor bloquea conexiones externas, usa sustitutos del modelo pesado y conserva las comprobaciones existentes. Las pruebas de sockets, API y PHP usan únicamente loopback. No necesita clave de OpenRouter ni descarga embeddings.

Las pruebas co-localizadas bajo `eval/` permanecen con sus herramientas. Sus comandos están en `eval/vision/README.md` y `eval/revision_lotes/README.md`.

Aplicación y herramientas:

```bash
python servidor.py
python examples/ejemplo.py foto.jpg
python tools/generar_tool.py /ruta/al/lote
python tools/servidor_revision.py /ruta/al/lote 8772
```

El ejemplo envía la foto a la API y puede generar gasto si esa API usa lectores externos. El generador usa archivos guardados y no consulta proveedores. El servidor de revisión debe usarse solo en loopback y con una carpeta privada conocida.

## Recursos y despliegue

La aplicación sigue resolviendo `.env`, `model.joblib`, los catálogos y los prompts desde la raíz. No cambia el comando de arranque, el contrato de la API ni los archivos de producción. Las herramientas resuelven recursos del repositorio desde su carpeta padre. Las pruebas hacen lo mismo para catálogos, fuente del servidor y puente PHP.

Para desplegar la aplicación, conservar esos recursos juntos. No es necesario copiar `tests/`, `tools/`, `examples/`, documentación ni evidencia privada al servicio. El cambio de estructura se revierte con el commit que hizo los movimientos; las decisiones humanas no se modifican.

## Inventario de Python

Cada archivo versionado tiene una ubicación y función en el árbol siguiente. Los tests bajo `eval/` son regresiones de su herramienta, no scripts de producción.

- `examples/ejemplo.py`
- `especialista_contenedores.py`
- `eval/analizar.py`
- `eval/harness/adversario.py`
- `eval/harness/calidad_arbitro.py`
- `eval/harness/capturar.py`
- `eval/harness/estabilidad.py`
- `eval/harness/interleaved.py`
- `eval/harness/inyeccion.py`
- `eval/harness/recaptura.py`
- `eval/harness/regresion_piezas.py`
- `eval/harness/ruido2.py`
- `eval/revision_lotes/consolidada/generar.py`
- `eval/revision_lotes/generar.py`
- `eval/revision_lotes/preparar_recientes.py`
- `eval/revision_lotes/regresiones.py`
- `eval/revision_lotes/reproducir_poda.py`
- `eval/revision_lotes/test_recientes.py`
- `eval/revision_lotes/test_regresiones.py`
- `eval/revision_lotes/test_reproducir_retiros.py`
- `eval/revision_lotes/test_revision_tecnica.py`
- `eval/revision_modos/revision_humana.py`
- `eval/vision/dataset.py`
- `eval/vision/evaluar_inventario.py`
- `eval/vision/local_training.py`
- `eval/vision/pipeline.py`
- `eval/vision/run.py`
- `eval/vision/test_evaluar_inventario.py`
- `eval/vision/test_pipeline.py`
- `eval/vision/test_runner.py`
- `eval/vision/test_view_training.py`
- `eval/vision/view_training.py`
- `evaluacion_foto.py`
- `tools/generar_tool.py`
- `guard_modelo.py`
- `modos_analisis.py`
- `observaciones_higiene.py`
- `politica_escombros.py`
- `prioridad.py`
- `prompts/__init__.py`
- `tests/pruebas.py`
- `revision_cordon.py`
- `revision_fragmentos.py`
- `revision_escombros_publica.py`
- `revision_omisiones.py`
- `revision_vereda.py`
- `servidor.py`
- `tools/servidor_revision.py`
- `tests/test_aptitud_higiene.py`
- `tests/test_contenedores.py`
- `tests/test_descripcion_principal.py`
- `tests/test_especialista_contenedores.py`
- `tests/test_evaluacion_foto.py`
- `tests/test_intermediario.py`
- `tests/test_modos_analisis.py`
- `tests/test_observaciones_higiene.py`
- `tests/test_politica_escombros.py`
- `tests/test_presencia_contenedores.py`
- `tests/test_prioridad.py`
- `tests/test_recoleccion_pertenencias.py`
- `tests/test_revision_cordon.py`
- `tests/test_revision_fragmentos.py`
- `tests/test_revision_escombros_publica.py`
- `tests/test_revision_omisiones.py`
- `tests/test_revision_vereda.py`
- `tests/test_tokens_api.py`
- `verificador.py`
- `pruebas.py`: entrada compatible.
- `tests/__init__.py`: paquete de pruebas.

## Protección de las herramientas de revisión

El generador presenta las descripciones y metadatos como texto, neutraliza cierres de script y rechaza IDs o rutas inválidas. Las decisiones recuperadas del disco se aplican a cada tarjeta, y una edición muestra guardando hasta confirmar la escritura.

El servidor escucha solo en loopback. Rechaza Host u Origin externos, enlaces fuera de la carpeta, archivos ocultos y listados de directorios. GET y HEAD comparten la restricción de rutas. El guardado admite solo un objeto de decisiones, tiene un límite de 5 MB y no sigue enlaces de estado ni temporales preexistentes. No exponer este servidor directamente a Internet; una bandeja remota requiere su propio control de acceso.

`tests/test_herramientas_revision.py` contiene las regresiones HTTP y del generador, incluidas en `python pruebas.py`.

## Dependencias y seguridad

`requirements.txt` y `requirements-test.txt` aplican `constraints-security.txt`. Ese archivo fija las versiones corregidas de Pillow, urllib3 y fsspec verificadas en la auditoría. Las dependencias transitivas reciben la misma restricción, sin instalar el conjunto pesado para ejecutar pruebas.

Una actualización debe contrastar el inventario realmente instalado con avisos del proveedor, resolver compatibilidad, ejecutar las pruebas y comprobar la versión después del reinicio. Conservar un respaldo de las versiones anteriores para revertir. No usar `pip-audit --fix` sobre el entorno productivo sin revisar su selección de cambios.

`revision_fragmentos.py` compara el daño del cordón con restos sueltos, sólo cuando ambos servicios están confirmados en Completo. Tres lecturas concordantes son necesarias para excluir el retiro duplicado.
