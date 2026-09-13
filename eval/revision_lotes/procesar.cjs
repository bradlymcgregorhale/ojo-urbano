/* Cola reanudable de modo Completo para revisión humana (#42). */
const fs=require('fs'),path=require('path'),crypto=require('crypto'),{spawnSync}=require('child_process');
const base=path.resolve(process.argv[2]||''),out=path.join(base,'analisis');
if(!process.argv[2])throw Error('Indicá la carpeta del lote.');
const plan=JSON.parse(fs.readFileSync(path.join(out,'plan.json')));
if(plan.autorizado!==true||!(plan.tope_usd>0))throw Error('Falta el tope autorizado para este lote.');
const manifest=JSON.parse(fs.readFileSync(path.join(base,'entrega/manifest.json'))),inputs=JSON.parse(fs.readFileSync(path.join(out,'entradas-api.json'))),statePath=path.join(out,'estado.json'),lock=path.join(out,'proceso.lock');
const sha=raw=>crypto.createHash('sha256').update(raw).digest('hex');
if(sha(fs.readFileSync(path.join(base,'entrega/manifest.json')))!==plan.manifest_sha256)throw Error('El manifiesto cambió respecto del lote autorizado.');
const pausa=fs.existsSync(statePath)?JSON.parse(fs.readFileSync(statePath)):{};
if(pausa.pausa_usuario===true||pausa.motivo_pausa==='pausa_usuario_para_correcciones')throw Error('El usuario pausó el lote. No se envían fotos ni se cambia el estado.');
if(fs.existsSync(lock)){const prev=JSON.parse(fs.readFileSync(lock));try{process.kill(prev.pid,0);throw Error('La cola ya tiene un proceso activo.');}catch(e){if(e.code!=='ESRCH')throw e;fs.unlinkSync(lock);}}
fs.writeFileSync(lock,JSON.stringify({pid:process.pid,inicio:new Date().toISOString()}),{flag:'wx'});
const state=fs.existsSync(statePath)?JSON.parse(fs.readFileSync(statePath)):{estado:'preparado',resultados:[],gasto_observado_usd:0,reserva_incierta_usd:0,reserva_acotada_usd:0,activo:null};
function save(){fs.writeFileSync(statePath+'.tmp',JSON.stringify(state,null,2));fs.renameSync(statePath+'.tmp',statePath);}
function render(){const p=spawnSync(plan.python,[path.join(__dirname,'generar.py'),base],{encoding:'utf8'});if(p.status!==0)throw Error('No se pudo actualizar el informe: '+p.stderr.slice(0,500));}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function request(page,method,url,photo){return page.evaluate(async({method,url,photo})=>{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);try{const opts={method,signal:controller.signal};if(photo){const form=new FormData();form.append('file',new File([Uint8Array.from(atob(photo),c=>c.charCodeAt(0))],'foto.jpg',{type:'image/jpeg'}));form.append('modo','alto');form.append('contexto','');form.append('verificar','1');opts.body=form;}const response=await fetch(url,opts);let data;try{data=await response.json();}catch(e){data={detail:'Respuesta no JSON'};}return{http:response.status,data,retry_after:response.headers.get('Retry-After')};}finally{clearTimeout(timer);}}, {method,url,photo});}
async function getJob(page,job){let last;for(let i=0;i<5;i++){try{last=await request(page,'GET','./trabajos/?id='+encodeURIComponent(job),null);if(last.http===200)return last;if(last.http<500&&last.http!==429)return last;}catch(e){last={http:0,data:{detail:e.message}};}await sleep(Math.min(30000,2000*2**i));}return last;}
(async()=>{let browser;try{
 if(state.reserva_incierta_usd)throw Error('Consumo incierto pendiente de conciliación. No se envían más fotos.');
 if(['cambio_de_version','uso_o_verificacion_pendiente','error_sin_resultado'].includes(state.motivo_pausa))throw Error('La pausa requiere una conciliación explícita antes de continuar.');
 if(state.activo&&!state.activo.trabajo)throw Error('Hay un envío previo sin identificador. Conciliar antes de reintentar.');
 const pp=require(plan.puppeteer);browser=await pp.launch({headless:true});const page=await browser.newPage();await page.goto(plan.url,{waitUntil:'domcontentloaded'});
 for(const row of manifest.fotos){
  const id=row.foto;if(state.resultados.some(r=>r.foto===id))continue;
  if(fs.existsSync(path.join(out,id+'-alto.json')))throw Error('Existe una respuesta sin asiento en el estado. Conciliar antes de seguir.');
  if(state.activo&&state.activo.foto!==id)throw Error('El trabajo activo no coincide con la siguiente foto.');
  if(!state.activo&&state.gasto_observado_usd+(state.reserva_acotada_usd||0)+plan.reserva_antes_de_foto_usd>plan.tope_usd){state.estado='pausado';state.motivo_pausa='presupuesto';save();render();return;}
  let response,started=state.activo?.inicio||Date.now();
  if(state.activo?.trabajo)response=await getJob(page,state.activo.trabajo);
  else{
   const source=fs.readFileSync(path.join(base,'entrega',row.archivo)),input=inputs[id],raw=fs.readFileSync(path.join(base,'entrega',input.archivo));if(sha(source)!==input.original_sha256||sha(raw)!==input.sha256||input.original_sha256!==row.sha256||input.sha256!==row.sha256_api||input.archivo!==row.entrada_api)throw Error('La foto o la copia de API cambió.');
   let rejections=0;
   while(true){while(state.no_antes&&Date.now()<state.no_antes)await sleep(Math.min(30000,state.no_antes-Date.now()));started=Date.now();state.estado='en_curso';state.motivo_pausa=null;state.activo={foto:id,modo:'alto',inicio:started,envio_iniciado:true};save();response=await request(page,'POST','./trabajos/',raw.toString('base64'));
    if(response.data.trabajo){state.activo.trabajo=response.data.trabajo;save();break;}
    if(!(response.http===429&&response.data.detail==='demasiados pedidos; probá de nuevo más tarde'))break;
    const ra=Number(response.retry_after),seconds=Number.isFinite(ra)&&ra>0?Math.ceil(ra)+2:3602;state.rechazos_previos=state.rechazos_previos||[];state.rechazos_previos.push({foto:id,http:429,fecha:new Date().toISOString(),espera_segundos:seconds,trabajo_creado:false});state.activo=null;state.no_antes=Date.now()+seconds*1000;state.motivo_pausa='espera_limite_pedidos';save();render();if(++rejections>=12)throw Error('Rechazos repetidos de límite. Revisar la cola.');
   }
  }
  if(![200,202].includes(response.http)){state.motivo_pausa='http_'+response.http;state.ultimo_error=response.data;throw Error('HTTP '+response.http+'. No reenviar sin conciliación.');}
  const job=state.activo?.trabajo||response.data.trabajo||null,cache=!job,pollStart=Date.now();
  while(job&&!['listo','error'].includes(response.data.estado)){
   if(Date.now()-pollStart>15*60*1000){state.motivo_pausa='espera_trabajo';throw Error('Trabajo pendiente; se conserva su identificador para consultar.');}
   await sleep(4000);response=await getJob(page,job);if(response.http!==200){state.motivo_pausa='consulta_'+response.http;throw Error('Consulta de trabajo fallida; no reenviar.');}
  }
  if(response.data.estado==='error'||!response.data.resultado){state.reserva_incierta_usd+=plan.reserva_antes_de_foto_usd;state.ultimo_error=response.data;state.motivo_pausa='error_sin_resultado';throw Error('Trabajo sin resultado. Conciliar consumo.');}
  const r=response.data.resultado,record={foto:id,modo:'alto',trabajo:job,cache,fecha:new Date().toISOString(),segundos:(Date.now()-started)/1000,entrada_api:inputs[id],resultado:r};
  fs.writeFileSync(path.join(out,id+'-alto.json'),JSON.stringify(record,null,2));state.resultados.push({foto:id,modo:'alto',archivo:id+'-alto.json',cache,costo_api:r.costo_api,tokens_api:r.tokens_api,analisis_estado:r.analisis_estado});
  if(!cache){if(typeof r.costo_api==='number'&&Number.isFinite(r.costo_api)&&r.costo_api>=0)state.gasto_observado_usd+=r.costo_api+0.000001;else state.reserva_incierta_usd+=plan.reserva_antes_de_foto_usd;if(r.tokens_api_completos!==true)state.reserva_incierta_usd=Math.max(state.reserva_incierta_usd,plan.reserva_antes_de_foto_usd);}
  const changed=state.modo_version&&state.modo_version!==r.modo_version;state.modo_version=state.modo_version||r.modo_version;state.activo=null;state.no_antes=Date.now()+Math.max(0,61000-record.segundos*1000);delete state.error_ejecucion;save();render();console.log(id,r.analisis_estado,'resultados',state.resultados.length,'USD conocidos',state.gasto_observado_usd.toFixed(6));
  if(r.modo!=='alto'||!r.modo_version||changed){state.motivo_pausa='cambio_de_version';throw Error('Versión de modo inesperada. Resultado conservado.');}
  if(state.reserva_incierta_usd||r.analisis_estado==='sin_verificacion'){state.motivo_pausa='uso_o_verificacion_pendiente';throw Error('Conciliar consumo o verificación antes de continuar.');}
 }
 state.estado='terminado';state.motivo_pausa=null;save();render();console.log('TERMINADO',state.resultados.length);
}finally{if(browser)await browser.close();}})().catch(e=>{state.estado='pausado';state.error_ejecucion=e.message;save();try{render();}catch(_){}console.error(e.message);process.exitCode=1;}).finally(()=>{try{fs.unlinkSync(lock);}catch(_){}});
