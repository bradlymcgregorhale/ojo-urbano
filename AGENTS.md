# ojo-urbano — instrucciones para agentes (codex y otros)

**Leé [`HISTORY.md`](HISTORY.md) y [`CLAUDE.md`](CLAUDE.md) en la raíz antes de
trabajar en este repo.** `HISTORY.md` es la memoria de trabajo del proyecto:
historia, decisiones, métricas y operación. NO está en git (`.gitignore`).

**Después de cualquier trabajo sustancial, actualizá `HISTORY.md`** (entrada con
fecha en "Registro de sesiones" + la sección que corresponda).

**Nunca commitees `HISTORY.md`** ni lo saques del `.gitignore`, y no escribas
secretos ahí (referí a Keychain o `.env`).

Después de tocar `verificador.py` o `servidor.py`, corré `pruebas.py` (0 fallas).
El resto de las convenciones está en `CLAUDE.md` y `HISTORY.md`.
