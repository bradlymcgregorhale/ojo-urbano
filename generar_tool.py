#!/usr/bin/env python3
"""Genera la herramienta de revision approve/correct: muestra la prediccion de
la API por foto; las categorias seleccionadas se ven como chips y se editan en
un panel desplegable; Confirmar oculta la tarjeta y salta a la siguiente.
Exporta CSV con prediccion de la API y veredicto humano."""
import json
from pathlib import Path

import sys
OJO = Path(__file__).resolve().parent
if len(sys.argv) < 2:
    raise SystemExit("uso: generar_tool.py <carpeta_revision> [sufijo]")
REV = Path(sys.argv[1])

cats_info = json.load(open(OJO / "categorias.json"))
canon = json.load(open(REV / "categorias_modelo.json"))
preds = json.load(open(REV / "predicciones.json"))

# clave de localStorage: la ronda 1 conserva su clave historica para no
# perder el progreso ya hecho; las demas carpetas derivan de su nombre.
# El sufijo opcional (argv[2]) versiona el lote: al re-muestrear la misma
# carpeta se pasa v2/v3 para que el estado viejo no contamine ids nuevos.
_suf = sys.argv[2] if len(sys.argv) > 2 else "v1"
KEY = ("revision_modelo_200_v1" if REV.name == "revision_humana_200"
       else f"revision_{REV.name}_{_suf}")

ORD = ["Residuos", "Contenedores", "Higiene", "Espacio público", "Otros"]
def gkey(k):
    g = cats_info.get(k, {}).get("grupo", "Otros")
    return (ORD.index(g) if g in ORD else len(ORD), g, k)
cats = sorted(canon, key=gkey)
CATS = [{"key": k, "nombre": cats_info.get(k, {}).get("nombre", k),
         "grupo": cats_info.get(k, {}).get("grupo", "Otros")} for k in cats]

PHOTOS = [{"id": p["id"], "archivo": p["archivo"], "pred": p["pred"],
           "grav": p["gravedad"], "en_cache": p["en_cache"],
           "desc": p.get("descripcion", ""), "posibles": p.get("posibles", []),
           "duda": p.get("en_duda", []), "calidad": p.get("calidad", {})}
          for p in preds]

html = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Revisión API ojo-urbano</title>
<style>
:root{color-scheme:dark}
body{font-family:system-ui,sans-serif;margin:0;background:#111;color:#eee}
header{position:sticky;top:0;background:#1b1b1b;padding:10px 16px;border-bottom:1px solid #333;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:10}
header b{font-size:15px}
button{background:#2d6cdf;color:#fff;border:0;padding:8px 14px;border-radius:6px;cursor:pointer;font-size:14px}
button.sec{background:#444}
button.big{font-size:15px;padding:10px 22px;font-weight:600}
.warn{font-size:12px;color:#e0a030;max-width:480px}
#prog{font-variant-numeric:tabular-nums;font-weight:600}
.card{max-width:780px;margin:16px auto;background:#1b1b1b;border:1px solid #333;border-radius:10px;overflow:hidden}
.card.rev{border-color:#2e7d32}
.card.corr{border-color:#c58a1a}
body.ocultar .card.hecha{display:none}
.card img{width:100%;display:block;background:#000;max-height:62vh;object-fit:contain}
.body{padding:12px 16px}
.modelo{background:#16233a;border:1px solid #294a7a;border-radius:6px;padding:8px 10px;margin:6px 0;font-size:13px}
.modelo b{color:#7fb0ff}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px;min-height:26px}
.chip{background:#1f4023;border:1px solid #2e7d32;color:#b9e8c2;padding:4px 10px;border-radius:14px;font-size:13px;display:inline-flex;align-items:center;gap:6px}
.chip u{cursor:pointer;text-decoration:none;color:#e88;font-weight:700}
.chips .vacio{color:#888;font-size:13px;padding:4px 0}
details.panel{margin:6px 0;border:1px solid #3a3a3a;border-radius:8px;background:#202020}
details.panel summary{cursor:pointer;padding:9px 12px;font-size:14px;color:#9cc0ff;user-select:none}
details.panel .inner{padding:4px 12px 12px}
.grp{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#888;margin:10px 0 4px}
.cats{display:flex;flex-wrap:wrap;gap:6px}
.cats label{display:inline-flex;align-items:center;gap:6px;background:#262626;border:1px solid #3a3a3a;padding:5px 9px;border-radius:6px;cursor:pointer;font-size:12px}
.cats input:checked+span{font-weight:700;color:#7fb0ff}
.row{display:flex;gap:14px;align-items:center;margin-top:12px;flex-wrap:wrap}
select{background:#262626;color:#eee;border:1px solid #3a3a3a;border-radius:6px;padding:6px 8px;font-size:13px}
.badge{font-size:12px;padding:3px 8px;border-radius:12px}
.b-pend{background:#3a3a3a;color:#bbb}.b-ok{background:#1f4023;color:#8fdca0}.b-corr{background:#4a3410;color:#e6c07a}
.nocache{background:#3a2020;border:1px solid #6a3030;color:#e0a0a0;font-size:12px;padding:6px 10px;border-radius:6px;margin:6px 0}
#fin{max-width:780px;margin:24px auto;text-align:center;color:#8fdca0;font-size:16px;display:none}
</style></head><body class="ocultar">
<header>
  <b>Revisión API</b>
  <span id="prog"></span>
  <button class="sec" id="btnver" onclick="verRevisadas()">Ver revisadas</button>
  <button onclick="exportar()">Exportar CSV</button>
  <button class="sec" onclick="if(confirm('¿Borrar todo el progreso?')){localStorage.removeItem(KEY);location.reload()}">Reiniciar</button>
  <span class="warn">⚠ Criterio: lo VISIBLE en la foto. No confirmes en automático.</span>
</header>
<div id="app"></div>
<div id="fin">🎉 No quedan fotos pendientes. Exportá el CSV.</div>
<script>
const PHOTOS = __PHOTOS__, CATS = __CATS__;
const KEY = "__KEY__";
const HAY_SERVIDOR = location.protocol.startsWith('http');
let data = JSON.parse(localStorage.getItem(KEY) || "{}");
// El disco manda sobre el navegador: es lo que comparten la compu y el
// celular. Servido como file:// no hay servidor y se usa solo localStorage.
if(HAY_SERVIDOR) fetch('estado').then(r=>r.json()).then(d=>{
  if(d && Object.keys(d).length){
    data=d; try{localStorage.setItem(KEY, JSON.stringify(data));}catch(e){}
    document.querySelectorAll('[data-id]').forEach(el=>{const p=PHOTOS.find(x=>x.id===el.dataset.id); if(p) paint(p);});
    if(typeof prog==='function') prog();
  }
}).catch(()=>{});
const NAME = Object.fromEntries(CATS.map(c=>[c.key,c.nombre]));
let _sinc=null, _pend=false;
function _marca(txt, mal){
  let e=document.getElementById('sincestado');
  if(!e){ e=document.createElement('div'); e.id='sincestado';
    e.style.cssText='position:fixed;right:10px;bottom:10px;z-index:99;font:12px system-ui;'+
      'padding:6px 12px;border-radius:14px;background:#111;color:#fff;opacity:.85';
    document.body.appendChild(e); }
  e.textContent=txt; e.style.background = mal ? '#8a2b2b' : '#111';
}
function _subir(){
  _pend=false;
  if(!HAY_SERVIDOR){ _marca('guardado solo en este navegador'); return; }
  fetch('estado', {method:'POST', headers:{'Content-Type':'application/json'},
                   body: JSON.stringify(data)})
    .then(r=>{ if(!r.ok) throw 0; _marca('guardado'); })
    .catch(()=>_marca('sin guardar', true));
}
function save(){
  try{ localStorage.setItem(KEY, JSON.stringify(data)); }catch(e){}
  if(_pend) return;                 // agrupa clicks seguidos en una sola subida
  _pend=true; clearTimeout(_sinc); _sinc=setTimeout(_subir, 400);
}
function get(p){
  if(!data[p.id]) data[p.id] = {cats:Object.fromEntries(p.pred.map(k=>[k,true])), grav:p.grav||"", rev:false};
  return data[p.id];
}
function estado(p){
  const d=data[p.id]; if(!d||!d.rev) return "pend";
  const sel = Object.keys(d.cats).filter(k=>d.cats[k]).sort().join(",");
  const mod = [...p.pred].sort().join(",");
  return (sel===mod && String(d.grav)===String(p.grav||"")) ? "ok" : "corr";
}
function prog(){
  const hechas = PHOTOS.filter(p=>data[p.id]&&data[p.id].rev);
  const c = PHOTOS.filter(p=>estado(p)==="corr").length;
  document.getElementById('prog').textContent = hechas.length+" / "+PHOTOS.length+" · "+c+" corregidas";
  document.getElementById('btnver').textContent =
    document.body.classList.contains('ocultar') ? "Ver revisadas ("+hechas.length+")" : "Ocultar revisadas";
  document.getElementById('fin').style.display =
    (hechas.length===PHOTOS.length) ? "block" : "none";
}
function verRevisadas(){ document.body.classList.toggle('ocultar'); prog(); }
function toggle(p,k,on){const d=get(p); d.cats[k]=on; save(); paint(p)}
function quitar(p,k){toggle(p,k,false);
  const cb=document.querySelector('#c_'+p.id+' input[data-k="'+k+'"]'); if(cb) cb.checked=false;}
function setgrav(p,v){const d=get(p); d.grav=v; save(); paint(p)}
function confirmar(p){
  const d=get(p); d.rev=!d.rev; save(); paint(p); prog();
  if(d.rev && document.body.classList.contains('ocultar')){
    const prox = PHOTOS.find(q=>!(data[q.id]&&data[q.id].rev));
    if(prox){ const el=document.getElementById('c_'+prox.id);
      if(el) el.scrollIntoView({behavior:'smooth',block:'start'}); }
  }
}
function chipsHtml(p){
  const d=get(p);
  const sel=CATS.filter(c=>d.cats[c.key]);
  if(!sel.length) return '<span class="vacio">(sin categorías: tildá en "Editar categorías" o dejá vacío si no hay problema)</span>';
  return sel.map(c=>`<span class="chip">${c.nombre} <u onclick="quitar(PHOTOS[${p._i}],'${c.key}')" title="quitar">✕</u></span>`).join('');
}
function paint(p){
  const el=document.getElementById('c_'+p.id); if(!el) return;
  const st=estado(p), d=data[p.id]||{};
  el.className='card'+(st==='ok'?' rev':st==='corr'?' corr':'')+(d.rev?' hecha':'');
  const b=el.querySelector('.badge');
  b.className='badge '+(st==='ok'?'b-ok':st==='corr'?'b-corr':'b-pend');
  b.textContent=st==='ok'?'✓ coincide':st==='corr'?'✎ corregido':'pendiente';
  el.querySelector('.chips').innerHTML=chipsHtml(p);
  el.querySelector('button.big').textContent = d.rev ? 'Reabrir' : 'Confirmar';
}
function card(p,i){
  p._i=i;
  const d=get(p);
  const grupos=[...new Set(CATS.map(c=>c.grupo))];
  const cats=grupos.map(g=>{
    const items=CATS.filter(c=>c.grupo===g).map(c=>
      `<label><input type="checkbox" data-k="${c.key}" ${d.cats[c.key]?'checked':''}
        onchange="toggle(PHOTOS[${i}],'${c.key}',this.checked)"><span>${c.nombre}</span></label>`).join('');
    return `<div class="grp">${g}</div><div class="cats">${items}</div>`;
  }).join('');
  const gv=[1,2,3,4,5].map(v=>`<option value="${v}" ${String(d.grav)===String(v)?'selected':''}>${v}</option>`).join('');
  const pos = (p.posibles&&p.posibles.length)?`<div style="font-size:12px;color:#c58a1a;margin-top:4px">Posibles (no confirmados): ${p.posibles.map(k=>NAME[k]||k).join(', ')}</div>`:'';
  const desc = p.desc?`<div style="font-size:12px;color:#aaa;margin-top:4px;font-style:italic">${p.desc}</div>`:'';
  // en_duda: lo que una mirada dirigida bajó o vio una sola fuente. No viene
  // pre-marcado a propósito; se muestra para poder confirmarlo a mano.
  const duda = (p.duda&&p.duda.length)?`<div style="font-size:12px;color:#7aa7d9;margin-top:4px">En duda (retirado o de una sola fuente): ${p.duda.map(k=>NAME[k]||k).join(', ')}</div>`:'';
  const cal = (p.calidad&&p.calidad.definicion==='limitada')?`<div style="font-size:12px;color:#888;margin-top:4px">Foto de definición limitada (${p.calidad.lado_menor}px, nitidez ${p.calidad.nitidez})</div>`:'';
  const modelo = p.en_cache
    ? `<div class="modelo">La API confirma: <b>${p.pred.map(k=>NAME[k]||k).join(', ')||'(nada)'}</b> · gravedad <b>${p.grav||'-'}</b>${desc}${pos}${duda}${cal}</div>`
    : `<div class="nocache">La API no clasificó esta foto. Etiquetala igual.</div>`;
  return `<div class="card" id="c_${p.id}">
    <img loading="lazy" src="fotos/${p.archivo}">
    <div class="body">
      <div class="row"><span class="badge b-pend">pendiente</span>
        <span style="font-size:12px;color:#888">${p.id} · ${i+1}/${PHOTOS.length}</span></div>
      ${modelo}
      <div class="chips"></div>
      <details class="panel"><summary>Editar categorías…</summary><div class="inner">${cats}</div></details>
      <div class="row">
        <label style="font-size:13px;color:#aaa">Gravedad:
          <select onchange="setgrav(PHOTOS[${i}],this.value)"><option value="">-</option>${gv}</select></label>
        <button class="big" onclick="confirmar(PHOTOS[${i}])">Confirmar</button>
      </div>
    </div></div>`;
}
document.getElementById('app').innerHTML = PHOTOS.map(card).join('');
PHOTOS.forEach(paint); prog();
function exportar(){
  const keys=CATS.map(c=>c.key);
  let csv="id,archivo,en_cache,estado,modelo_pred,modelo_gravedad,humano_final,humano_gravedad\\n";
  PHOTOS.forEach(p=>{
    const d=get(p), st=estado(p);
    const hum=keys.filter(k=>d.cats[k]);
    csv+=[p.id,p.archivo,p.en_cache?1:0,st,
      '"'+p.pred.join(';')+'"',p.grav||"",
      '"'+hum.join(';')+'"',d.grav||""].join(",")+"\\n";
  });
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  a.download="revision_"+"__DIR__"+".csv"; a.click();
}
</script></body></html>"""
html = (html.replace("__PHOTOS__", json.dumps(PHOTOS, ensure_ascii=False))
            .replace("__CATS__", json.dumps(CATS, ensure_ascii=False))
            .replace("__KEY__", KEY)
            .replace("__DIR__", REV.name))
(REV / "index_revision.html").write_text(html, encoding="utf-8")
(REV / "index.html").write_text(html, encoding="utf-8")
print("herramienta:", REV / "index.html", "| clave estado:", KEY)
print("categorias:", len(CATS), "| fotos:", len(PHOTOS),
      "| con prediccion:", sum(1 for p in PHOTOS if p["en_cache"]))