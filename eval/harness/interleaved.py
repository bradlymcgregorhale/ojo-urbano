#!/usr/bin/env python3
"""Comparacion PAREADA e INTERCALADA voto1 vs voto3.

Corrige el error anterior: las condiciones se corrian en bloques de tiempo
separados y la tasa de flips DERIVA (dos corridas identicas: 13.9% y 7.8%).
Aca, para CADA foto se corren las dos condiciones dos veces cada una, todo
mezclado en orden aleatorio, asi la deriva golpea igual a ambas ramas.

Mide varios endpoints, no solo igualdad de conjuntos: lo que el usuario nota
es si cambia hay_problema, la gravedad o la descripcion.
    interleaved.py <shard> <nshards>
"""
import os
import sys
from pathlib import Path

# Rutas relativas al repo: el harness corre desde cualquier clon.
REPO = Path(__file__).resolve().parents[2]
DATOS = REPO / "eval" / "datos"
sys.path.insert(0, str(REPO))
import json, os, random, sys
AQUI=Path(__file__).resolve().parent
OJO=REPO
_env = OJO/".env"      # opcional: solo hace falta para llamar a los modelos
for l in (_env.read_text().splitlines() if _env.exists() else []):
    l=l.strip()
    if l and not l.startswith("#") and "=" in l:
        k,_,v=l.partition("="); os.environ.setdefault(k.strip(),v.strip())
os.environ.setdefault("TEMPERATURA","0")
import verificador as V
CATS=json.loads((OJO/"categorias.json").read_text())
V._imagen_data_url=lambda img:""
SH=int(sys.argv[1]); NSH=int(sys.argv[2])
CONDS={"voto1":1,"voto3":3}

def corrida(f,votos):
    V.ARBITRO_VOTOS=votos
    cong={v["modelo"]:v for v in f["veredictos"]}
    V._verificar_uno=lambda m,du,cats,ctx="":cong[m]
    V.CONSENSO_VLM_SOLO="confirma"
    r=V.verificar(None,CATS,f["local"],"")
    probs=sorted(c["key"] for c in r["confirmadas"] if c["key"] not in V.PRESENCIA)
    gr=[c["gravedad"] for c in r["confirmadas"]
        if c["key"] not in V.PRESENCIA and c.get("gravedad")]
    arb=r.get("arbitro") or {}
    return {"cats":probs,"hay":bool(probs),"grav":max(gr) if gr else None,
            "desc":(r.get("descripcion") or "")[:200],
            "degradado":bool(arb.get("degradado"))}

filas=[json.loads(l) for l in (DATOS/"evidencia_congelada.jsonl").open()]
mias=[f for i,f in enumerate(filas) if i%NSH==SH]
# reanudable: los kills por limite de tiempo no deben perder lo ya hecho
HECHAS=set()
acum=DATOS/"votacion_pareada.jsonl"  # reanuda del MISMO archivo que escribe
if acum.exists():
    for l in acum.read_text().splitlines():
        try: HECHAS.add(json.loads(l)["ident"])
        except Exception: pass
mias=[f for f in mias if f["ident"] not in HECHAS][:int(os.environ.get("TOPE","40"))]
rng=random.Random(2000+SH)
SALIDA=DATOS/"votacion_pareada.jsonl"   # guarda el resultado, no solo lo imprime
_sal=SALIDA.open("a")
pares=[]   # por foto: para poder hacer McNemar pareado
for f in mias:
    plan=[(c,rep) for c in CONDS for rep in (0,1)]
    rng.shuffle(plan)                      # intercalado + orden aleatorio
    res={p:corrida(f,CONDS[p[0]]) for p in plan}
    fila={"ident":f["ident"]}
    for c in CONDS:
        a,b=res[(c,0)],res[(c,1)]
        fila[c]={"cats":a["cats"]!=b["cats"], "hay":a["hay"]!=b["hay"],
                 "grav":a["grav"]!=b["grav"], "desc":a["desc"]!=b["desc"],
                 "degr":bool(a["degradado"] or b["degradado"])}
    pares.append(fila)
    _sal.write(json.dumps(fila, ensure_ascii=False)+"\n"); _sal.flush()
    print(json.dumps(fila), flush=True)
