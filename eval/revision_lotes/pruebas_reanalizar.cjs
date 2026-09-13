'use strict';
const {crearServicio}=require('./reanalizar.cjs'),fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert'),crypto=require('crypto');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),wait=ms=>new Promise(r=>setTimeout(r,ms));
function preparar(){const base=fs.mkdtempSync(path.join(os.tmpdir(),'ojo-reanalisis-')),a=path.join(base,'analisis'),e=path.join(base,'entrega');fs.mkdirSync(a);fs.mkdirSync(e);const raw=Buffer.from('foto sintética'),input={archivo:'foto.jpg',sha256:sha(raw),original_sha256:sha(raw)};fs.writeFileSync(path.join(e,'foto.jpg'),raw);fs.writeFileSync(path.join(e,'manifest.json'),JSON.stringify({fotos:[{foto:'H0001',archivo:'foto.jpg',entrada_api:'foto.jpg',sha256:sha(raw),sha256_api:sha(raw)}]}));fs.writeFileSync(path.join(a,'entradas-api.json'),JSON.stringify({H0001:input}));fs.writeFileSync(path.join(a,'H0001-alto.json'),JSON.stringify({resultado:{modo:'alto',modo_version:'anterior'},fecha:'2026-09-12'}));fs.writeFileSync(path.join(a,'estado.json'),JSON.stringify({pausa_usuario:true}));return base;}
async function termina(s){for(let i=0;i<100&&s.estado().activo;i++)await wait(10);}
(async()=>{
 let calls=0,release;const base=preparar(),config={token:'a'.repeat(64),modo_version:'nueva',tope_usd:3,intervalo_ms:1};
 const r={modo:'alto',modo_version:'nueva',analisis_estado:'completo',costo_api:.02,tokens_api:100,tokens_api_completos:true};
 const transport={version:async()=>'nueva',enviar:async()=>{calls++;await new Promise(resolve=>release=resolve);return{trabajo:'job',estado:'listo',resultado:r}},consultar:async()=>({estado:'listo',resultado:r})};
 const s=crearServicio(base,config,transport),old=fs.readFileSync(path.join(base,'analisis/H0001-alto.json')),batch=fs.readFileSync(path.join(base,'analisis/estado.json'));
 const key=crypto.randomUUID(),first=s.iniciar('H0001',key);assert.equal(s.iniciar('H0001',key).id,first.id);assert.throws(()=>s.iniciar('H0001',crypto.randomUUID()),/curso/);
 await wait(10);assert.equal(calls,1);release();await termina(s);assert.equal(s.estado().intentos[0].estado,'listo');assert.equal(s.estado().gasto_usd,.020001);assert.deepEqual(fs.readFileSync(path.join(base,'analisis/H0001-alto.json')),old);assert.deepEqual(fs.readFileSync(path.join(base,'analisis/estado.json')),batch);
 assert(s.pagina(first.id).includes('Resultado anterior, conservado'));assert(s.pagina(first.id).includes('"version_nueva":"nueva"'));assert.throws(()=>s.iniciar('H9999',crypto.randomUUID()),/ajena/);
 await new Promise(r=>s.server.listen(0,'127.0.0.1',r));const url='http://127.0.0.1:'+s.server.address().port;
 assert.equal((await fetch(url+'/incorrecto/estado')).status,403);assert.equal((await fetch(url+'/'+config.token+'/estado',{headers:{Origin:'https://ajeno.example'}})).status,403);
 assert.equal((await fetch(url+'/'+config.token+'/estado',{headers:{Origin:'null'}})).status,200);
 assert.equal((await fetch(url+'/'+config.token+'/reanalizar',{method:'POST',headers:{'Content-Type':'text/plain'},body:'{}'})).status,415);
 const page=await(await fetch(url+'/'+config.token+'/revision/'+first.id)).text();assert(page.includes('"nueva_huella"'));
 await new Promise(r=>s.server.close(r));
 // Respuesta perdida: un solo POST, consumo incierto y ningún reenvío al reiniciar.
 const failBase=preparar();let failedCalls=0;
 const failTransport={version:async()=>'nueva',enviar:async()=>{failedCalls++;throw Error('respuesta perdida')},consultar:async()=>{throw Error('sin ID')}};
 const failing=crearServicio(failBase,config,failTransport);failing.iniciar('H0001',crypto.randomUUID());await wait(30);assert.equal(failedCalls,1);assert.equal(failing.estado().reserva_incierta_usd,1);assert.throws(()=>failing.iniciar('H0001',crypto.randomUUID()));crearServicio(failBase,config,failTransport);await wait(10);assert.equal(failedCalls,1);
 // Versión distinta no consume; límite reserva un dólar antes de enviar.
 const wrong=crearServicio(preparar(),config,{version:async()=>'otra',enviar:async()=>{throw Error('No debe enviar')}});wrong.iniciar('H0001',crypto.randomUUID());await termina(wrong);assert.equal(wrong.estado().intentos[0].estado,'no_enviado');
 const low=crearServicio(preparar(),{...config,tope_usd:.5},transport);assert.throws(()=>low.iniciar('H0001',crypto.randomUUID()),/tope/);
 // Una reserva conciliada conserva su cota dentro del presupuesto, sin fingir costo conocido.
 const boundBase=preparar(),boundDir=path.join(boundBase,'analisis/reanalisis');fs.mkdirSync(boundDir);fs.writeFileSync(path.join(boundDir,'estado.json'),JSON.stringify({gasto_usd:.1,reserva_incierta_usd:0,reserva_acotada_usd:2,activo:null,intentos:[]}));
 const bounded=crearServicio(boundBase,config,transport);assert.equal(bounded.estado().reserva_acotada_usd,2);assert.equal(bounded.estado().gasto_usd,.1);assert.throws(()=>bounded.iniciar('H0001',crypto.randomUUID()),/tope/);

 // Un preflight fallido se puede reintentar sin quedar bloqueado para siempre.
 const retryBase=preparar();let version='otra',enviados=0;
 const retry=crearServicio(retryBase,config,{version:async()=>version,enviar:async()=>{enviados++;return{estado:'listo',resultado:{...r,descripcion:"$& $` $' $$ <script>"}}}});
 const reqId=crypto.randomUUID();retry.iniciar('H0001',reqId);await termina(retry);version='nueva';const retryId=retry.iniciar('H0001',reqId).id;await termina(retry);assert.equal(enviados,1);
 const retryHtml=retry.pagina(retryId),embedded=JSON.parse(retryHtml.match(/id="datos">(.*?)<\/script>/s)[1]);assert.equal(embedded.resultados.H0001.respuesta.descripcion,"$& $` $' $$ <script>");
 assert.equal(embedded.procedencia.cache,true);assert(embedded.aviso_reanalisis.includes('caché'));assert.equal(retry.estado().gasto_usd,0);
 const oldPath=path.join(retryBase,'analisis/H0001-alto.json'),oldWrapper=JSON.parse(fs.readFileSync(oldPath));oldWrapper.resultado.modo_version='nueva';fs.writeFileSync(oldPath,JSON.stringify(oldWrapper));
 const same=crearServicio(retryBase,config,{version:async()=>'nueva',enviar:async()=>({estado:'listo',resultado:r})});const sameId=same.iniciar('H0001',crypto.randomUUID()).id;await termina(same);assert(same.pagina(sameId).includes('La versión coincide'));
 // Consulta diferida y recuperación de trabajo conocido: jamás repite POST.
 const recoveryBase=preparar(),dir=path.join(recoveryBase,'analisis/reanalisis');fs.mkdirSync(dir);const recoveryId=crypto.randomUUID();
 fs.writeFileSync(path.join(dir,'estado.json'),JSON.stringify({gasto_usd:0,reserva_incierta_usd:0,activo:recoveryId,bloqueo:'Hay un pedido pendiente de conciliación. No se generan más cargos.',intentos:[{id:recoveryId,solicitud:crypto.randomUUID(),foto:'H0001',estado:'pendiente_conciliacion',trabajo:'job',envio_iniciado:true,error:'Consulta fallida',original_huella:sha(fs.readFileSync(path.join(recoveryBase,'analisis/H0001-alto.json')))}]}));
 let queries=0;const recovery=crearServicio(recoveryBase,config,{consultar:async()=>++queries===1?{trabajo:'job',estado:'en_cola'}:{trabajo:'job',estado:'listo',resultado:r},enviar:async()=>{throw Error('No repetir POST')}});await termina(recovery);assert.equal(queries,2);assert.equal(recovery.estado().bloqueo,null);assert.equal(recovery.estado().intentos[0].error,undefined);assert.equal(recovery.estado().gasto_usd,.020001);
 // Caída antes o después del POST, sin ID remoto: recuperar sin reenviar ni ocultar consumo.
 for(const enviado of [false,true]){
  const crashBase=preparar(),crashDir=path.join(crashBase,'analisis/reanalisis'),crashId=crypto.randomUUID();fs.mkdirSync(crashDir);
  fs.writeFileSync(path.join(crashDir,'estado.json'),JSON.stringify({gasto_usd:0,reserva_incierta_usd:0,activo:crashId,intentos:[{id:crashId,foto:'H0001',estado:'preparado',envio_iniciado:enviado}]}));
  const crash=crearServicio(crashBase,config,{enviar:async()=>{throw Error('No reenviar al recuperar')}}),st=crash.estado();
  assert.equal(st.intentos[0].estado,enviado?'pendiente_conciliacion':'no_enviado');assert.equal(st.reserva_incierta_usd,enviado?1:0);assert.equal(st.activo,enviado?crashId:null);if(enviado)assert(st.bloqueo);
 }
 console.log(JSON.stringify({duplicados_bloqueados:true,originales_y_pausa_intactos:true,costo_y_version_controlados:true,fallo_sin_reenvio:true,recuperacion_sin_id:true,origen_host_y_token_restringidos:true,comparacion_independiente:true}));
})().catch(e=>{console.error(e);process.exitCode=1});
