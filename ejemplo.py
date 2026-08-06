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

if not data["hay_problema"]:
    print("Sin problema identificable")
else:
    print(f"Gravedad máxima: {data['gravedad_maxima']}/5")
    for p in data["problemas"]:
        # Una entrada puede venir con "key" (categoría propia) o con "codigo"
        # (prestación del catálogo completo de la Ciudad), nunca con las dos.
        ident = p.get("key") or p.get("codigo")
        print(f"{ident:30s} gravedad={p['gravedad']} fuentes={', '.join(p['fuentes'])}")
if data.get("foto_valida") is False:
    print("La foto no corresponde al reclamo; se reportó lo que contó el vecino.")
for s in data["categorias_contexto"]:
    print("Sugerido por el contexto:", s.get("key") or s.get("codigo"), "-", s["nombre"])
for p in data.get("posibles") or []:
    print("Posible (sin confirmar):", p.get("key") or p.get("codigo"))
if data["en_duda"]:
    print("En duda:", ", ".join(data["en_duda"]))
if data.get("descripcion"):
    print("Descripción:", data["descripcion"])

veri = data["detalle"]["verificacion"]
print("Verificación:", "activa" if veri.get("activa") else f"inactiva ({veri.get('motivo')})")
