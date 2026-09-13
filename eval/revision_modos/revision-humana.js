(()=>{
'use strict';
const D=JSON.parse(document.getElementById('human-data').textContent), $=id=>document.getElementById(id);
const services=['recoleccion','retiro_escombros','retiro_muebles','retiro_poda'], containers=['contenedor_humedos_lateral','contenedor_humedos_bilateral','contenedor_secos'];
const labels={recoleccion:'Residuos comunes / reciclables',retiro_escombros:'Escombros / restos de obra',retiro_muebles:'Muebles y otros voluminosos',retiro_poda:'Poda / jardinería',contenedor_humedos_lateral:'Húmedos lateral',contenedor_humedos_bilateral:'Húmedos bilateral',contenedor_secos:'Secos / reciclables'};
const modes={bajo:'Económico',medio:'Equilibrado',alto:'Completo'}, ids=D.fotos.map(p=>p.foto), KEY='ojo-revision-humana-v1:'+D.conjunto;
const blank=()=>({materiales:Object.fromEntries(services.map(k=>[k,''])),servicios:Object.fromEntries(services.map(k=>[k,''])),contenedores:Object.fromEntries(containers.map(k=>[k,''])),notas:'',completa:false,exposicion_previa:false,revelada_en:null,guia_en:null,terminada_en:null,actualizada_en:null,historial:[]});
let state={version:1,conjunto:D.conjunto,actual:ids[0],revisiones:{}}, shown=false, pendingImport=null, storageBroken=false, externalConflict=false;
const allowed={materiales:['','si','no','duda'],servicios:['','si','no','duda','no_evaluable'],contenedores:['','si','no','duda']};
const fields={materiales:services,servicios:services,contenedores:containers};
const get=id=>state.revisiones[id]||blank(), current=()=>get(state.actual), stamp=()=>new Date().toISOString();
const message=(text,error=false)=>{$('guardado').textContent=text;$('guardado').classList.toggle('error',error);};
const full=r=>Object.keys(fields).every(g=>fields[g].every(k=>r[g][k]!==''));
function validate(v){
 if(!v||v.version!==1||v.conjunto!==D.conjunto||!ids.includes(v.actual)||!v.revisiones||Array.isArray(v.revisiones)||typeof v.revisiones!=='object')throw Error('El archivo no corresponde a este conjunto o versión.');
 const clean={version:1,conjunto:D.conjunto,actual:v.actual,revisiones:{}};
 for(const [id,r] of Object.entries(v.revisiones)){
  if(!ids.includes(id)||!r||typeof r!=='object')throw Error('El archivo contiene una foto desconocida.');
  const n=blank();
  for(const [g,keys] of Object.entries(fields)){
   if(!r[g]||Object.keys(r[g]).length!==keys.length)throw Error('Faltan categorías o hay categorías desconocidas.');
   for(const k of keys){if(!allowed[g].includes(r[g][k]))throw Error('Una categoría tiene un valor inválido.');n[g][k]=r[g][k];}
  }
  if(typeof r.notas!=='string'||r.notas.length>3000||typeof r.completa!=='boolean'||typeof r.exposicion_previa!=='boolean')throw Error('Notas o estado de revisión inválidos.');
  n.notas=r.notas;n.completa=r.completa;n.exposicion_previa=r.exposicion_previa;
  for(const k of ['revelada_en','guia_en','terminada_en','actualizada_en']){if(r[k]!==null&&(typeof r[k]!=='string'||!Number.isFinite(Date.parse(r[k]))))throw Error('Fecha inválida.');n[k]=r[k];}
  if(!Array.isArray(r.historial)||r.historial.length>10000||r.historial.some(x=>!x||typeof x.accion!=='string'||x.accion.length>100||typeof x.fecha!=='string'||!Number.isFinite(Date.parse(x.fecha))))throw Error('Historial inválido.');
  n.historial=r.historial.map(x=>({accion:x.accion,fecha:x.fecha}));
  if(n.completa&&(!full(n)||!n.terminada_en))throw Error('Una revisión terminada tiene categorías sin responder.');
  clean.revisiones[id]=n;
 }
 return clean;
}
try{const saved=localStorage.getItem(KEY);if(saved)state=validate(JSON.parse(saved));}catch(e){storageBroken=true;message('No se pudo recuperar el guardado. No se reemplazará: exportá esta sesión y conservá cualquier copia anterior. '+e.message,true);}
function save(){
 if(externalConflict){message('Otra pestaña cambió la revisión. Exportá esta sesión antes de recargar; no se reemplazará el guardado de la otra pestaña.',true);return false;}
 if(storageBroken){message('Guardado automático no disponible. Exportá tu revisión antes de cerrar.',true);return false;}
 try{localStorage.setItem(KEY,JSON.stringify(state));message('Guardado en este navegador a las '+new Date().toLocaleTimeString('es-AR')+'.');return true;}catch(e){message('No se pudo guardar en el navegador. Exportá tu revisión antes de cerrar.',true);return false;}
}
window.addEventListener('storage',e=>{if(e.key===KEY&&e.newValue!==JSON.stringify(state)){externalConflict=true;save();}});
function revise(action,edit){const r=current();edit(r);r.actualizada_en=stamp();r.historial.push({fecha:r.actualizada_en,accion:action});state.revisiones[state.actual]=r;save();$('foto-estado').textContent=r.completa?'Revisión terminada.':'Borrador con cambios guardados. Terminá la revisión para incluirla en los puntajes.';progress();}
function expose(id,kind){const r=get(id),key=kind==='guia'?'guia_en':'revelada_en';if(!r[key]){r[key]=stamp();r.actualizada_en=r[key];r.historial.push({fecha:r[key],accion:kind==='guia'?'Consulta de guía':'Resultados revelados'});state.revisiones[id]=r;}}
function progress(){const n=Object.values(state.revisiones).filter(r=>r.completa).length;$('avance').textContent=n+' / '+ids.length;$('progreso').value=n;for(const o of $('foto-select').options)o.textContent=o.value+(get(o.value).completa?' · revisada':'');if(!$('puntajes').hidden)renderMetrics();}
function fieldHTML(g,k){const values=allowed[g].filter(Boolean),word={si:'Sí',no:'No',duda:'No puedo determinarlo',no_evaluable:'No evaluable'};return '<fieldset class="human-field"><legend>'+labels[k]+'</legend><div class="human-options">'+values.map(v=>'<label><input type="radio" name="'+g+'.'+k+'" value="'+v+'">'+word[v]+'</label>').join('')+'</div></fieldset>';}
for(const g of Object.keys(fields))$(g).innerHTML=fields[g].map(k=>fieldHTML(g,k)).join('');
$('foto-select').innerHTML=ids.map(id=>'<option value="'+id+'">'+id+'</option>').join('');
function exposureText(r){const tags=[];if(r.exposicion_previa)tags.push('Exposición previa declarada');if(r.guia_en)tags.push('Foto vista en la guía');if(r.revelada_en)tags.push('Resultados revelados');$('exposicion-aviso').textContent=tags.join('. ');}
function render(){
 const p=D.fotos.find(p=>p.foto===state.actual),r=current();shown=false;$('resultados-revelados').hidden=true;$('resultados-foto').replaceChildren();
 $('foto-select').value=state.actual;$('foto-humana').src=p.archivo;$('foto-humana').alt='Foto '+p.foto;$('foto-grande').href=p.archivo;$('foto-titulo').textContent=p.foto+' · tu evaluación';
 $('foto-estado').textContent=r.completa?'Revisión terminada. Si cambiás una respuesta, vuelve a borrador.':'Borrador. Respondé cada categoría para terminar; las dudas son válidas.';
 for(const [g,keys] of Object.entries(fields))for(const k of keys)for(const input of document.getElementsByName(g+'.'+k))input.checked=input.value===r[g][k];
 $('notas').value=r.notas;$('exposicion-previa').checked=r.exposicion_previa;exposureText(r);$('form-mensaje').textContent='';$('revelar').textContent='Revelar resultados de esta foto';
 $('anterior').disabled=ids.indexOf(state.actual)===0;$('siguiente').disabled=ids.indexOf(state.actual)===ids.length-1;progress();
}
function move(id){if(!ids.includes(id))return;state.actual=id;save();render();$('foto-grande').scrollIntoView({block:'start'});}
function nextPending(){const start=ids.indexOf(state.actual);for(let n=1;n<=ids.length;n++){const id=ids[(start+n)%ids.length];if(!get(id).completa){move(id);return;}}$('form-mensaje').textContent='Terminaste las '+ids.length+' fotos. Exportá una copia de tu revisión.';$('puntajes').hidden=false;renderMetrics();}
$('form-revision').addEventListener('change',e=>{if(e.target.type==='radio'){const [g,k]=e.target.name.split('.');revise('Cambio de categoría',r=>{r[g][k]=e.target.value;r.completa=false;});$('foto-estado').textContent='Borrador con cambios guardados. Terminá la revisión para incluirla en los puntajes.';}});
$('notas').addEventListener('input',()=>revise('Cambio de nota',r=>{r.notas=$('notas').value;r.completa=false;}));
$('exposicion-previa').addEventListener('change',()=>{revise('Exposición previa declarada',r=>{r.exposicion_previa=$('exposicion-previa').checked;r.completa=false;});exposureText(current());});
$('form-revision').addEventListener('submit',e=>{e.preventDefault();if(!full(current())){const missing=[];for(const [g,keys] of Object.entries(fields))for(const k of keys)if(!current()[g][k])missing.push(labels[k]);$('form-mensaje').textContent='Falta responder: '+missing.join(', ')+'. Podés marcar una duda.';return;}revise('Revisión terminada',r=>{r.completa=true;r.terminada_en=stamp();});nextPending();});
$('anterior').onclick=()=>move(ids[ids.indexOf(state.actual)-1]);$('siguiente').onclick=()=>move(ids[ids.indexOf(state.actual)+1]);$('pendiente').onclick=nextPending;$('foto-select').onchange=()=>move($('foto-select').value);
$('revelar').onclick=()=>{shown=!shown;$('resultados-revelados').hidden=!shown;$('revelar').textContent=shown?'Ocultar resultados':'Revelar resultados de esta foto';if(shown){expose(state.actual,'resultados');save();exposureText(current());const out=$('resultados-foto');out.replaceChildren();const row=$(state.actual);for(const node of row.querySelectorAll('.mode,.review')){const copy=node.cloneNode(true);copy.querySelectorAll('.comparison').forEach(n=>n.remove());out.append(copy);}out.scrollIntoView({block:'start'});}};
$('guia').addEventListener('toggle',()=>{if($('guia').open){for(const id of D.ejemplos||[])expose(id,'guia');save();exposureText(current());}});
$('ver-informe').onclick=()=>{for(const id of ids)expose(id,'resultados');save();$('human-app').hidden=true;$('legacy').hidden=false;window.scrollTo(0,0);};
$('volver-revision').onclick=()=>{$('legacy').hidden=true;$('human-app').hidden=false;render();window.scrollTo(0,0);};
function download(){const doc={...state,exportada_en:stamp(),criterios:'revision-humana-1',advertencia:'Referencia humana separada de la original. Material visible no equivale a servicio. Exposición registrada; no asumir revisión ciega.'};const a=document.createElement('a'),u=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'}));a.href=u;a.download='ojo-urbano-revision-humana-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),30000);}
$('exportar').onclick=download;
$('importar').onclick=()=>$('archivo-revision').click();
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
function previewImport(parsed){const incoming=validate(parsed),conflicts=Object.keys(incoming.revisiones).filter(id=>state.revisiones[id]&&!equal(state.revisiones[id],incoming.revisiones[id]));pendingImport=incoming;const box=$('import-preview');box.hidden=false;box.replaceChildren();const p=document.createElement('p');p.textContent='Archivo válido: '+Object.keys(incoming.revisiones).length+' fotos guardadas. '+conflicts.length+' tienen diferencias con este navegador. Exportá tu revisión actual antes de reemplazar.';box.append(p);const button=(label,fn)=>{const b=document.createElement('button');b.textContent=label;b.onclick=fn;box.append(b);};button('Importar y conservar mis decisiones existentes',()=>mergeImport(false));if(conflicts.length)button('Reemplazar las decisiones en conflicto con las del archivo',()=>{download();mergeImport(true);});button('Cancelar',()=>{pendingImport=null;box.hidden=true;});}
function mergeImport(replace){for(const [id,r] of Object.entries(pendingImport.revisiones)){if(!state.revisiones[id]||replace){const old=state.revisiones[id];const n=structuredClone(r);if(old){n.revelada_en=old.revelada_en||n.revelada_en;n.guia_en=old.guia_en||n.guia_en;n.exposicion_previa=old.exposicion_previa||n.exposicion_previa;n.historial=[...old.historial,...n.historial,{fecha:stamp(),accion:'Importación con reemplazo'}];}state.revisiones[id]=n;}}state.actual=pendingImport.actual;pendingImport=null;$('import-preview').hidden=true;save();render();}
$('archivo-revision').onchange=async()=>{try{const f=$('archivo-revision').files[0];if(!f)return;if(f.size>5000000)throw Error('El archivo supera el tamaño admitido.');previewImport(JSON.parse(await f.text()));}catch(e){message('No se importó ningún dato: '+e.message,true);}finally{$('archivo-revision').value='';}};
const zero=()=>({tp:0,fp:0,fn:0,tn:0,pendientes_positivos:0,posibles:0,excluidos:0,confirmados_excluidos:0});
function score(keys,mode){const z=zero();for(const id of ids){const r=get(id);if(!r.completa)continue;const pred=D.modos[id][mode];for(const k of keys){const v=(services.includes(k)?r.servicios:r.contenedores)[k],yes=pred.confirmados.includes(k),possible=!yes&&pred.posibles.includes(k);if(v!=='si'&&v!=='no'){z.excluidos++;if(yes)z.confirmados_excluidos++;continue;}if(possible)z.posibles++;if(v==='si'){if(yes)z.tp++;else if(possible)z.pendientes_positivos++;else z.fn++;}else if(yes)z.fp++;else z.tn++;}}return z;}
const pct=(n,d)=>d?(100*n/d).toFixed(1)+'% ('+n+'/'+d+')':'Sin casos';
function table(groups){let h='<div class="human-breakdown"><table><thead><tr><th>Modo / categoría</th><th>Precisión</th><th>Detección</th><th>Confirmaciones incorrectas</th><th>Omisiones</th><th>Positivos pendientes</th><th>Posibles evaluables</th><th>Ejes excluidos</th></tr></thead><tbody>';for(const [title,keys] of groups)for(const mode of Object.keys(modes)){const s=score(keys,mode);h+='<tr><td>'+modes[mode]+' / '+title+'</td><td>'+pct(s.tp,s.tp+s.fp)+'</td><td>'+pct(s.tp,s.tp+s.fn+s.pendientes_positivos)+'</td><td>'+s.fp+'</td><td>'+s.fn+'</td><td>'+s.pendientes_positivos+'</td><td>'+s.posibles+'</td><td>'+s.excluidos+' ('+s.confirmados_excluidos+' confirmados)</td></tr>';}return h+'</tbody></table></div>';}
function renderMetrics(){const reviewed=ids.filter(id=>get(id).completa),n=reviewed.length;const exposed=reviewed.filter(id=>{const r=get(id);return r.exposicion_previa||(r.revelada_en&&r.revelada_en<=r.terminada_en)||(r.guia_en&&r.guia_en<=r.terminada_en);}).length;let h='<p><strong>'+n+' de '+ids.length+' fotos revisadas.</strong> '+exposed+' con exposición declarada o registrada antes de terminar la revisión. Las revisiones históricas ya vistas fuera de esta página deben declararse manualmente. No se asume que las demás sean ciegas.</p>';if(n!==ids.length)h+='<p class="human-warn">Muestra parcial elegida por el revisor. Todavía no representa el conjunto completo.</p>';h+=table([['Servicios',services],['Contenedores',containers]]);h+='<details><summary>Ver cada categoría</summary>'+table(Object.keys(labels).map(k=>[labels[k],[k]]))+'</details><h3>Costos conocidos y respuestas parciales</h3><ul>';for(const [mode,name] of Object.entries(modes)){const out=reviewed.map(id=>D.modos[id][mode]),costs=out.filter(r=>typeof r.costo==='number'),sum=costs.reduce((v,r)=>v+r.costo,0);h+='<li>'+name+': '+(costs.length?'USD '+(sum/costs.length).toFixed(6)+' promedio conocido en '+costs.length+' fotos':'sin fotos revisadas')+'. '+out.filter(r=>r.estado!=='completo').length+' respuestas parciales o sin verificación; '+out.filter(r=>r.consumo_completo===false).length+' con consumo incompleto.</li>';}h+='</ul><p class="human-muted">Los estados parciales se conservan. Los costos incompletos son mínimos conocidos. Dudas: no aumentan ni aciertos ni errores. Una categoría posible no se trata como confirmada.</p>';$('puntajes-contenido').innerHTML=h;}
$('ver-puntajes').onclick=()=>{$('puntajes').hidden=!$('puntajes').hidden;if(!$('puntajes').hidden){renderMetrics();$('puntajes').scrollIntoView({block:'start'});}};
render();if(!storageBroken)message('Revisión lista. Las decisiones se guardan automáticamente al responder.');
})();
