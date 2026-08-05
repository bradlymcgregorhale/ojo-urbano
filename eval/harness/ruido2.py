#!/usr/bin/env python3
"""Piso de ruido del arbitro, con sharding para correr en paralelo.

    ruido2.py <modo> <shard> <total_shards>
Env: TEMPERATURA, PROVEEDOR_FIJO, ARBITRO (para probar otro modelo).
Cada shard es un PROCESO aparte: verificador usa estado de modulo, asi que
no se puede paralelizar con hilos, pero si con procesos.
"""
import os
import sys
from pathlib import Path

# Rutas relativas al repo: el harness corre desde cualquier clon.
REPO = Path(__file__).resolve().parents[2]
DATOS = REPO / "eval" / "datos"
sys.path.insert(0, str(REPO))
import json, os, sys, time
AQUI=Path(__file__).resolve().parent
OJO=REPO
_env = OJO/".env"      # opcional: solo hace falta para llamar a los modelos
for l in (_env.read_text().splitlines() if _env.exists() else []):
    l=l.strip()
    if l and not l.startswith("#") and "=" in l:
        k,_,v=l.partition("="); os.environ.setdefault(k.strip(),v.strip())
import verificador as V
CATS=json.loads((OJO/"categorias.json").read_text())
V._imagen_data_url=lambda img:""
MODO=sys.argv[1]; SH=int(sys.argv[2]); NSH=int(sys.argv[3])
def corrida(f):
    cong={v["modelo"]:v for v in f["veredictos"]}
    V._verificar_uno=lambda m,du,cats,ctx="":cong[m]
    V.CONSENSO_VLM_SOLO=MODO
    r=V.verificar(None,CATS,f["local"],"")
    return sorted(c["key"] for c in r["confirmadas"] if c["key"] not in V.PRESENCIA)
filas=[json.loads(l) for l in (DATOS/"evidencia_congelada.jsonl").open()]
mias=[f for i,f in enumerate(filas) if i%NSH==SH]
dist=0; usadas=0
for f in mias:
    a=corrida(f); b=corrida(f)
    usadas+=1
    if a!=b: dist+=1
print(json.dumps({"shard":SH,"n":usadas,"distintos":dist,"modo":MODO,
                  "temp":V.TEMPERATURA,"pin":V.PROVEEDOR_FIJO,"arbitro":V.ARBITRO}))
