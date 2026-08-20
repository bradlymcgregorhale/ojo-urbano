# ojo-urbano — instrucciones para agentes

**Antes de trabajar en este repo, leé [`HISTORY.md`](HISTORY.md)** (en la raíz).
Es la historia, las decisiones, las métricas y las notas de operación del
proyecto. NO está en git (está en `.gitignore`): es la memoria de trabajo local.

**Después de cualquier trabajo sustancial en ojo-urbano, actualizá `HISTORY.md`.**
Agregá una entrada en "Registro de sesiones" con fecha y refrescá la sección que
corresponda (arquitectura, despliegue, métricas, rondas, flywheel, etc.).

**Nunca commitees `HISTORY.md`** ni lo saques del `.gitignore`. No escribas
secretos ahí (contraseñas, claves): referí a Keychain o `.env`, nunca los valores.

Convenciones rápidas (el detalle vive en HISTORY.md):
- Después de tocar `verificador.py` o `servidor.py`, corré `pruebas.py` y dejá 0
  fallas (nunca bajes el conteo de tests sin querer).
- Los cambios grandes de rúbrica (`verificador.py` `_RUBRICA`) van con revisión
  de codex + Opus.
- Deploy a prod: ver la sección "Despliegue y operaciones" de HISTORY.md
  (Cloudways, auth por contraseña desde Keychain, reinicio aislado con el patrón
  de brackets, verificar en conexión aparte).
- Frontend: está EMBEBIDO en `servidor.py`, no hay archivos sueltos.
