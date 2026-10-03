/* Primer análisis, confirmación y recarga con transporte sintético (#139). */
'use strict';
const {crearServicio}=require('./reanalizar.cjs'),fs=require('fs'),path=require('path'),os=require('os'),crypto=require('crypto'),assert=require('assert');
const pp=require(process.env.OJO_PUPPETEER||'puppeteer'),sha=b=>crypto.createHash('sha256').update(b).digest('hex');
(async()=>{
 const base=fs.mkdtempSync(path.join(os.tmpdir(),'ojo-recientes-browser-')),a=path.join(base,'analisis'),e=path.join(base,'entrega');fs.mkdirSync(a);fs.mkdirSync(e);
 const bytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');fs.writeFileSync(path.join(e,'foto.jpg'),bytes);
 const row={foto:'R0001',archivo:'foto.jpg',entrada_api:'foto.jpg',sha256:sha(bytes),sha256_api:sha(bytes),fecha:new Date().toISOString()};
 fs.writeFileSync(path.join(e,'manifest.json'),JSON.stringify({fotos:[row]}));fs.writeFileSync(path.join(a,'entradas-api.json'),JSON.stringify({R0001:{archivo:'foto.jpg',sha256:sha(bytes),original_sha256:sha(bytes)}}));
 fs.writeFileSync(path.join(a,'estado.json'),JSON.stringify({estado:'manual',resultados:[]}));
 let calls=0;const cfg={modo_version:'prueba-recientes',token:'c'.repeat(64),tope_usd:3,intervalo_ms:5,python:process.env.OJO_PYTHON||'python3'};
 const result={modo:'alto',modo_version:cfg.modo_version,hay_reclamo:true,problemas:[{key:'recoleccion',nombre:'Recolección'}],posibles:[],elementos_detectados:[],descripcion:'RESPUESTA SINTÉTICA',analisis_estado:'completo',costo_api:.01,tokens_api:10,tokens_api_completos:true};
 const s=crearServicio(base,cfg,{version:async()=>cfg.modo_version,enviar:async()=>{calls++;return{trabajo:'nuevo',estado:'en_cola'}},consultar:async()=>({trabajo:'nuevo',estado:'listo',resultado:result})});
 await new Promise(r=>s.server.listen(0,'127.0.0.1',r));cfg.puerto=s.server.address().port;fs.writeFileSync(path.join(a,'reanalisis-config.json'),JSON.stringify(cfg));
 const url='http://127.0.0.1:'+cfg.puerto+'/'+cfg.token+'/';
 const b=await pp.launch({headless:true});try{
  const p=await b.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto(url);
  await p.waitForFunction(()=>!document.getElementById('reanalisar-foto').disabled);assert.equal(calls,0);assert.equal(await p.$eval('#reanalisar-foto',e=>e.textContent),'Analizar esta foto');assert(await p.$eval('#acciones',e=>e.hidden));
  await p.click('[data-filtro="sin_resultado"]');assert.equal(await p.$$eval('.photo-row',e=>e.length),1);
  await p.click('#reanalisar-foto');await p.waitForFunction(()=>!document.getElementById('acciones').hidden,{timeout:15000});assert.equal(calls,1);
  assert.equal(await p.$eval('#descripcion-original',e=>e.textContent),result.descripcion);
  await p.click('#calidad-suficiente');await p.click('#aprobar-publica');assert.equal(await p.$eval('#avance',e=>e.textContent),'1 / 1 revisadas');
  await p.click('[data-filtro="revisadas"]');assert.equal(await p.$$eval('.photo-row',e=>e.length),1);
  const original=fs.readFileSync(path.join(a,'R0001-alto.json')),cost=s.estado().gasto_usd;
  await p.reload();assert.equal(await p.$eval('#avance',e=>e.textContent),'1 / 1 revisadas');assert.equal(await p.$eval('#descripcion-original',e=>e.textContent),result.descripcion);assert.equal(calls,1);assert.deepEqual(fs.readFileSync(path.join(a,'R0001-alto.json')),original);assert.equal(s.estado().gasto_usd,cost);
  await p.click('#editar');assert(!(await p.$eval('#editor',e=>e.hidden)));assert(await p.$eval('[data-group="categorias"][data-key="recoleccion"]',e=>e.parentElement.parentElement.id==='correcciones-principales'));
  await p.select('[data-group="categorias"][data-key="recoleccion"]','no');await p.select('#agregar-categoria','retiro_muebles');await p.select('[data-group="categorias"][data-key="retiro_muebles"]','confirmado');await p.click('#guardar');
  assert(!(await p.$eval('#correccion-guardada',e=>e.hidden)));assert((await p.$eval('#correccion-guardada',e=>e.textContent)).includes('guardada'));assert.deepEqual(fs.readFileSync(path.join(a,'R0001-alto.json')),original);
  await p.reload();assert(!(await p.$eval('#correccion-guardada',e=>e.hidden)));const saved=await p.evaluate(()=>JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('ojo-lote-v2:')))));assert.equal(saved.revisiones.R0001.estado,'corregido');assert.equal(saved.revisiones.R0001.categorias.recoleccion,'no');assert.equal(saved.revisiones.R0001.categorias.retiro_muebles,'confirmado');assert.equal(calls,1);
  await p.click('[data-filtro="todas"]');await p.type('#buscar-foto','inexistente');assert.equal(await p.$$eval('.photo-row',e=>e.length),0);assert(!(await p.$eval('#bandeja-vacia',e=>e.hidden)));
  await p.setViewport({width:390,height:844});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);console.log(JSON.stringify({sin_pedidos_al_abrir:true,primer_analisis_unico:true,resultado_persistido:true,confirmacion_recuperada:true,filtros_y_busqueda:true,celular:true,sin_inferencias_reales:true,evidencia:base}));
 }finally{await b.close();s.server.closeAllConnections();await new Promise(r=>s.server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1});
