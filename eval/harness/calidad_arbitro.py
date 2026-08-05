#!/usr/bin/env python3
"""¿El arbitro denso es estable Y ademas acierta? Un modelo que rechaza todo
seria perfectamente estable e inutil. Se mide contra la adjudicacion a ojo.
    calidad_arbitro.py <shard> <nshards>   env: ARBITRO
"""
import os
import sys
from pathlib import Path

# Rutas relativas al repo: el harness corre desde cualquier clon.
REPO = Path(__file__).resolve().parents[2]
DATOS = REPO / "eval" / "datos"
sys.path.insert(0, str(REPO))
import json
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
adj={x["n"]:x for x in json.load(open(DATOS/"adjudicacion.json"))}
mues={x["n"]:x for x in json.load(open(DATOS/"muestra.json"))}
cap={f["ident"]:f for f in (json.loads(l) for l in open(DATOS/"evidencia_congelada.jsonl"))}
usables=[n for n,a in adj.items() if not a.get("basura") and mues[n]["ident"] in cap]
mias=[n for i,n in enumerate(sorted(usables)) if i%NSH==SH]
tp=fp=fn=0; conf=0
for n in mias:
    f=cap[mues[n]["ident"]]
    cong={v["modelo"]:v for v in f["veredictos"]}
    if len(cong)<2: continue
    V._verificar_uno=lambda m,du,cats,ctx="":cong[m]
    V.CONSENSO_VLM_SOLO="confirma"
    r=V.verificar(None,CATS,f["local"],"")
    pred={c["key"] for c in r["confirmadas"] if c["key"] not in V.PRESENCIA}
    gold=set(adj[n]["visible"])-V.PRESENCIA
    tp+=len(gold&pred); fp+=len(pred-gold); fn+=len(gold-pred); conf+=len(pred)
print(json.dumps({"shard":SH,"tp":tp,"fp":fp,"fn":fn,"confirmadas":conf,
                  "arbitro":V.ARBITRO,"n":len(mias)}))
