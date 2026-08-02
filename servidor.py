#!/usr/bin/env python3
"""Ojo Urbano: API de clasificación de fotos de incidencias urbanas.

Un modelo propio (embeddings CLIP + DINOv2 + SigLIP2 con cabezal de regresión
logística multi-etiqueta, entrenado con miles de fotos reales etiquetadas a
mano) clasifica la foto localmente. Si hay una OPENROUTER_API_KEY configurada,
dos modelos de visión verifican el resultado de forma independiente y un
árbitro de texto resuelve los desacuerdos (ver verificador.py).

Uso:
    pip install -r requirements.txt
    cp .env.example .env   # completar OPENROUTER_API_KEY (opcional)
    python servidor.py

Abrí http://127.0.0.1:8080 y arrastrá una foto, o usá POST /clasificar.
La primera ejecución descarga los modelos de embeddings (varios GB).
"""
import io
import json
import os
from pathlib import Path

# carga .env si existe (sin dependencias extra)
_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    for linea in _env.read_text().splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import joblib
import numpy as np
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image
from sentence_transformers import SentenceTransformer

import verificador

AQUI = Path(__file__).resolve().parent
MODELO = AQUI / "model.joblib"
CATEGORIAS = json.loads((AQUI / "categorias.json").read_text())
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
UMBRAL = float(os.environ.get("UMBRAL", "0.5"))
GRAV_MAX = 5

if not MODELO.exists():
    raise SystemExit("Falta model.joblib junto a servidor.py")

bundle = joblib.load(MODELO)
clf = bundle["clf"]
clases = list(bundle.get("classes") or clf.classes_)
sev_model = bundle.get("sev_model")
combo = bundle.get("embed_model", "clip-ViT-B-32").lower()

print("Cargando CLIP (la primera vez se descarga)...")
clip = SentenceTransformer("clip-ViT-B-32")

_dino, _siglip = {}, {}


def dino_vec(img):
    if not _dino:
        import torch
        from transformers import AutoImageProcessor, AutoModel
        print("Cargando facebook/dinov2-base...")
        _dino["torch"] = torch
        _dino["proc"] = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
        _dino["model"] = AutoModel.from_pretrained("facebook/dinov2-base").eval()
    with _dino["torch"].no_grad():
        out = _dino["model"](**_dino["proc"](images=img, return_tensors="pt"))
    v = out.pooler_output[0].numpy().astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-8)


def siglip_vec(img):
    if not _siglip:
        import torch
        from transformers import AutoModel, AutoProcessor
        nombre = "google/siglip2-so400m-patch14-384"
        print(f"Cargando {nombre}...")
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _siglip["torch"] = torch
        _siglip["dev"] = dev
        _siglip["proc"] = AutoProcessor.from_pretrained(nombre)
        _siglip["model"] = AutoModel.from_pretrained(nombre).to(dev).eval()
    with _siglip["torch"].no_grad():
        inp = _siglip["proc"](images=img, return_tensors="pt").to(_siglip["dev"])
        v = _siglip["model"].get_image_features(**inp)
        # transformers >= 5 devuelve un ModelOutput; antes, el tensor directo
        if not _siglip["torch"].is_tensor(v):
            v = v.pooler_output
        v = v[0].cpu().numpy().astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-8)


def caracteristicas(img):
    partes = [clip.encode([img], convert_to_numpy=True, normalize_embeddings=True)[0]]
    if "dinov2" in combo:
        partes.append(dino_vec(img))
    if "siglip" in combo:
        partes.append(siglip_vec(img))
    return np.concatenate(partes).reshape(1, -1)


def nombre_de(k):
    return CATEGORIAS.get(k, {}).get("nombre", k)


def clasificar_local(img):
    feats = caracteristicas(img)
    proba = clf.predict_proba(feats)[0]
    # pliega las clases sinónimas del modelo a su categoría canónica (max score)
    agg = {}
    for k, p in zip(clases, proba):
        ck = verificador.FOLD.get(k, k)
        agg[ck] = max(agg.get(ck, 0.0), float(p))
    ranking = sorted(agg.items(), key=lambda x: -x[1])
    fmt = lambda lst: [{"key": k, "nombre": nombre_de(k), "score": round(s, 4)}
                       for k, s in lst]
    predichas = [p for p in ranking if p[1] >= UMBRAL] or ranking[:1]

    gravedad = None
    if sev_model is not None:
        raw = float(sev_model.predict(feats)[0])
        gravedad = {"value": int(min(GRAV_MAX, max(1, round(raw)))), "raw": round(raw, 2)}

    return {
        "predichas": fmt(predichas),
        "top5": fmt(ranking[:5]),
        "probabilidades": fmt(ranking),
        "gravedad": gravedad,
        "umbral": UMBRAL,
    }


app = FastAPI(title="Ojo Urbano")


@app.get("/salud")
def salud():
    canonicas = sorted({verificador.FOLD.get(k, k) for k in clases})
    return {"ok": True, "clases": canonicas,
            "verificacion": verificador.disponible(),
            "verificadores": verificador.VERIFICADORES,
            "arbitro": verificador.ARBITRO or None}


@app.post("/clasificar")
async def clasificar(file: UploadFile = File(...), verificar: str = "auto"):
    try:
        img = Image.open(io.BytesIO(await file.read())).convert("RGB")
    except Exception:
        raise HTTPException(400, "no pude leer la imagen")

    local = clasificar_local(img)
    activar = (verificador.disponible() if verificar == "auto"
               else verificar not in ("0", "false", "no"))

    if activar and verificador.disponible():
        veri = verificador.verificar(img, CATEGORIAS, local)
        final = {"categorias": veri["confirmadas"], "en_duda": veri["en_duda"],
                 "descripcion": veri["descripcion"]}
    else:
        motivo = ("falta OPENROUTER_API_KEY" if not verificador.disponible()
                  else "desactivada por parámetro")
        veri = {"activa": False, "motivo": motivo}
        final = {"categorias": [{"key": p["key"], "nombre": p["nombre"],
                                 "gravedad": (local["gravedad"] or {}).get("value"),
                                 "fuentes": ["modelo_local"]}
                                for p in local["predichas"] if p["key"] != "sin_problema"],
                 "en_duda": [], "descripcion": None}

    problemas = [c for c in final["categorias"]
                 if c["key"] not in verificador.PRESENCIA]
    gravedades = [c["gravedad"] for c in problemas if c.get("gravedad")]
    final["gravedad_maxima"] = max(gravedades) if gravedades else None
    final["sin_problema"] = not problemas
    return JSONResponse({"modelo_local": local, "verificacion": veri, "final": final})


@app.get("/", response_class=HTMLResponse)
def portada():
    return PAGINA


PAGINA = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ojo Urbano · clasificador de incidencias urbanas</title>
<style>
  :root{--bg:#f4f4f4;--surface:#fff;--soft:#fafafa;--ink:#111;--muted:#666;
        --muted2:#8a8a8a;--line:#dedede;--line2:#bdbdbd;--bar:#e8e8e8}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;
       background:linear-gradient(180deg,rgba(255,255,255,.86),rgba(255,255,255,0) 320px),var(--bg);color:var(--ink)}
  .wrap{width:min(920px,100%);margin:0 auto;padding:28px 20px 44px}
  .masthead{margin-bottom:20px;padding-bottom:22px;border-bottom:1px solid var(--line)}
  h1{font-size:19px;letter-spacing:.14em;margin:0}
  .tagline{margin-top:3px;color:var(--muted);font-size:13px;font-weight:700}
  .sub{color:var(--muted2);font-size:13px;margin-top:5px}
  .mode{display:inline-block;font-size:12px;padding:3px 10px;border-radius:20px;
        border:1px solid var(--line2);background:var(--surface);color:var(--muted)}
  #drop{margin-top:18px;border:1px dashed var(--line2);border-radius:8px;background:var(--surface);
        padding:44px 20px;text-align:center;cursor:pointer;transition:.15s}
  #drop:hover,#drop.over{border-color:var(--ink);background:var(--soft)}
  #drop p{margin:6px 0;color:var(--muted)}
  #drop strong{color:var(--ink)}
  .grid{display:grid;grid-template-columns:300px minmax(0,1fr);gap:22px;margin-top:22px;align-items:start}
  #preview{width:100%;border-radius:8px;border:1px solid var(--line);background:#111;display:none}
  .res{display:none;border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:18px}
  .res h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:0 0 10px}
  .cats{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
  .cat{border:1px solid var(--line2);border-radius:8px;background:var(--soft);padding:8px 12px}
  .cat b{display:block;font-size:14px}
  .cat span{font-size:12px;color:var(--muted)}
  .desc{display:none;font-size:13.5px;background:var(--soft);border:1px solid var(--line);
        border-radius:8px;padding:10px 12px;margin-bottom:14px}
  .estado{font-size:12.5px;color:var(--muted);margin-bottom:14px}
  .row{margin:9px 0}
  .row .name{display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px}
  .row .name .pct{color:var(--muted)}
  .track{height:8px;background:var(--bar);border-radius:999px;overflow:hidden}
  .track>i{display:block;height:100%;background:var(--ink);border-radius:999px}
  .row:not(:first-child) .track>i{background:#747474}
  .err{font-size:13px;font-weight:700;margin-top:10px}
  .espera{display:none;margin-top:14px;color:var(--muted);font-size:13px}
  .api{margin-top:30px;border-top:1px solid var(--line);padding-top:22px}
  .api h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:0 0 8px}
  .endpoint{font:13px ui-monospace,Menlo,monospace;background:var(--surface);
            border:1px solid var(--line);border-radius:8px;padding:8px 11px;margin-bottom:14px}
  .tabs{display:flex;gap:6px;margin-bottom:8px}
  .tab{padding:5px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);
       cursor:pointer;font:13px inherit;color:var(--muted)}
  .tab.active{color:#fff;border-color:var(--ink);background:var(--ink)}
  pre.code,pre.json{background:#111;color:#f2f2f2;padding:14px;border-radius:8px;overflow:auto;
       font:12.5px/1.55 ui-monospace,Menlo,monospace;margin:0}
  pre.code{display:none}
  pre.code.active{display:block}
  .json-wrap{margin-top:20px}
  @media(max-width:700px){.grid{grid-template-columns:1fr}}
</style></head>
<body><div class="wrap">
  <header class="masthead">
    <h1>OJO URBANO</h1>
    <div class="tagline">Reconocimiento visual de incidencias urbanas</div>
    <div class="sub">Modelo propio + verificación cruzada por IA vía OpenRouter.</div>
  </header>
  <span id="modechip" class="mode">cargando…</span>
  <div id="drop">
    <p><strong>Arrastrá una foto acá</strong> o hacé clic para elegir</p>
    <p>JPG / PNG / WEBP</p>
    <input id="file" type="file" accept="image/*" hidden>
  </div>
  <div class="err" id="err"></div>
  <div class="espera" id="espera">Analizando… la verificación con modelos de visión puede tardar hasta un minuto.</div>
  <div class="grid">
    <img id="preview" alt="">
    <div class="res" id="res">
      <h2>Resultado</h2>
      <div class="cats" id="cats"></div>
      <div class="desc" id="desc"></div>
      <div class="estado" id="estado"></div>
      <div id="bars"></div>
    </div>
  </div>

  <div class="api">
    <h2>API</h2>
    <div class="endpoint"><b>POST</b> <span id="ep"></span> · multipart/form-data, campo <b>file</b> · ?verificar=auto|1|0</div>
    <div class="tabs" id="tabs">
      <button class="tab active" data-l="curl">curl</button>
      <button class="tab" data-l="python">Python</button>
      <button class="tab" data-l="js">JavaScript</button>
    </div>
    <pre class="code active" id="code-curl"></pre>
    <pre class="code" id="code-python"></pre>
    <pre class="code" id="code-js"></pre>
    <div class="json-wrap">
      <h2>Respuesta JSON</h2>
      <pre class="json" id="json">// subí una foto para ver la respuesta real</pre>
    </div>
  </div>
</div>
<script>
const $=s=>document.querySelector(s);
const O=location.origin;
$('#ep').textContent=O+'/clasificar';
const SNIP={
 curl:`curl -s -F "file=@foto.jpg" ${O}/clasificar`,
 python:`import requests\n\nwith open("foto.jpg", "rb") as f:\n    r = requests.post("${O}/clasificar", files={"file": f})\ndata = r.json()\nprint(data["final"]["descripcion"])\nfor c in data["final"]["categorias"]:\n    print(c["key"], c["gravedad"], c["fuentes"])`,
 js:`const fd = new FormData();\nfd.append("file", fileInput.files[0]);\nconst res = await fetch("${O}/clasificar", { method: "POST", body: fd });\nconst data = await res.json();\nconsole.log(data.final.categorias);`
};
['curl','python','js'].forEach(l=>$('#code-'+l).textContent=SNIP[l]);
$('#tabs').onclick=e=>{const b=e.target.closest('.tab');if(!b)return;
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t===b));
  document.querySelectorAll('.code').forEach(c=>c.classList.remove('active'));
  $('#code-'+b.dataset.l).classList.add('active');};
fetch('/salud').then(r=>r.json()).then(h=>{
  $('#modechip').textContent=h.verificacion
    ?'verificación activa: '+h.verificadores.join(' + ')
    :'solo modelo local (sin OPENROUTER_API_KEY)';});
const drop=$('#drop'),file=$('#file');
drop.onclick=()=>file.click();
// Toda la página acepta drop: si la segunda foto cae fuera del recuadro (por
// ejemplo sobre el resultado), el navegador ya no navega al archivo.
['dragover','dragenter'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over')}));
document.addEventListener('drop',ev=>{if(ev.dataTransfer.files[0])enviar(ev.dataTransfer.files[0])});
file.addEventListener('change',()=>{if(file.files[0]){enviar(file.files[0]);file.value='';}});
let ctrl=null;
function enviar(f){
  if(ctrl)ctrl.abort();
  ctrl=new AbortController();
  $('#err').textContent='';$('#espera').style.display='block';
  const img=$('#preview');img.src=URL.createObjectURL(f);img.style.display='block';
  const fd=new FormData();fd.append('file',f);
  fetch('/clasificar',{method:'POST',body:fd,signal:ctrl.signal}).then(r=>{if(!r.ok)throw new Error('no pude leer la imagen');return r.json()})
   .then(d=>{
     $('#espera').style.display='none';$('#res').style.display='block';
     const fin=d.final;
     $('#cats').innerHTML=fin.sin_problema
       ?'<div class="cat"><b>Sin problema identificable</b></div>'
       :fin.categorias.map(c=>`<div class="cat"><b>${c.nombre}</b><span>gravedad ${c.gravedad??'—'} · ${c.fuentes.length} fuente${c.fuentes.length>1?'s':''}</span></div>`).join('');
     const desc=$('#desc');desc.textContent=fin.descripcion||'';
     desc.style.display=fin.descripcion?'block':'none';
     const v=d.verificacion;
     $('#estado').textContent=v.activa
       ?'Verificado por '+v.verificadores.filter(x=>x.ok).map(x=>x.modelo).join(' y ')
         +(fin.en_duda.length?' · en duda: '+fin.en_duda.join(', '):'')
       :'Sin verificación ('+v.motivo+')';
     $('#bars').innerHTML=d.modelo_local.top5.map(t=>`
       <div class="row"><div class="name"><span>${t.nombre}</span><span class="pct">${Math.round(t.score*100)}%</span></div>
       <div class="track"><i style="width:${Math.max(2,Math.round(t.score*100))}%"></i></div></div>`).join('');
     $('#json').textContent=JSON.stringify(d,null,2);
   }).catch(e=>{if(e.name==='AbortError')return;
     $('#espera').style.display='none';$('#err').textContent=e.message});
}
</script></body></html>"""


if __name__ == "__main__":
    estado = "activa" if verificador.disponible() else "desactivada (sin OPENROUTER_API_KEY)"
    print(f"Ojo Urbano -> http://{HOST}:{PORT}  ·  verificación {estado}")
    uvicorn.run(app, host=HOST, port=PORT)
