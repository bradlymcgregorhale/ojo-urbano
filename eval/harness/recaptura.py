#!/usr/bin/env python3
"""Re-captura los veredictos de vision con la RUBRICA ENDURECIDA (#8).

El harness de calidad replaya veredictos congelados, capturados con la
rubrica vieja: no puede ver el cambio. Para saber si el endurecimiento
suprime hallazgos legitimos hay que volver a preguntarle a los VLM.
Solo las fotos adjudicadas, que son las que tienen etiqueta.
Reanudable.
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
import servidor, verificador as V
from PIL import Image
import concurrent.futures as cf
CATS=servidor.CATEGORIAS
SAL=AQUI/"recaptura.jsonl"
adj={x["n"]:x for x in json.load(open(DATOS/"adjudicacion.json"))}
mues={x["n"]:x for x in json.load(open(DATOS/"muestra.json"))}
hechas=set()
if SAL.exists():
    for l in SAL.read_text().splitlines():
        try: hechas.add(json.loads(l)["n"])
        except Exception: pass
pend=[n for n,a in sorted(adj.items()) if not a.get("basura") and n not in hechas]
pend=pend[:int(os.environ.get("TOPE","30"))]
print(f"pendientes esta vuelta: {len(pend)} (hechas {len(hechas)})")
faltan=[]
t0=time.time()
with SAL.open("a") as f:
    for i,n in enumerate(pend,1):
        # muestra.json NO trae rutas ni URLs (privacidad). Las fotos salen
        # del cache que arma el operador desde su base privada.
        cache=DATOS.parent/"fotos_cache"; cache.mkdir(exist_ok=True)
        ruta=cache/(mues[n]["ident"].replace("/","-")+".jpg")
        if not ruta.exists():
            # sin URLs versionadas a proposito: las fotos son de vecinos.
            # El operador arma el cache desde su base privada.
            faltan.append(mues[n]["ident"]); continue
        try: img=Image.open(ruta).convert("RGB")
        except Exception: continue
        local=servidor.clasificar_local(img)
        du=V._imagen_data_url(img)
        with cf.ThreadPoolExecutor(2) as p:
            ver=list(p.map(lambda m: V._verificar_uno(m,du,CATS,""), V.VERIFICADORES))
        f.write(json.dumps({"n":n,"ident":mues[n]["ident"],"local":local,
                            "veredictos":ver}, ensure_ascii=False)+"\n"); f.flush()
        if i%10==0: print(f"  {i}/{len(pend)} ({(time.time()-t0)/i:.0f}s/foto)")
if faltan:
    print(f"!! faltaban {len(faltan)} fotos en eval/fotos_cache/ y se saltearon.")
    print("   Ver eval/README.md: hay que armar el cache desde la base privada.")
print("ok")
