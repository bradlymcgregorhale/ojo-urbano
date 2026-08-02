#!/usr/bin/env python3
"""Ejemplo mínimo: clasificar una foto contra la API local.

    python ejemplo.py foto.jpg ["contexto vecinal opcional"]
"""
import sys

import requests

if len(sys.argv) not in (2, 3):
    raise SystemExit('uso: python ejemplo.py foto.jpg ["contexto vecinal opcional"]')

datos = {"contexto": sys.argv[2]} if len(sys.argv) == 3 else {}
with open(sys.argv[1], "rb") as f:
    r = requests.post("http://127.0.0.1:8080/clasificar", files={"file": f}, data=datos)
r.raise_for_status()
data = r.json()

final = data["final"]
if final["sin_problema"]:
    print("Sin problema identificable")
else:
    for c in final["categorias"]:
        print(f"{c['key']:30s} gravedad={c['gravedad']} fuentes={', '.join(c['fuentes'])}")
if final["en_duda"]:
    print("En duda:", ", ".join(final["en_duda"]))
if final.get("descripcion"):
    print("Descripción:", final["descripcion"])

veri = data["verificacion"]
print("Verificación:", "activa" if veri.get("activa") else f"inactiva ({veri.get('motivo')})")
