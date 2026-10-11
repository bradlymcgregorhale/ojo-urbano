# Comprobaciones de seguridad del 11 de octubre de 2026

La auditoría sigue abierta: https://github.com/bradlymcgregorhale/ojo-urbano/issues/177.
No hay evidencia suficiente para declarar comprobadas todas sus superficies.

Inspeccioné `main` c6efd7493dbbe776c9ea6fdce9642e6cafcfe230. La API productiva
sigue en 8e5773c456be4faa82fa3b2af6653a568320c615; ese dato se volvió a leer
del despliegue el 11 de octubre. La ronda local recuperada usa c6efd749.
La puerta pública sigue ejecutando la copia antigua cargada en memoria.

Las pruebas activas usan archivos, respuestas, cuentas, certificados y proveedores
sintéticos en loopback. No hice carga destructiva ni nuevas consultas pagas.
Los archivos privados de evidencia conservan entradas y resultados; este informe
no incluye credenciales, fotos ni anotaciones de vecinos.

## Resultado por control

Una fila marcada ✅ identifica el alcance que pasó. Una fila ❌ identifica un
fallo o una comprobación pendiente; no significa que exista una explotación.

| Control | Evidencia y resultado | Estado |
| --- | --- | --- |
| Token de API | La regresión HTTP exige 401 cuando se configura un token y se omite. Producción tiene el token opcional sin configurar, por lo que acepta análisis anónimos por diseño. No demuestra autenticación de usuarios en producción. | ✅ Control condicional comprobado |
| Resultados de trabajos | Inspeccioné identificadores aleatorios de 128 bits, consulta, cancelación y estados. El identificador funciona como credencial de acceso: no hay separación por cuenta. Las regresiones verifican identificadores inexistentes y trabajos pendientes. | ✅ Contrato actual comprobado |
| Tamaño de cargas | Regresiones de Content-Length, bytes, imágenes ilegibles y megapíxeles. Límites actuales: 10 MiB de archivo y 25 millones de píxeles. No hice una carga hostil contra producción. | ✅ Regresiones aisladas |
| Concurrencia y recuperación | Las pruebas cubren cola global y por IP, cancelación, devolución de cupo, abandono y rechazo al agotarse la reserva. No demuestran comportamiento bajo carga distribuida. | ✅ Regresiones aisladas |
| Gasto y caché | Las regresiones separan verificar=0, cuota agotada, caché y verificadores deshabilitados. El recorrido productivo con verificar=0 informó costo cero y no llamó lectores desde su reinicio. La cuota diaria de 500 y el límite de 60 solicitudes por hora/IP están en memoria y se reinician con el proceso; no son un tope financiero persistente. | ✅ Alcance comprobado con esa limitación |
| IP del proxy | El puente PHP reconoce CF-Connecting-IP sólo desde redes de Cloudflare. La prueba por origen con encabezados falsificados mantuvo la dirección real en el log. Pruebas PHP y estilo pasaron. | ✅ Corrección instalada |
| XSS y archivos de revisión | Regresiones sintéticas de JSON incrustado, identificadores, rutas, enlaces externos, ocultos, listados, Host y Origin. La recuperación local sirve sólo en loopback y rechaza POST de análisis. | ✅ Controles comprobados localmente |
| Recuperación humana | El recorrido posterior a instalar recorrió 65 entradas, verificó 58 fotos por SHA y conservó 65 revisiones, propuestas e historial al recargar y exportar. Siete fotos faltan y su edición está bloqueada. | ❌ Recuperación visual incompleta |
| JWT de Access | En la puerta actual versionada, firmas RSA, emisor, audiencia, cuenta, vencimiento, origen y rutas pasan con claves y cuentas sintéticas. La puerta pública devuelve 302 sin sesión; eso sólo comprueba el inicio del control de Access, no la navegación autorizada. | ❌ Falta recorrido público autorizado |
| Cookie alternativa | Probé la omisión de Secure y el envío por HTTP con una credencial sintética. El parche emite Secure, HttpOnly y SameSite=Lax, migra la cookie anterior y evita su envío por HTTP en un navegador real. No comprobé la instalación en la puerta pública antigua. | ❌ Parche integrado, instalación pendiente |
| Dependencias Python | Inventario y auditoría de 56 paquetes efectivos: 54 sin avisos registrados después de actualizar. Volví a comprobar Pillow 12.3.0, urllib3 2.8.0 y fsspec 2026.6.0 instalados. `constraints-security.txt` fija los mínimos para repetir la instalación. | ✅ Paquetes auditables actualizados |
| Variantes CPU | pip-audit omite torch 2.13.0+cpu y torchvision 0.28.0+cpu porque no están en PyPI. Consulté OSV directamente con ambas versiones efectivas y sus versiones públicas: las cuatro consultas no devolvieron avisos. Esa búsqueda comprueba avisos por versión, no integridad de binarios ni bibliotecas nativas. | ✅ Consulta de avisos por versión completada |
| Secretos | Revisé 1083 blobs alcanzables de Git con patrones de OpenRouter, GitHub PAT y cabeceras PEM: cero coincidencias. `.env` productivo tiene permisos 0600. No demuestra ausencia de cualquier secreto o dato personal. | ✅ Búsqueda limitada comprobada |
| Logs y privacidad | Inspeccioné completos los tres logs existentes de la aplicación (1.343.127 bytes, 2027 líneas de uso). La búsqueda de credenciales actuales, patrones de tokens, imágenes incrustadas, campos privados y correos tuvo cero coincidencias. El acceso conserva IP. No hay imágenes ni archivos privados versionados en HEAD. No cubre archivos eliminados, otros tipos de datos personales ni servicios externos. | ✅ Búsqueda limitada comprobada |
| Instrucciones en fotos y contexto | Las regresiones sin red comprueban las guardas deterministas y separación del contexto. Hay evidencia histórica de 100 imágenes adversariales sin servicios confirmados en esa prueba antigua. No repetí ese conjunto con los modelos y la versión actual. | ❌ Validación semántica actual pendiente |
| TLS e infraestructura | La API escucha sólo en 127.0.0.1:8091. El acceso público validó certificado y hostname y negoció TLS 1.3. Las rutas probadas para .env, arranque.log, model.joblib y acceso al directorio privado devolvieron 403. .env tiene permisos 0600. Esto comprueba el recorrido público y la escucha de la aplicación; no el modo TLS de origen de Cloudflare ni la infraestructura del proveedor. | ❌ Comprobaciones de aplicación pasan; TLS de origen pendiente |

## Correcciones y seguimiento

Actualicé las dependencias mediante https://github.com/bradlymcgregorhale/ojo-urbano/pull/186.
La fuente de Pillow confirma el parche del parser EPS en 12.3.0:
https://github.com/python-pillow/Pillow/security/advisories/GHSA-pg7v-jwj7-p798.
La fuente de urllib3 confirma el parche de Deflate en 2.8.0:
https://github.com/urllib3/urllib3/security/advisories/GHSA-gh4c-6fx4-qh6g.
No reproduje esas cargas maliciosas en el servicio público.

Los hallazgos corregidos tienen tickets individuales y pruebas registradas:

- Archivos y revisión: https://github.com/bradlymcgregorhale/ojo-urbano/pull/185.
- Verificación deshabilitada y cuota: https://github.com/bradlymcgregorhale/ojo-urbano/pull/188.
- Proxy e IP: https://github.com/bradlymcgregorhale/ojo-urbano/pull/190.
- Cookie: https://github.com/bradlymcgregorhale/ojo-urbano/issues/195,
  integrada en https://github.com/bradlymcgregorhale/ojo-urbano/pull/197.
- Recuperación: https://github.com/bradlymcgregorhale/ojo-urbano/issues/196,
  avance integrado en https://github.com/bradlymcgregorhale/ojo-urbano/pull/198.

## Pasos para verificar la corrección

1. Consultar la versión desplegada y el inventario efectivo: muestran 8e5773c en
   la API, Pillow 12.3.0 y urllib3 2.8.0. ✅
2. Ejecutar `tests/pruebas.py`, las pruebas HTTP de revisión, las pruebas de la
   puerta y el navegador sintético de cookie: los controles dentro de su alcance pasan: 998 comprobaciones, cero fallas; ocho pruebas HTTP adicionales pasan. ✅
3. Abrir http://127.0.0.1:8772/revision.html, recorrer las entradas, recargar y
   exportar: conserva las 65 revisiones; las fotos pendientes se identifican. ✅
4. Instalar la puerta corregida y comprobar acceso autorizado, rechazo y cookie
   del entorno afectado: pendiente. ❌
5. Completar los controles marcados pendientes, registrar cualquier hallazgo y
   repetir su reproducción después del parche: pendiente. ❌

Resultado esperado: los pasos 1 a 3 muestran ✅ dentro del alcance descrito.
Para cerrar la auditoría, los pasos 4 y 5 también tienen que mostrar ✅ o contar
con una decisión explícita sobre el alcance y riesgo restante. No hay tal decisión
registrada para las filas pendientes.


## Ampliación de infraestructura y privacidad

El 11 de octubre volví a inspeccionar la API desplegada en 8e5773c. Los tres
logs existentes son `arranque_nuevo.log`, `servidor.log` y `arranque.log`; no
hay una subcarpeta de logs con más archivos. Leí su contenido sólo para producir
contadores redactados, sin copiar entradas privadas al informe. Las búsquedas
limitadas anteriores no prueban ausencia de cualquier dato personal.

El certificado público validado vence el 1 de diciembre de 2026. No desactivé
validación TLS ni probé cargas hostiles. El endpoint público de cada ruta privada
probada respondió 403, sin publicar su contenido. Esas cuatro rutas no demuestran
que toda ruta posible esté protegida; las regresiones locales cubren además
traversal, ocultos y enlaces externos.

Los permisos inspeccionados son 0755 para la aplicación y 0775 para private_html.
El archivo .env tiene 0600. No afirmo aislamiento entre todos los usuarios del
servidor administrado ni una auditoría del sistema operativo.

No pude recuperar la configuración original de Access: la credencial existente
de cloudflared devuelve cero aplicaciones a nivel cuenta y 403 al consultar la
zona. No cambié políticas ni credenciales. La herramienta de navegador no dispone
de una sesión utilizable. Sigue pendiente la comprobación pública autorizada.

Consulté también https://api.osv.dev/v1/query con torch 2.13.0+cpu, torch 2.13.0,
torchvision 0.28.0+cpu y torchvision 0.28.0. Las cuatro consultas devolvieron cero
avisos. Así cubrí explícitamente las versiones omitidas por pip-audit en la búsqueda
de avisos. No lo interpreto como una auditoría binaria o una garantía de ausencia
de vulnerabilidades desconocidas.
