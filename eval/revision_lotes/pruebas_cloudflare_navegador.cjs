/* Bandeja por ruta pública relativa, con transporte sintético y sin inferencias. */
'use strict';
const {crearServicio}=require('./reanalizar.cjs'),fs=require('fs'),path=require('path'),os=require('os'),crypto=require('crypto'),assert=require('assert');
const {spawnSync}=require('child_process');
const pp=require(process.env.OJO_PUPPETEER||'puppeteer'),sha=b=>crypto.createHash('sha256').update(b).digest('hex');
(async()=>{
 const base=fs.mkdtempSync(path.join(os.tmpdir(),'ojo-recientes-browser-')),a=path.join(base,'analisis'),e=path.join(base,'entrega');fs.mkdirSync(a);fs.mkdirSync(e);
 const bytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');fs.writeFileSync(path.join(e,'foto.jpg'),bytes);
 const row={foto:'R0001',archivo:'foto.jpg',entrada_api:'foto.jpg',sha256:sha(bytes),sha256_api:sha(bytes),fecha:new Date().toISOString()};
 fs.writeFileSync(path.join(e,'manifest.json'),JSON.stringify({fotos:[row]}));fs.writeFileSync(path.join(a,'entradas-api.json'),JSON.stringify({R0001:{archivo:'foto.jpg',sha256:sha(bytes),original_sha256:sha(bytes)}}));
 fs.writeFileSync(path.join(a,'estado.json'),JSON.stringify({estado:'manual',resultados:[]}));
 let calls=0,falloConexion=true;const cfg={modo_version:'prueba-recientes',token:'c'.repeat(64),tope_usd:3,intervalo_ms:5,python:process.env.OJO_PYTHON||'python3'};
 const result={modo:'alto',modo_version:cfg.modo_version,hay_reclamo:true,problemas:[{key:'recoleccion',nombre:'Recolección'}],posibles:[],elementos_detectados:[],descripcion:'RESPUESTA SINTÉTICA',analisis_estado:'completo',costo_api:.01,tokens_api:10,tokens_api_completos:true};
 const s=crearServicio(base,cfg,{version:async()=>{if(falloConexion)throw Error('Sesión interrumpida');return cfg.modo_version;},enviar:async()=>{calls++;return{trabajo:'nuevo',estado:'en_cola'}},consultar:async()=>({trabajo:'nuevo',estado:'listo',resultado:result})});
 await new Promise(r=>s.server.listen(0,'127.0.0.1',r));cfg.puerto=s.server.address().port;fs.writeFileSync(path.join(a,'reanalisis-config.json'),JSON.stringify(cfg));
 const {crearPuerta}=require('./cloudflare.cjs'),http=require('http');
 const origin='https://revision.example';
 const puerta=crearPuerta({origin,path:'/ojo'},cfg,async()=> 'cuenta-sintetica');
 // Adaptador de cabeceras del terminador TLS para el navegador aislado en loopback.
 // Firma de Access y rechazo de orígenes se comprueban en pruebas_cloudflare.cjs.
 let url;
 const proxy=http.createServer((req,res)=>{
  if(req.method==='POST'&&req.headers.origin===new URL(url).origin)req.headers.origin=origin;
  req.headers.host=new URL(origin).host;puerta.emit('request',req,res);
 });
 await new Promise(r=>proxy.listen(0,'127.0.0.1',r));url='http://127.0.0.1:'+proxy.address().port+'/ojo/';
 const b=await pp.launch({headless:true});try{
  const p=await b.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));falloConexion=false;
  for(const width of [360,390]){
   await p.setViewport({width,height:844});await p.goto(url);
   await p.waitForFunction(()=>!document.getElementById('reanalisar-foto').disabled);
   assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert((await p.$eval('#reanalisar-foto',e=>parseFloat(getComputedStyle(e).minHeight)))>=44);
  }
  assert.equal(calls,0);await p.click('#reanalisar-foto');
  await p.waitForFunction(()=>!document.getElementById('acciones').hidden,{timeout:15000});assert.equal(calls,1);
  await p.click('#confirmar-simple');assert.equal(await p.$eval('#avance',e=>e.textContent),'1 / 1 revisadas');
  await p.reload();await p.waitForFunction(()=>!document.getElementById('reanalisar-foto').disabled);
  assert.equal(await p.$eval('#avance',e=>e.textContent),'1 / 1 revisadas');
  assert(!(await p.content()).includes(cfg.token));
  const links=await p.$$eval('#reanalisar-intentos a',xs=>xs.map(x=>x.href));assert(links.length);assert(links.every(x=>x.startsWith(url)));
  for(const link of links){const response=await fetch(link);assert.equal(response.status,200);assert(!(await response.text()).includes(cfg.token));}
  await p.click('#reanalisar-foto');await p.waitForFunction(()=>document.getElementById('avance').textContent==='0 / 1 revisadas',{timeout:15000});assert.equal(calls,2);
  await p.click('#editar');await p.type('#buscar-categoria','muebles');await p.click('#resultados-categoria button');await p.click('#guardar-simple');
  assert(!(await p.$eval('#correccion-guardada',e=>e.hidden)));await p.reload();await p.waitForFunction(()=>!document.getElementById('reanalisar-foto').disabled);
  assert(!(await p.$eval('#correccion-guardada',e=>e.hidden)));assert.equal(calls,2);assert.deepEqual(errors,[]);
  const failed=await p.$eval('#reanalisar-estado',e=>e.textContent);assert(!failed.includes('Invalid base URL'));
  await p.screenshot({path:path.join(base,'celular-cloudflare.png'),fullPage:true});
  console.log(JSON.stringify({ruta_relativa:true,celular_360_390:true,botones_44px:true,analisis_manual:true,reanalisis_reinicia_revision:true,confirmacion_y_correccion_persisten:true,historial_navegable:true,clave_local_no_publicada:true,sin_inferencias_reales:true,evidencia:base}));
 }finally{await b.close();proxy.closeAllConnections();await new Promise(r=>proxy.close(r));s.server.closeAllConnections();await new Promise(r=>s.server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1});
