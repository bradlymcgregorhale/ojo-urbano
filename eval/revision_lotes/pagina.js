(()=>{
'use strict';
const $=id=>document.getElementById(id),D=JSON.parse($('datos').textContent),ids=D.fotos.map(p=>p.foto),cats=D.categorias,keys=Object.keys(cats),materialKeys=['recoleccion','retiro_escombros','retiro_muebles','retiro_poda'];
const presence=new Set(['contenedor_humedos_lateral','contenedor_humedos_bilateral','contenedor_secos']);
const hygiene=k=>['Residuos','Contenedores','Cestos papeleros'].includes(cats[k]?.grupo)&&!presence.has(k);
const core=keys.filter(k=>hygiene(k)||presence.has(k)),other=keys.filter(k=>!core.includes(k));
const KEY='ojo-lote-v2:'+D.conjunto,stamp=()=>new Date().toISOString();let state={version:2,conjunto:D.conjunto,actual:ids[0],revisiones:{}},draft=null,blocked=false,pending=null;
const indoorMessage='Necesitamos una foto del objeto o problema en la vía pública. Una foto dentro de una vivienda o espacio privado no sirve para este reclamo.';
const excluded=r=>r?.exclusion==='interior';
const technical=r=>['contexto','calidad'].includes(r?.revision_tecnica);
const QUALITY=['sin_revisar','suficiente','limitada','insuficiente'];
const MOTIVOS_CALIDAD=['desenfoque','movimiento','oscuridad','sobreexposicion','detalle_insuficiente'];
const qualityNames={suficiente:'Se puede evaluar',limitada:'Limitada, pero se entiende',insuficiente:'No se puede evaluar'};
const motiveNames={desenfoque:'desenfoque',movimiento:'movimiento',oscuridad:'oscuridad',sobreexposicion:'sobreexposición',detalle_insuficiente:'falta de detalle'};
function validExtra(r){
 if(r.revision_tecnica!==undefined&&(!technical(r)||r.estado==='aprobado'))throw Error('Revisión técnica inválida.');
 if(r.calidad_humana!==undefined&&!QUALITY.includes(r.calidad_humana))throw Error('Calidad humana inválida.');
 if(r.motivo_calidad!==undefined&&!MOTIVOS_CALIDAD.includes(r.motivo_calidad))throw Error('Motivo de calidad inválido.');
 if(r.revision_tecnica==='calidad'&&(r.calidad_humana!=='insuficiente'||!MOTIVOS_CALIDAD.includes(r.motivo_calidad)))throw Error('La calidad insuficiente exige un motivo concreto.');
 const principal=r.principal_humano;
 if(principal!==undefined&&principal!==null&&principal!=='sin_revisar'){
  if(technical(r)||excluded(r)||r.ambito==='interior')throw Error('Una foto sin evaluar no admite prioridad.');
  if(principal!=='indeterminado'&&(!keys.includes(principal)||presence.has(principal)||r.categorias[principal]!=='confirmado'))throw Error('La prioridad debe ser un problema confirmado en la revisión.');
 }
}
const names={confirmado:'Confirmado',posible:'Posible',no:'No corresponde',sin_revisar:'Sin revisar'},decisions={aceptar:'Aceptar',rechazar:'Rechazar',revision:'Pedir contexto / otra foto'};
function original(id){return D.resultados[id]?.respuesta;}
function suggested(id,a=original(id)){if(!a)return null;const confirmed=new Set([...(a.problemas||[]),...(a.elementos_detectados||[])].map(x=>x.key||x.codigo)),possible=new Set((a.posibles||[]).map(x=>x.key||x.codigo)),doubt=new Set(a.en_duda||[]);const statuses=Object.fromEntries(keys.map(k=>[k,confirmed.has(k)?'confirmado':possible.has(k)?'posible':doubt.has(k)?'sin_revisar':'no']));return {estado:'borrador',original_huella:D.resultados[id].huella,ambito:'sin_revisar',decision:a.hay_reclamo?'aceptar':possible.size||doubt.size?'revision':'rechazar',categorias:statuses,materiales:Object.fromEntries(materialKeys.map(k=>[k,statuses[k]==='confirmado'?'si':statuses[k]==='posible'?'duda':statuses[k]==='sin_revisar'?'sin_revisar':'no'])),explicacion:a.descripcion||'',nota:'',parcial_revisada:false,fecha:null,historial:[]};}
function validate(v){if(!v||v.version!==2||v.conjunto!==D.conjunto||!ids.includes(v.actual)||!v.revisiones||typeof v.revisiones!=='object'||Array.isArray(v.revisiones))throw Error('Conjunto o versión incompatibles.');const clean={version:2,conjunto:D.conjunto,actual:v.actual,revisiones:{}};for(const[id,stored]of Object.entries(v.revisiones)){const r=structuredClone(stored);for(const[g,ks]of [['categorias',keys],['materiales',materialKeys]])if(r?.[g]&&typeof r[g]==='object'&&!Array.isArray(r[g]))for(const k of ks)if(!Object.hasOwn(r[g],k))r[g][k]='sin_revisar';if(!ids.includes(id)||!r||!['borrador','aprobado','corregido'].includes(r.estado)||typeof r.original_huella!=='string'||!/^[a-f0-9]{64}$/.test(r.original_huella)||!['sin_revisar','via_publica','interior','indeterminado'].includes(r.ambito)||!Object.hasOwn(decisions,r.decision))throw Error('Revisión inválida.');for(const[g,ks,vs]of [['categorias',keys,Object.keys(names)],['materiales',materialKeys,['si','no','duda','sin_revisar']]]){if(!r[g]||Object.keys(r[g]).length!==ks.length||ks.some(k=>!vs.includes(r[g][k])))throw Error('Categorías inválidas.');}validExtra(r);if(r.procedencia!==undefined&&(r.estado==='aprobado'||!r.procedencia||typeof r.procedencia!=='object'||!/^[a-f0-9-]{20,64}$/.test(r.procedencia.reanalisis||'')||typeof r.procedencia.modo_version!=='string'||!/^[a-f0-9]{64}$/.test(r.procedencia.huella||'')))throw Error('Procedencia de reanálisis inválida.');if(r.exclusion!==undefined&&r.exclusion!=='interior')throw Error('Exclusión inválida.');if(typeof r.nota!=='string'||r.nota.length>3000||typeof r.explicacion!=='string'||r.explicacion.length>5000||typeof r.parcial_revisada!=='boolean'||!Array.isArray(r.historial)||r.historial.length>10000)throw Error('Contenido de revisión inválido.');if(r.fecha!==null&&!Number.isFinite(Date.parse(r.fecha)))throw Error('Fecha inválida.');if(r.historial.some(x=>!x||typeof x.accion!=='string'||x.accion.length>100||!Number.isFinite(Date.parse(x.fecha))))throw Error('Historial inválido.');if(r.estado!=='borrador'&&!r.fecha)throw Error('Falta fecha de revisión.');if(r.estado!=='borrador'&&ruleError(r,id,false))throw Error('La revisión terminada contradice las reglas de ámbito o completitud.');clean.revisiones[id]={estado:r.estado,original_huella:r.original_huella,ambito:r.ambito,decision:r.decision,categorias:{...r.categorias},materiales:{...r.materiales},explicacion:r.explicacion,nota:r.nota,parcial_revisada:r.parcial_revisada,fecha:r.fecha,historial:r.historial.map(x=>({accion:x.accion,fecha:x.fecha})),...(r.exclusion?{exclusion:r.exclusion}:{}),...(r.revision_tecnica?{revision_tecnica:r.revision_tecnica}:{}),...(r.principal_humano!==undefined?{principal_humano:r.principal_humano}:{}),...(r.calidad_humana?{calidad_humana:r.calidad_humana}:{}),...(r.motivo_calidad?{motivo_calidad:r.motivo_calidad}:{}),...(r.procedencia?{procedencia:{reanalisis:r.procedencia.reanalisis,modo_version:r.procedencia.modo_version,huella:r.procedencia.huella}}:{})};}return clean;}
function notice(text,error=false){$('guardado').textContent=text;$('guardado').classList.toggle('error',error);}
try{const s=localStorage.getItem(KEY);if(s){state=validate(JSON.parse(s));localStorage.setItem(KEY,JSON.stringify(state));}}catch(e){blocked=true;notice('No se pudo recuperar el guardado. No se reemplazará. Exportá esta sesión antes de cerrar. '+e.message,true);}
function save(){if(blocked){notice('Guardado automático detenido para proteger datos existentes. Exportá esta sesión antes de recargar.',true);return false;}try{localStorage.setItem(KEY,JSON.stringify(state));notice('Guardado en este navegador a las '+new Date().toLocaleTimeString('es-AR')+'.');return true;}catch(e){notice('No se pudo guardar. Exportá tu revisión antes de cerrar.',true);return false;}}
window.addEventListener('storage',e=>{if(e.key===KEY&&e.newValue!==JSON.stringify(state)){blocked=true;save();}});
function validDone(id){const r=state.revisiones[id];return r&&r.estado!=='borrador'&&D.resultados[id]?.huella===r.original_huella;}
function updateProgress(){const n=ids.filter(validDone).length;$('avance').textContent=n+' / '+ids.length+' revisadas';$('progreso').max=ids.length;$('progreso').value=n;$('procesadas').textContent=Object.keys(D.resultados).length+' / '+ids.length+' respuestas listas';const cost=D.proceso.gasto_observado_usd;$('consumo').textContent=Number.isFinite(cost)?'USD '+cost.toFixed(4)+' conocidos':'';for(const o of $('foto-select').options)o.textContent=o.value+(validDone(o.value)?' · '+state.revisiones[o.value].estado:!D.resultados[o.value]?' · sin resultado':'');if(!$('resumen').hidden)summary();renderBandeja();}
let filtroBandeja='pendientes';
const fechaFoto=p=>p.fecha?new Date(p.fecha).toLocaleDateString('es-AR',{day:'numeric',month:'short'}):'';
function renderBandeja(){
 const box=$('bandeja');if(!box)return;
 const query=$('buscar-foto').value.trim().toLocaleLowerCase('es-AR');
 const selected=D.fotos.filter(p=>{
  const done=validDone(p.foto),has=!!D.resultados[p.foto];
  return (filtroBandeja==='todas'||filtroBandeja==='pendientes'&&!done||filtroBandeja==='sin_resultado'&&!has||filtroBandeja==='revisadas'&&done)&&(!query||[p.foto,fechaFoto(p),p.fecha||''].join(' ').toLocaleLowerCase('es-AR').includes(query));
 });
 const scrollTop=box.scrollTop,scrollLeft=box.scrollLeft;box.replaceChildren();$('cantidad-bandeja').textContent=selected.length;$('bandeja-vacia').hidden=!!selected.length;
 for(const p of selected){
  const b=document.createElement('button');b.type='button';b.className='photo-row';b.dataset.foto=p.foto;b.setAttribute('aria-current',String(p.foto===state.actual));
  const img=document.createElement('img');img.src=p.archivo;img.alt='';img.loading='lazy';
  const content=document.createElement('span'),title=document.createElement('strong'),date=document.createElement('small'),status=document.createElement('small');
  title.textContent=p.foto;date.textContent=fechaFoto(p)||'Foto del conjunto';status.className='row-state';status.textContent=validDone(p.foto)?(state.revisiones[p.foto].estado==='aprobado'?'Confirmada':'Corregida'):!D.resultados[p.foto]?'Sin analizar':state.revisiones[p.foto]?.estado==='borrador'?'En revisión':'Resultado listo';
  content.append(title,date,status);b.append(img,content);b.onclick=()=>move(p.foto);box.append(b);
 }
 box.scrollTop=scrollTop;box.scrollLeft=scrollLeft;
 document.querySelectorAll('[data-filtro]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.filtro===filtroBandeja)));
}
if($('buscar-foto'))$('buscar-foto').oninput=renderBandeja;
document.querySelectorAll('[data-filtro]').forEach(b=>b.onclick=()=>{filtroBandeja=b.dataset.filtro;renderBandeja();});
function checkpoint(action){if(!draft)return;draft.estado='borrador';draft.fecha=stamp();draft.historial.push({accion:action,fecha:draft.fecha});state.revisiones[state.actual]=structuredClone(draft);$('revision-estado').textContent='Borrador';save();updateProgress();}
function option(value,text){const o=document.createElement('option');o.value=value;o.textContent=text;return o;}
function buildFields(id,ks,group,values){const box=$(id);for(const k of ks){const label=document.createElement('label');label.append(document.createTextNode(cats[k].nombre));const select=document.createElement('select');select.dataset.group=group;select.dataset.key=k;select.setAttribute('aria-label',cats[k].nombre);for(const[v,t]of Object.entries(values))select.append(option(v,t));select.onchange=()=>{draft[group][k]=select.value;if(group==='categorias'&&draft.principal_humano===k&&select.value!=='confirmado')delete draft.principal_humano;checkpoint('Cambio de '+group);fillExtra();};label.append(select);box.append(label);}}
buildFields('categorias',core,'categorias',names);buildFields('otras',other,'categorias',names);buildFields('materiales',materialKeys,'materiales',{si:'Sí',no:'No',duda:'Duda',sin_revisar:'Sin revisar'});
for(const id of ids)$('foto-select').append(option(id,id));
const categoriasAgregadas=new Set();
for(const key of [...keys].sort((a,b)=>cats[a].nombre.localeCompare(cats[b].nombre,'es-AR')))$('agregar-categoria').append(option(key,cats[key].nombre));
function enfocarCategorias(){
 if(!draft)return;
 const a=original(state.actual),important=new Set([...(a?.problemas||[]),...(a?.posibles||[]),...(a?.elementos_detectados||[])].map(x=>x.key||x.codigo));
 for(const k of a?.en_duda||[])important.add(k);
 for(const k of categoriasAgregadas)important.add(k);
 for(const k of keys)if(['confirmado','posible'].includes(draft.categorias[k]))important.add(k);
 document.querySelectorAll('[data-group="categorias"]').forEach(e=>{const k=e.dataset.key;$(important.has(k)?'correcciones-principales':core.includes(k)?'categorias':'otras').append(e.parentElement);});
}
$('agregar-categoria').onchange=()=>{const key=$('agregar-categoria').value;if(!key)return;categoriasAgregadas.add(key);enfocarCategorias();const field=document.querySelector('[data-group="categorias"][data-key="'+key+'"]');field.focus();field.scrollIntoView({block:'center'});$('agregar-categoria').value='';};

function fillEditor(){if(!draft)return;enfocarCategorias();fillExtra();const corrected=state.revisiones[state.actual];$('correccion-guardada').hidden=!(validDone(state.actual)&&corrected?.estado==='corregido');$('correccion-guardada').textContent=corrected?.estado==='corregido'?'Tu corrección está guardada. '+(corrected.decision==='rechazar'?'No corresponde aceptar el reclamo.':corrected.decision==='revision'?'Hace falta otra foto o contexto.':'Confirmaste: '+keys.filter(k=>corrected.categorias[k]==='confirmado'&&!presence.has(k)).map(k=>cats[k].nombre).join(', ')+'.'):'';$('ambito').value=draft.ambito;$('decision').value=draft.decision;$('nota').value=draft.nota;$('explicacion').value=draft.explicacion;$('parcial').checked=draft.parcial_revisada;document.querySelectorAll('[data-group]').forEach(e=>e.value=draft[e.dataset.group][e.dataset.key]);$('ambito-aviso').textContent=draft.ambito==='interior'?'':draft.ambito==='indeterminado'?'Sin ubicación suficiente, pedí contexto o una foto adicional.':'';const indoor=draft.ambito==='interior';$('resultado').hidden=indoor;$('rechazo-interior').hidden=!indoor;$('parcial-label').hidden=indoor||original(state.actual)?.analisis_estado==='completo';for(const id of ['aprobar','aprobar-publica','editar'])$(id).hidden=indoor||technical(draft);if(indoor||technical(draft))$('editor').hidden=true;$('interior').textContent='Rechazar por interior y seguir';$('revision-estado').textContent=excluded(draft)&&draft.estado!=='borrador'?'Rechazada por interior':draft.original_huella!==D.resultados[state.actual]?.huella?'Respuesta modificada: revisar de nuevo':draft.estado+(draft.procedencia?' · propuesta del reanálisis '+draft.procedencia.modo_version:'');const askQuality=!!original(state.actual)&&!indoor&&draft.revision_tecnica!=='contexto'&&!validDone(state.actual);$('calidad-humana').hidden=!askQuality;const q=draft.calidad_humana;for(const [id,value]of [['calidad-suficiente','suficiente'],['calidad-limitada','limitada'],['calidad-insuficiente','insuficiente']])$(id).setAttribute('aria-pressed',String(q===value));$('calidad-elegida').textContent=!q||q==='sin_revisar'?'Elegí una opción antes de confirmar.':qualityNames[q]+(draft.motivo_calidad?' · '+motiveNames[draft.motivo_calidad]:'');$('motivos-calidad').hidden=!askQuality||q!=='insuficiente'||!!draft.motivo_calidad;}
function render(){if(D.reanalisis){setTimeout(refrescarReanalisis,0);const tieneOriginal=!!original(state.actual);$('reanalisar-foto').textContent=tieneOriginal?'Reanalizar esta foto':'Analizar esta foto';$('reanalisar-panel').querySelector('h3').textContent=tieneOriginal?'Volver a analizar':'Analizar esta foto';}categoriasAgregadas.clear();const id=state.actual,p=D.fotos.find(p=>p.foto===id),a=original(id),saved=state.revisiones[id];draft=saved?structuredClone(saved):suggested(id);$('foto').src=p.archivo;$('foto').alt='Foto '+id;$('foto-link').href=p.entrada_api||p.archivo;$('titulo').textContent=id;if($('fecha-foto'))$('fecha-foto').textContent=p.fecha?'Recibida el '+new Date(p.fecha).toLocaleString('es-AR'):'';$('foto-select').value=id;$('resultado-pendiente').hidden=!!a;$('resultado').hidden=!a;$('acciones').hidden=!a;$('atajo-interior').hidden=!a;$('editor').hidden=true;$('mensaje').textContent='';$('revision-estado').textContent=saved?(D.resultados[id]?.huella!==saved.original_huella?'Respuesta modificada: revisar de nuevo':saved.estado):'Pendiente';if(intentoVisible&&intentoVisible.foto!==id)cerrarIntento();$('anterior').disabled=ids.indexOf(id)===0;$('siguiente').disabled=ids.indexOf(id)===ids.length-1;if(a){$('decision-original').textContent=a.hay_reclamo?'Confirma reclamo':(a.posibles||[]).length?'No confirma; informa posibles':(a.en_duda||[]).length?'No confirma; quedan categorías en duda':'No confirma reclamo';$('estado-original').textContent='Modo: Completo · Estado: '+a.analisis_estado+' · USD '+a.costo_api+' · '+(a.tokens_api??'Sin conteo')+' tokens'+(a.tokens_api_completos===false?' (conteo incompleto)':'');$('descripcion-original').textContent=a.descripcion||'Sin explicación';$('json-original').textContent=JSON.stringify(a,null,2);const box=$('categorias-originales');box.replaceChildren();for(const[field,title]of [['problemas','Confirmados'],['posibles','Posibles'],['elementos_detectados','Elementos'],['en_duda','En duda']]){if(!(a[field]||[]).length)continue;const h=document.createElement('h4');h.textContent=title;box.append(h);const ul=document.createElement('ul');for(const v of a[field]||[]){const li=document.createElement('li');li.textContent=typeof v==='string'?(cats[v]?.nombre||v):v.nombre||cats[v.key]?.nombre||v.key||v.codigo;ul.append(li);}if(!ul.children.length){const li=document.createElement('li');li.textContent='Ninguno';ul.append(li);}box.append(ul);}$('parcial-label').hidden=a.analisis_estado==='completo';fillEditor();}updateProgress();}
function move(id){if(!ids.includes(id))return;state.actual=id;save();render();$('foto-link').scrollIntoView({block:'start'});}
function next(){const start=ids.indexOf(state.actual);for(let i=1;i<=ids.length;i++){const id=ids[(start+i)%ids.length];if(D.resultados[id]&&!validDone(id)){move(id);return;}}if(D.reanalisis){for(let i=1;i<=ids.length;i++){const id=ids[(start+i)%ids.length];if(!validDone(id)){move(id);return;}}}$('mensaje').textContent='No quedan respuestas listas pendientes. Exportá tu revisión. Podés actualizar cuando haya más resultados.';}
function ruleError(r,id=state.actual,current=true){try{validExtra(r);}catch(e){return e.message;}if(technical(r)){if(r.exclusion||r.decision!=='revision'||r.ambito==='interior'||keys.some(k=>r.categorias[k]!=='sin_revisar')||materialKeys.some(k=>r.materiales[k]!=='sin_revisar'))return'La revisión técnica no adjudica categorías ni demuestra interior.';if(r.revision_tecnica==='calidad'&&(r.calidad_humana!=='insuficiente'||!MOTIVOS_CALIDAD.includes(r.motivo_calidad)))return'Elegí por qué la calidad no alcanza: desenfoque, movimiento, oscuridad, sobreexposición o falta de detalle.';if(current&&(!D.resultados[id]||r.original_huella!==D.resultados[id].huella))return'La respuesta original cambió o falta.';return'';}if(excluded(r)){if(r.ambito!=='interior'||r.decision!=='rechazar'||r.estado==='aprobado'||keys.some(k=>r.categorias[k]!=='sin_revisar')||materialKeys.some(k=>r.materiales[k]!=='sin_revisar'))return'El rechazo por interior no permite confirmar categorías ni materiales.';if(current&&(!D.resultados[id]||r.original_huella!==D.resultados[id].huella))return'La respuesta original cambió o falta: esta revisión debe actualizarse.';return'';}if(current&&!['suficiente','limitada'].includes(r.calidad_humana||''))return'Indicá si la calidad alcanza para evaluar. Limitada (sucia, oscura, pocos píxeles) todavía puede evaluarse. No es interior ni encuadre.';
if(r.ambito==='sin_revisar')return'Elegí dónde está el material o usá el botón que confirma vía pública.';if(r.ambito==='interior'&&(r.decision==='aceptar'||keys.some(k=>hygiene(k)&&r.categorias[k]==='confirmado')))return'Una escena interior no permite aceptar un reclamo de higiene. Usá Corregir.';if(r.ambito==='indeterminado'&&r.decision==='aceptar')return'El ámbito es indeterminado: pedí contexto antes de aceptar.';if(r.decision==='aceptar'&&!keys.some(k=>!presence.has(k)&&r.categorias[k]==='confirmado'))return'Para aceptar, confirmá al menos una categoría de problema.';if(r.decision==='rechazar'&&keys.some(k=>!presence.has(k)&&r.categorias[k]==='confirmado'))return'La decisión rechaza el reclamo pero todavía hay problemas confirmados. Corregí esas categorías o la decisión.';if(current&&original(id)?.analisis_estado!=='completo'&&!r.parcial_revisada)return'Confirmá que revisaste la respuesta parcial antes de terminar.';if(current&&(!D.resultados[id]||r.original_huella!==D.resultados[id].huella))return'La respuesta original cambió o falta: esta revisión debe actualizarse.';return'';}
function finish(kind){if(!draft)return;const error=ruleError(draft);if(error){$('mensaje').textContent=error;if(error.includes('calidad'))$('calidad-humana').scrollIntoView({block:'center'});else if(error.includes('Elegí dónde'))$('ambito').scrollIntoView({block:'center'});else $('mensaje').scrollIntoView({block:'center'});return;}if(kind==='aprobado'){const s=suggested(state.actual);if(JSON.stringify(draft.categorias)!==JSON.stringify(s.categorias)||JSON.stringify(draft.materiales)!==JSON.stringify(s.materiales)||draft.decision!==s.decision||draft.explicacion!==s.explicacion){$('mensaje').textContent='Cambiaste las sugerencias. Usá Guardar corrección y seguir.';$('editor').hidden=false;return;}delete draft.procedencia;}draft.estado=kind;draft.fecha=stamp();draft.historial.push({accion:kind==='aprobado'?'Resultado aprobado':'Corrección confirmada',fecha:draft.fecha});state.revisiones[state.actual]=structuredClone(draft);const persisted=save();updateProgress();fillEditor();if(persisted)next();else $('mensaje').textContent='No se pudo guardar el rechazo o la revisión. Exportá una copia antes de seguir.';}
function scope(value){
 if(!draft)return;
 const previous=draft.ambito;
 if((previous==='interior'&&value!=='interior')||technical(draft)){
  const fresh=suggested(state.actual);
  draft={...fresh,nota:draft.nota,historial:[...draft.historial]};
  $('editor').hidden=false;
 }
 draft.ambito=value;
 if(value==='interior'){
  delete draft.principal_humano;delete draft.calidad_humana;delete draft.motivo_calidad;draft.exclusion='interior';draft.decision='rechazar';
  draft.categorias=Object.fromEntries(keys.map(k=>[k,'sin_revisar']));
  draft.materiales=Object.fromEntries(materialKeys.map(k=>[k,'sin_revisar']));
  draft.parcial_revisada=false;draft.explicacion=indoorMessage;
  $('editor').hidden=true;
 }else if(value==='indeterminado'&&draft.decision==='aceptar'){
  draft.decision='revision';
  for(const k of keys)if(hygiene(k)&&draft.categorias[k]==='confirmado')draft.categorias[k]='posible';
  draft.explicacion='Hace falta contexto o una foto que permita determinar si el material está en la vía pública.';
  $('editor').hidden=false;
 }
 checkpoint('Cambio de ámbito');fillEditor();
}
function fillExtra(){
 const tech=technical(draft);$('revision-tecnica').hidden=!tech;
 $('revision-tecnica-texto').textContent=tech?(draft.revision_tecnica==='contexto'?'Falta contexto: hace falta una foto más abierta que ubique el problema en la vía pública.':'La calidad no alcanza'+(draft.motivo_calidad?' ('+motiveNames[draft.motivo_calidad]+')':'')+': hace falta otra foto. No es que el problema no exista.'):'';
 $('prioridad-humana').hidden=tech||excluded(draft);
 const select=$('principal-humano');select.replaceChildren(option('sin_revisar','Sin revisar'),option('indeterminado','No se distingue una prioridad'));
 for(const k of keys)if(!presence.has(k)&&draft.categorias[k]==='confirmado')select.append(option(k,cats[k].nombre));
 select.value=draft.principal_humano||'sin_revisar';
}
function technicalReview(reason){
 if(!draft)return;
 draft.revision_tecnica=reason;delete draft.exclusion;delete draft.principal_humano;
 draft.ambito='indeterminado';draft.decision='revision';
 draft.categorias=Object.fromEntries(keys.map(k=>[k,'sin_revisar']));
 draft.materiales=Object.fromEntries(materialKeys.map(k=>[k,'sin_revisar']));
 draft.parcial_revisada=false;
 if(reason==='contexto'){delete draft.calidad_humana;delete draft.motivo_calidad;draft.explicacion='Necesitamos una foto más abierta que muestre el objeto y su ubicación en la vía pública.';}
 else {draft.calidad_humana='insuficiente';draft.explicacion='Necesitamos otra foto por '+motiveNames[draft.motivo_calidad]+'. No es que el problema no exista.';}
 checkpoint('Pedido de otra foto por '+reason);finish('corregido');
}
function markQuality(kind){
 if(!draft)return;
 if(kind==='insuficiente'){delete draft.revision_tecnica;delete draft.motivo_calidad;draft.calidad_humana='insuficiente';checkpoint('Calidad insuficiente, falta motivo');fillEditor();return;}
 delete draft.revision_tecnica;delete draft.motivo_calidad;draft.calidad_humana=kind;checkpoint('Calidad '+kind);fillEditor();
}
$('contexto-insuficiente').onclick=()=>technicalReview('contexto');
$('calidad-suficiente').onclick=()=>markQuality('suficiente');
$('calidad-limitada').onclick=()=>markQuality('limitada');
$('calidad-insuficiente').onclick=()=>markQuality('insuficiente');
document.querySelectorAll('#motivos-calidad [data-motivo]').forEach(b=>b.onclick=()=>{if(!draft)return;draft.motivo_calidad=b.dataset.motivo;technicalReview('calidad');});
$('revisar-clasificacion').onclick=()=>{const anterior=draft;draft={...suggested(state.actual),nota:anterior.nota,historial:[...anterior.historial]};checkpoint('Volver a revisar la clasificación');fillEditor();$('editor').hidden=false;};
$('guardar-prioridad').onclick=()=>{
 if(!draft||technical(draft)||excluded(draft))return;
 draft.principal_humano=$('principal-humano').value;draft.fecha=stamp();
 draft.historial.push({accion:'Revisión de prioridad sin aprobar categorías',fecha:draft.fecha});
 state.revisiones[state.actual]=structuredClone(draft);
 if(save())$('mensaje').textContent='Prioridad guardada. No se aprobaron ni cambiaron las categorías.';else $('mensaje').textContent='No se pudo guardar la prioridad. Exportá una copia antes de seguir.';
};
$('ambito').onchange=()=>scope($('ambito').value);$('interior').onclick=()=>{scope('interior');finish('corregido');};$('aprobar').onclick=()=>finish('aprobado');$('aprobar-publica').onclick=()=>{scope('via_publica');finish('aprobado');};$('editar').onclick=()=>{$('editor').hidden=false;fillEditor();$('editor').scrollIntoView({block:'start',behavior:'smooth'});};$('guardar').onclick=()=>finish('corregido');$('decision').onchange=()=>{draft.decision=$('decision').value;checkpoint('Cambio de decisión');};$('parcial').onchange=()=>{draft.parcial_revisada=$('parcial').checked;checkpoint('Revisión de respuesta parcial');};for(const[id,key]of [['nota','nota'],['explicacion','explicacion']])$(id).oninput=()=>{draft[key]=$(id).value;checkpoint('Cambio de texto');};
$('anterior').onclick=()=>move(ids[ids.indexOf(state.actual)-1]);$('siguiente').onclick=()=>move(ids[ids.indexOf(state.actual)+1]);$('foto-select').onchange=()=>move($('foto-select').value);$('pendiente').onclick=next;$('actualizar').onclick=()=>{if(save())location.reload();};
function download(){const data={...state,...(D.procedencia?{procedencia:D.procedencia}:{}),exportada_en:stamp(),tipo:'revision_de_sugerencias',alcance:'Aprobación o corrección humana con respuesta visible. No es evaluación ciega. Las exclusiones por interior y los pedidos de otra foto por contexto o calidad no evalúan categorías ni materiales. En fotos todavía no revisadas, la calidad suficiente, limitada o insuficiente es una decisión humana aparte. No equiparar suciedad, pocos píxeles o una métrica baja con foto inutilizable. La prioridad humana es una revisión independiente.'};const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='ojo-urbano-lote-revisado-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('exportar').onclick=download;$('importar').onclick=()=>$('archivo').click();
$('archivo').onchange=async()=>{try{const f=$('archivo').files[0];if(!f)return;if(f.size>15_000_000)throw Error('Archivo demasiado grande.');pending=validate(JSON.parse(await f.text()));const conflicts=Object.keys(pending.revisiones).filter(id=>state.revisiones[id]&&JSON.stringify(state.revisiones[id])!==JSON.stringify(pending.revisiones[id]));const box=$('import-preview');box.hidden=false;box.replaceChildren();const p=document.createElement('p');p.textContent=Object.keys(pending.revisiones).length+' revisiones válidas. '+conflicts.length+' tienen diferencias con este navegador.';box.append(p);const button=(title,fn)=>{const b=document.createElement('button');b.textContent=title;b.onclick=fn;box.append(b);};button('Importar conservando mis decisiones existentes',()=>merge(false));if(conflicts.length)button('Respaldar y reemplazar con el archivo',()=>{download();merge(true);});button('Cancelar',()=>{pending=null;box.hidden=true;});}catch(e){notice('No se importó ningún dato: '+e.message,true);}finally{$('archivo').value='';}};
function merge(replace){for(const[id,r]of Object.entries(pending.revisiones)){if(!state.revisiones[id]||replace){const old=state.revisiones[id];state.revisiones[id]=r;if(old)state.revisiones[id].historial=[...old.historial,...r.historial,{accion:'Importación con reemplazo',fecha:stamp()}];}}state.actual=pending.actual;pending=null;$('import-preview').hidden=true;save();render();}
function summary(){const reviews=ids.filter(validDone).map(id=>state.revisiones[id]),approved=reviews.filter(r=>r.estado==='aprobado').length,corrected=reviews.filter(r=>r.estado==='corregido').length,indoor=reviews.filter(r=>r.ambito==='interior').length,pendingScope=reviews.filter(r=>r.ambito==='indeterminado').length;const stale=ids.filter(id=>state.revisiones[id]&&D.resultados[id]?.huella!==state.revisiones[id].original_huella).length;$('resumen-texto').textContent=reviews.length+' revisiones terminadas: '+approved+' aprobaciones y '+corrected+' correcciones. '+indoor+' escenas interiores y '+pendingScope+' de ámbito indeterminado. '+stale+' revisiones requieren cotejar una respuesta modificada. '+(ids.length-reviews.length)+' fotos pendientes o en borrador.';}
$('resumen-btn').onclick=()=>{$('resumen').hidden=!$('resumen').hidden;if(!$('resumen').hidden){summary();$('resumen').scrollIntoView({block:'start'});}};

let consultaReanalisis=false,enviandoReanalisis=false;
function resumenRespuesta(r){return ['problemas','posibles','elementos_detectados'].map(c=>c+': '+(r[c]||[]).map(x=>x.nombre||x.key).join(', ')).join('\n')+'\n'+(r.descripcion||'');}
if(D.anterior){
 $('comparacion-anterior').hidden=false;
 $('comparacion-versiones').textContent='Anterior: '+D.procedencia.version_original+' · Nueva: '+D.procedencia.version_nueva;
 $('comparacion-resumen').textContent=resumenRespuesta(D.anterior);
 $('comparacion-json').textContent=JSON.stringify(D.anterior,null,2);
 $('resultado').querySelector('h3').textContent='Resultado nuevo: revisá esta versión';
 if(D.aviso_reanalisis){const p=document.createElement('p');p.className='notice';p.textContent=D.aviso_reanalisis;$('comparacion-anterior').prepend(p);}
}
async function refrescarReanalisis(){
 if(!D.reanalisis||consultaReanalisis)return;consultaReanalisis=true;
 const id=state.actual;
 try{
  const response=await fetch(D.reanalisis.url+'/estado',{signal:AbortSignal.timeout(5000)}),v=await response.json();if(!response.ok)throw Error(v.error);
if(id!==state.actual)return;
$('reanalisar-ayuda').textContent='Modo Completo · Versión '+v.modo_version+'. Consumo conocido: USD '+v.gasto_usd.toFixed(4)+'. Reservas: USD '+((v.reserva_incierta_usd||0)+(v.reserva_acotada_usd||0)).toFixed(4)+'. Tope: USD '+v.tope_usd+'. Cada clic envía solo esta foto. El lote sigue pausado.';
   if(D.proceso.estado==='manual'){$('consumo').textContent='USD '+v.gasto_usd.toFixed(4)+' conocidos';}
   const sinPresupuesto=v.gasto_usd+(v.reserva_acotada_usd||0)+1>v.tope_usd;
   $('reanalisar-foto').disabled=enviandoReanalisis||!!v.activo||!!v.reserva_incierta_usd||!!v.bloqueo||sinPresupuesto;
  const box=$('reanalisar-intentos');box.replaceChildren();
  const completed=v.intentos.filter(a=>a.foto===id&&a.estado==='listo');
  if(!original(id)&&completed.length)await cargarPrimerResultado(completed[0].id,id);
  if(id!==state.actual)return;
  for(const a of v.intentos.filter(a=>a.foto===id)){
   const p=document.createElement('p');p.textContent=a.inicio+' · '+a.estado+(a.modo_version?' · versión '+a.modo_version:'')+(a.cache?' · Respuesta de caché':'')+(a.error?' · '+a.error:'')+' ';
   if(a.url){const ver=document.createElement('button');ver.type='button';ver.textContent=intentoVisible?.intento===a.id?'Mostrando abajo':'Ver acá';ver.disabled=intentoVisible?.intento===a.id;ver.onclick=()=>mostrarIntento(a.id);p.append(ver);const link=document.createElement('a');link.href=new URL(a.url,D.reanalisis.url).href;link.target='_blank';link.rel='noopener';link.textContent='Abrir en otra pestaña';p.append(link);}box.append(p);
  }
  $('reanalisar-estado').textContent=v.bloqueo||(v.reserva_incierta_usd?'Hay consumo incierto pendiente de conciliación.':v.activo?'Hay un pedido en curso. Podés seguir revisando otras fotos.':sinPresupuesto?'Se alcanzó el tope del reanálisis.':'Listo para reanalizar.');
 }catch(e){$('reanalisar-estado').textContent='El servicio local no responde. No se enviará otra foto automáticamente. '+e.message;$('reanalisar-foto').disabled=true;}
 finally{consultaReanalisis=false;}
}
let intentoVisible=null;
function resumenEvaluacion(r){const e=r.evaluacion_foto,c=r.contexto_visual,partes=[];if(e)partes.push('Ámbito: '+(e.ambito||'sin evaluar')+' · Calidad suficiente: '+(e.calidad_suficiente===true?'sí':e.calidad_suficiente===false?'no':'sin evaluar')+' · Estado: '+(e.estado||'sin evaluar'));if(c)partes.push('Contexto suficiente: '+(c.suficiente===true?'sí':c.suficiente===false?'no':'sin evaluar'));return partes.length?partes.join('\n')+'\n':'';}
function cerrarIntento(){intentoVisible=null;$('reanalisis-inline').hidden=true;}
async function cargarPrimerResultado(attempt,id){
 const r=await fetch(D.reanalisis.url+'/intento/'+attempt,{signal:AbortSignal.timeout(5000)}),v=await r.json();
 if(!r.ok)throw Error(v.error||'No se pudo recuperar el análisis.');
 if(v.foto!==id||!v.primera_vez||!v.resultado||!v.huella_original)return;
 D.resultados[id]={respuesta:v.resultado,huella:v.huella_original,fecha:v.fecha};
 if(id===state.actual)render();
}
async function mostrarIntento(id){
 if(!D.reanalisis)return;
 try{
  const r=await fetch(D.reanalisis.url+'/intento/'+id,{signal:AbortSignal.timeout(5000)}),v=await r.json();if(!r.ok)throw Error(v.error||'No se pudo leer el reanálisis.');
  if(v.foto!==state.actual)throw Error('El reanálisis pertenece a otra foto.');
const a=original(state.actual);intentoVisible=v;
   $('reanalisis-inline-titulo').textContent=(v.primera_vez?'Análisis':'Reanálisis')+' del '+v.fecha+' · versión '+v.modo_version+(v.version_original?' (original del lote: '+v.version_original+')':'');
   const avisos=[v.aviso,v.cache?'Respuesta recuperada de caché: no se ejecutó otra inferencia.':null,!v.primera_vez&&v.modo_version===v.version_original?'La versión coincide con la del lote: no representa una versión corregida distinta.':null,!v.primera_vez&&D.resultados[state.actual]?.huella!==v.original_huella?'La respuesta del lote cambió después de este reanálisis.':null].filter(Boolean).join(' ');
   $('reanalisis-inline-aviso').textContent=avisos;$('reanalisis-inline-aviso').hidden=!avisos;$('reanalisis-inline-aviso').className='notice';
   $('reanalisis-inline-original').textContent=(a?resumenEvaluacion(a)+resumenRespuesta(a):'Sin respuesta original.');
   $('reanalisis-inline-nuevo').textContent=resumenEvaluacion(v.resultado)+resumenRespuesta(v.resultado);
   $('reanalisis-inline-json').textContent=JSON.stringify(v.resultado,null,2);
   $('reanalisis-proponer').disabled=false;$('reanalisis-inline').hidden=false;$('reanalisis-inline').scrollIntoView({block:'nearest'});
  await refrescarReanalisis();
 }catch(e){$('reanalisar-estado').textContent=e.message;}
}
$('reanalisis-cerrar').onclick=()=>{cerrarIntento();refrescarReanalisis();};
$('reanalisis-proponer').onclick=()=>{
  const v=intentoVisible,id=state.actual;if(!v||v.foto!==id||blocked)return;
  if(!D.resultados[id]&&v.primera_vez)D.resultados[id]={respuesta:v.resultado,huella:v.huella_original||v.huella,fecha:v.fecha};
  if(!D.resultados[id])return;
  const s=suggested(id,v.resultado),prev=state.revisiones[id],ambito=prev&&prev.ambito!=='sin_revisar'?prev.ambito:v.resultado.evaluacion_foto?.ambito==='publica'?'via_publica':v.resultado.evaluacion_foto?.ambito==='interior'?'interior':'sin_revisar';
  draft={...s,ambito,nota:prev?.nota||'',historial:prev?structuredClone(prev.historial):[],procedencia:{reanalisis:v.intento,modo_version:v.modo_version,huella:v.huella}};
  if(prev?.calidad_humana&&prev.calidad_humana!=='insuficiente'){draft.calidad_humana=prev.calidad_humana;}
  if(prev?.principal_humano&&draft.categorias[prev.principal_humano]==='confirmado')draft.principal_humano=prev.principal_humano;
  if(ambito==='interior'){draft.decision='rechazar';draft.exclusion='interior';delete draft.calidad_humana;delete draft.principal_humano;}
  $('editor').hidden=false;fillEditor();checkpoint('Propuesta cargada desde el '+(v.primera_vez?'análisis':'reanálisis')+' '+v.modo_version);
  $('mensaje').textContent='Propuesta cargada desde el análisis. Ajustá lo que haga falta y tocá Guardar.';$('editor').scrollIntoView({block:'start'});
};
$('reanalisar-foto').onclick=async()=>{
 if(enviandoReanalisis||!D.reanalisis||blocked)return;
 if(!save())return;
 enviandoReanalisis=true;$('reanalisar-foto').disabled=true;
 const foto=state.actual,requestKey='ojo-reanalisis-pedido:'+D.conjunto+':'+foto+':'+D.reanalisis.modo_version;
 try{
  // Mantener el mismo ID ante una respuesta perdida o una recarga.
  let solicitud=localStorage.getItem(requestKey);
  if(!solicitud){solicitud=crypto.randomUUID();localStorage.setItem(requestKey,solicitud);}
  const r=await fetch(D.reanalisis.url+'/reanalizar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({foto,solicitud}),signal:AbortSignal.timeout(15000)}),v=await r.json();
  if(!r.ok)throw Error(v.error||'No se pudo registrar el pedido.');
  $('reanalisar-estado').textContent='Intento: '+v.estado+(v.error?' · '+v.error:'. El resultado aparecerá en esta sección.');
 }catch(e){$('reanalisar-estado').textContent=e.message+' No se reintentará solo.';}
 finally{enviandoReanalisis=false;await refrescarReanalisis();}
};
if(D.reanalisis){$('reanalisar-panel').hidden=false;setInterval(refrescarReanalisis,5000);}
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT','BUTTON','SUMMARY'].includes(e.target.tagName)||e.ctrlKey||e.metaKey||e.altKey)return;if(e.key==='ArrowRight'){e.preventDefault();move(ids[ids.indexOf(state.actual)+1]);}if(e.key==='ArrowLeft'){e.preventDefault();move(ids[ids.indexOf(state.actual)-1]);}});
const proc=D.proceso;$('estado-proceso').textContent='Procesamiento: '+(proc.estado||'preparación')+(proc.motivo_pausa?' · '+proc.motivo_pausa:'')+'. Los resultados pendientes no cuentan como negativos.';render();if(!blocked)notice('Revisión lista. Las decisiones se guardan al responder.');
})();
