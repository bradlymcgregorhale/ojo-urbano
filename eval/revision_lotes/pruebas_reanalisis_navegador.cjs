/* Recorrido completo con transporte sintético, sin pedidos a OpenRouter (#58, #118). */
'use strict';
const {crearServicio}=require('./reanalizar.cjs'),fs=require('fs'),path=require('path'),os=require('os'),crypto=require('crypto'),assert=require('assert'),{spawnSync}=require('child_process');
const pp=require(process.env.OJO_PUPPETEER||'puppeteer'),sha=b=>crypto.createHash('sha256').update(b).digest('hex');

async function paginaAvanzada(context){const page=await context.newPage();await page.evaluateOnNewDocument(()=>document.addEventListener('DOMContentLoaded',()=>document.getElementById('opciones-avanzadas')?.click()));return page;}
(async()=>{
 const base=fs.mkdtempSync(path.join(os.tmpdir(),'ojo-reanalisis-browser-')),a=path.join(base,'analisis'),e=path.join(base,'entrega');fs.mkdirSync(a);fs.mkdirSync(e);
 const bytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');fs.writeFileSync(path.join(e,'foto.jpg'),bytes);
 const row={foto:'H0001',archivo:'foto.jpg',entrada_api:'foto.jpg',sha256:sha(bytes),sha256_api:sha(bytes)};fs.writeFileSync(path.join(e,'manifest.json'),JSON.stringify({fotos:[row]}));fs.writeFileSync(path.join(a,'entradas-api.json'),JSON.stringify({H0001:{archivo:'foto.jpg',sha256:sha(bytes),original_sha256:sha(bytes)}}));
 const old={modo:'alto',modo_version:'anterior',hay_reclamo:true,problemas:[{key:'recoleccion',nombre:'Recolección'}],posibles:[],elementos_detectados:[],descripcion:'Resultado anterior',analisis_estado:'completo',costo_api:.01,tokens_api:10,tokens_api_completos:true};
 fs.writeFileSync(path.join(a,'H0001-alto.json'),JSON.stringify({foto:'H0001',modo:'alto',fecha:'2026-09-12T12:00:00Z',resultado:old}));fs.writeFileSync(path.join(a,'estado.json'),JSON.stringify({pausa_usuario:true}));fs.mkdirSync(path.join(a,'reanalisis'));fs.writeFileSync(path.join(a,'reanalisis/estado.json'),JSON.stringify({gasto_usd:.1,reserva_incierta_usd:0,reserva_acotada_usd:1,activo:null,intentos:[]}));
 let calls=0,fallar=false;const cfg={modo_version:'nueva',token:'b'.repeat(64),tope_usd:3,intervalo_ms:5};
 const s=crearServicio(base,cfg,{version:async()=>'nueva',enviar:async raw=>{assert.deepEqual(raw,bytes);calls++;if(fallar)throw Error('Respuesta perdida');return{trabajo:'job',estado:'en_cola'}},consultar:async()=>({trabajo:'job',estado:'listo',resultado:{...old,modo_version:'nueva',descripcion:'Respuesta nueva $& <b>texto</b>',problemas:[{key:'retiro_poda',nombre:'Retiro de poda'}]}})});
 await new Promise(r=>s.server.listen(0,'127.0.0.1',r));cfg.puerto=s.server.address().port;fs.writeFileSync(path.join(a,'reanalisis-config.json'),JSON.stringify(cfg));
 const build=spawnSync(process.env.OJO_PYTHON||'python3',[path.join(__dirname,'generar.py'),base],{encoding:'utf8'});assert.equal(build.status,0,build.stderr);
 const b=await pp.launch({headless:true,protocolTimeout:15000});try{
  console.log('navegador iniciado');const page=await paginaAvanzada(b),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('file://'+path.join(e,'revision.html'));
  console.log('página cargada');await page.click('#calidad-suficiente');await page.click('#aprobar-publica');const saved=await page.evaluate(()=>localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('ojo-lote-v2:'))));
  console.log('esperando servicio',await page.$eval('#reanalisar-estado',e=>e.textContent));await page.waitForFunction(()=>!document.getElementById('reanalisar-foto').disabled,{polling:100,timeout:5000});assert((await page.$eval('#reanalisar-ayuda',e=>e.textContent)).includes('Reservas: USD 1.0000'));await page.click('#reanalisar-foto');
  console.log('pedido enviado');await page.waitForSelector('#reanalisar-intentos a',{timeout:15000});const link=await page.$eval('#reanalisar-intentos a',e=>e.href);
  console.log('resultado disponible');await page.waitForFunction(()=>document.getElementById('descripcion-original').textContent==='Respuesta nueva $& <b>texto</b>',{timeout:15000});
  assert.equal(await page.$eval('#avance',e=>e.textContent),'0 / 1 revisadas');
  const archive=await page.evaluate(()=>JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('ojo-historial-v1:')))));
  assert.equal(archive.length,1);assert.deepEqual(archive[0].revision,JSON.parse(saved).revisiones.H0001);
  await page.reload();await page.waitForFunction(()=>document.getElementById('descripcion-original').textContent==='Respuesta nueva $& <b>texto</b>');assert.equal(calls,1);
  await page.click('#calidad-suficiente');await page.click('#aprobar-publica');const approved=await page.evaluate(()=>JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('ojo-lote-v2:')))));
  assert.equal(approved.revisiones.H0001.estado,'aprobado');assert.equal(approved.revisiones.H0001.categorias.retiro_poda,'confirmado');
  await page.reload();assert.equal(await page.$eval('#avance',e=>e.textContent),'1 / 1 revisadas');
  await page.waitForFunction(()=>!document.getElementById('reanalisar-foto').disabled);await page.click('#reanalisar-foto');
  await page.waitForFunction(()=>document.getElementById('avance').textContent==='0 / 1 revisadas',{timeout:15000});assert.equal(calls,2);
  await page.click('#editar');await page.select('#ambito','via_publica');await page.click('#calidad-suficiente');await page.click('#guardar');
  const cdp=await page.createCDPSession();await cdp.send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:base});await page.click('#exportar');
  for(let i=0;i<100&&!fs.readdirSync(base).some(f=>f.startsWith('ojo-urbano-lote-revisado-')&&f.endsWith('.json'));i++)await new Promise(r=>setTimeout(r,100));
  const exported=JSON.parse(fs.readFileSync(path.join(base,fs.readdirSync(base).find(f=>f.startsWith('ojo-urbano-lote-revisado-')&&f.endsWith('.json')))));
  assert.equal(exported.historial_revisiones.length,2);assert.equal(exported.revisiones.H0001.estado,'corregido');
  assert.equal(JSON.parse(fs.readFileSync(path.join(a,'H0001-alto.json'))).resultado.descripcion,'Resultado anterior');
  const revisionNueva=await page.evaluate(()=>localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('ojo-lote-v2:'))));
  fallar=true;await page.waitForFunction(()=>!document.getElementById('reanalisar-foto').disabled);await page.click('#reanalisar-foto');await page.waitForFunction(()=>document.getElementById('reanalisar-estado').textContent.includes('conciliación'),{timeout:15000});assert.equal(calls,3);assert.equal(await page.$eval('#descripcion-original',e=>e.textContent),'Respuesta nueva $& <b>texto</b>');assert.equal(await page.evaluate(()=>localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('ojo-lote-v2:')))),revisionNueva);
  await page.setViewport({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  console.log(JSON.stringify({resultado_nuevo_visible:true,entrada_pendiente:true,revision_anterior_archivada:true,recarga_conserva_resultado_y_confirmacion:true,clic_deliberado_genera_otro_intento:true,historial_exportado:true,fallo_conserva_resultado_y_revision:true,original_intacto:true,celular:true,sin_inferencias_reales:true}));
 }finally{console.log('cerrando navegador');await b.close();console.log('cerrando servicio');s.server.closeAllConnections();await new Promise(r=>s.server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1});
