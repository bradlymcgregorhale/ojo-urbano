'use strict';
const assert=require('assert'),http=require('http');
const {transportePublico}=require('./reanalizar.cjs');
const pp=require(process.env.OJO_PUPPETEER||'puppeteer');
(async()=>{
 let posts=0,browser;
 const server=http.createServer(async(req,res)=>{
  if(req.method==='POST'){posts++;for await(const chunk of req){}res.setHeader('Content-Type','application/json');res.end('{');return;}
  res.setHeader('Content-Type','application/json');
  res.end(JSON.stringify(req.url.includes('salud')?{modos_analisis:[{modo:'alto',disponible:true,modo_version:'prueba'}]}:{estado:'listo'}));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const engine={launch:async opts=>(browser=await pp.launch(opts))};
 let transport;
 try{
  transport=await transportePublico({url:'http://127.0.0.1:'+server.address().port+'/'},engine);
  assert.equal(await transport.version(),'prueba');
  const page=(await browser.pages()).at(-1);
  // Reproduce el contexto desprendido con una pestaña que sigue abierta.
  page.evaluate=async()=>{throw Error('Attempted to use detached Frame');};
  assert.equal(await transport.version(),'prueba');
  await (await browser.pages()).at(-1).close();
  assert.equal((await transport.consultar('trabajo')).estado,'listo');
  await browser.close();
  assert.equal(await transport.version(),'prueba');
  // El servidor recibe el POST y pierde la respuesta: no debe repetirse.
  await assert.rejects(()=>transport.enviar(Buffer.from('foto de prueba')));
  assert.equal(posts,1);
  assert.equal(await transport.version(),'prueba');
  assert.equal(posts,1);
  console.log('Transporte: recupera pestaña/contexto/navegador, sin repetir POST incierto.');
 }finally{await transport?.cerrar();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
