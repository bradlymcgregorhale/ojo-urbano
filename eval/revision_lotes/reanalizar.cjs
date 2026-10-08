/* Servicio local para intentos individuales inmutables (#58). Nunca reanuda la cola. */
'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto'),http=require('http'),{spawnSync}=require('child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const json=p=>JSON.parse(fs.readFileSync(p));
function atomic(p,v){fs.writeFileSync(p+'.tmp',JSON.stringify(v,null,2),{mode:0o600});fs.renameSync(p+'.tmp',p);}
function crearServicio(base,config,transporte){
 base=path.resolve(base);const out=path.join(base,'analisis/reanalisis');fs.mkdirSync(out,{recursive:true});
 const manifest=json(path.join(base,'entrega/manifest.json')),inputs=json(path.join(base,'analisis/entradas-api.json'));
 const ledgerPath=path.join(out,'estado.json'),ledger=fs.existsSync(ledgerPath)?json(ledgerPath):{gasto_usd:0,reserva_incierta_usd:0,intentos:[],activo:null};
 if(!(config.tope_usd>0&&config.tope_usd<5)||!config.modo_version||!config.token||!/^[a-f0-9]{48,}$/.test(config.token))throw Error('Configuración de reanálisis inválida.');
 const guardar=()=>atomic(ledgerPath,ledger),byId=id=>ledger.intentos.find(x=>x.id===id);
 const reservarIncierto=a=>{if(!a.reserva_incierta_usd){a.reserva_incierta_usd=1;ledger.reserva_incierta_usd+=1;}};
 const exponer=a=>({...a,url:a.estado==='listo'?'/'+config.token+'/revision/'+a.id:null});
 const estado=()=>({modo_version:config.modo_version,tope_usd:config.tope_usd,gasto_usd:ledger.gasto_usd,reserva_incierta_usd:ledger.reserva_incierta_usd,reserva_acotada_usd:ledger.reserva_acotada_usd||0,activo:ledger.activo,bloqueo:ledger.bloqueo||null,intentos:ledger.intentos.map(exponer)});
 const validarFoto=id=>{const row=manifest.fotos.find(x=>x.foto===id);if(!row)throw Error('Foto ajena al lote.');const input=inputs[id];if(!input)throw Error('Falta entrada preparada.');const original=fs.readFileSync(path.join(base,'entrega',row.archivo)),raw=fs.readFileSync(path.join(base,'entrega',input.archivo));if(sha(original)!==row.sha256||sha(raw)!==row.sha256_api||sha(raw)!==input.sha256||input.original_sha256!==row.sha256)throw Error('Cambió la foto original o su entrada.');return{row,raw,input};};
 async function continuar(a){
  try{
   if(a.estado==='listo'&&a.contabilizado)return;
   let response;
   const archivo=path.join(out,a.id+'.json'),recuperado=fs.existsSync(archivo)?json(archivo):null;
   if(recuperado){if(recuperado.foto!==a.foto||recuperado.original_huella!==a.original_huella)throw Error('El resultado guardado no corresponde al intento.');response={estado:'listo',resultado:recuperado.resultado};}
   else if(a.trabajo)response=await transporte.consultar(a.trabajo);
   else{
    const actual=await transporte.version();if(actual!==config.modo_version)throw Error('La versión desplegada cambió. No se envió la foto.');
    const {raw}=validarFoto(a.foto);
    a.envio_iniciado=true;guardar();
    response=await transporte.enviar(raw);
    if(response.trabajo){a.trabajo=response.trabajo;guardar();}
   }
   const inicio=Date.now();
   while(response.trabajo&&!['listo','error','cancelado'].includes(response.estado)){
    if(Date.now()-inicio>15*60*1000)throw Error('El trabajo sigue pendiente. Se conserva su identificador.');
    await new Promise(r=>setTimeout(r,config.intervalo_ms||4000));response=await transporte.consultar(a.trabajo);
   }
   if(!response.resultado)throw Error(response.detail?'El servicio informó: '+response.detail:'No se recibió un resultado; no se reenvía automáticamente.');
   const r=response.resultado,cache=!a.trabajo;
   const wrapper=recuperado||{foto:a.foto,modo:'alto',fecha:new Date().toISOString(),trabajo:a.trabajo||null,cache,
    entrada_api:inputs[a.foto],version_esperada:config.modo_version,resultado:r,original_huella:a.original_huella};
   const raw=recuperado?fs.readFileSync(archivo):JSON.stringify(wrapper,null,2);if(!recuperado)fs.writeFileSync(archivo,raw,{flag:'wx',mode:0o600});
   a.huella=sha(raw);a.modo_version=r.modo_version;a.cache=cache;a.fecha=wrapper.fecha;a.costo_api=r.costo_api;
   if(a.reserva_incierta_usd){ledger.reserva_incierta_usd=Math.max(0,ledger.reserva_incierta_usd-a.reserva_incierta_usd);delete a.reserva_incierta_usd;}
   if(!cache&&!a.contabilizado){if(Number.isFinite(r.costo_api)&&r.costo_api>=0)ledger.gasto_usd+=r.costo_api+0.000001;
    if(!Number.isFinite(r.costo_api)||r.costo_api<0||r.tokens_api_completos!==true)ledger.reserva_incierta_usd+=1;}
   if(a.primera_vez){
    const originalPath=path.join(base,'analisis',a.foto+'-alto.json');
    if(!fs.existsSync(originalPath))fs.writeFileSync(originalPath,JSON.stringify(wrapper,null,2),{flag:'wx',mode:0o600});
    const saved=json(originalPath);
    if(saved.foto!==a.foto||saved.trabajo!==wrapper.trabajo||JSON.stringify(saved.resultado)!==JSON.stringify(r))throw Error('Ya existe otro resultado original para esta foto.');
    a.huella_original=sha(fs.readFileSync(originalPath));
   }
   a.contabilizado=true;a.estado='listo';delete a.error;ledger.activo=null;
   if(ledger.bloqueo==='Hay un pedido pendiente de conciliación. No se generan más cargos.'&&!ledger.intentos.some(x=>x.estado==='pendiente_conciliacion'))ledger.bloqueo=null;
   if(r.modo!=='alto'||r.modo_version!==config.modo_version||r.analisis_estado==='sin_verificacion'){
    a.aviso='La respuesta no acredita la versión o la verificación esperada. Conservada para revisión.';ledger.bloqueo=a.aviso;
   }
   guardar();
  }catch(e){a.error=e.message;
   if(a.envio_iniciado){a.estado='pendiente_conciliacion';ledger.bloqueo='Hay un pedido pendiente de conciliación. No se generan más cargos.';reservarIncierto(a);}
   else{a.estado='no_enviado';ledger.activo=null;}
   guardar();
  }
 }
function iniciar(foto,solicitud){
   if(!/^[a-f0-9-]{20,64}$/.test(solicitud||''))throw Error('Identificador de solicitud inválido.');
   const previo=ledger.intentos.findLast(a=>a.solicitud===solicitud);if(previo&&previo.foto!==foto)throw Error('La solicitud pertenece a otra foto.');if(previo&&previo.estado!=='no_enviado')return exponer(previo);
   if(ledger.activo)throw Error('Ya hay un reanálisis en curso. Consultá ese intento.');
   if(ledger.bloqueo||ledger.reserva_incierta_usd)throw Error(ledger.bloqueo||'Consumo incierto pendiente de conciliación.');
   if(ledger.gasto_usd+(ledger.reserva_acotada_usd||0)+1>config.tope_usd)throw Error('Se alcanzó el tope del reanálisis.');
   validarFoto(foto);
   const origFile=path.join(base,'analisis',foto+'-alto.json'),hasOriginal=fs.existsSync(origFile);
   const a={id:crypto.randomUUID(),solicitud,foto,estado:'preparado',inicio:new Date().toISOString(),original_huella:hasOriginal?sha(fs.readFileSync(origFile)):'none',envio_iniciado:false,primera_vez:!hasOriginal};
   ledger.intentos.push(a);ledger.activo=a.id;guardar();void continuar(a);return exponer(a);
 }
function intento(id){
   const a=byId(id);if(!a||a.estado!=='listo')throw Error('Resultado todavía no disponible.');
   const wrapper=json(path.join(out,id+'.json'));
   if(wrapper.foto!==a.foto||wrapper.original_huella!==a.original_huella)throw Error('El resultado guardado no corresponde al intento.');
   const origFile=path.join(base,'analisis',a.foto+'-alto.json'),hasOriginal=fs.existsSync(origFile);
   return {intento:id,foto:a.foto,fecha:a.fecha,huella:a.huella,cache:!!wrapper.cache,modo_version:wrapper.resultado.modo_version,version_original:hasOriginal&&!a.primera_vez?json(origFile).resultado.modo_version:null,original_huella:a.original_huella,aviso:a.aviso||null,resultado:wrapper.resultado,primera_vez:a.primera_vez||false,huella_original:a.huella_original||null};
  }
function pagina(id){
   const a=byId(id);if(!a||a.estado!=='listo')throw Error('Resultado todavía no disponible.');
   const wrapper=json(path.join(out,id+'.json')),row=manifest.fotos.find(r=>r.foto===a.foto);
   const origFile=path.join(base,'analisis',a.foto+'-alto.json'),hasOriginal=fs.existsSync(origFile);
   if(hasOriginal&&!a.primera_vez&&sha(fs.readFileSync(origFile))!==a.original_huella)throw Error('Cambió el original.');
   const D={version:2,conjunto:sha(Buffer.from(a.original_huella+id)),fotos:[{...row,archivo:'/'+config.token+'/foto/'+a.foto,entrada_api:'/'+config.token+'/entrada/'+a.foto}],
    resultados:{[a.foto]:{respuesta:wrapper.resultado,huella:a.huella,fecha:a.fecha}},categorias:json(path.join(__dirname,'../../categorias.json')),proceso:{estado:hasOriginal?'reanalisis_individual':'analisis_individual',gasto_observado_usd:ledger.gasto_usd},cantidad:1,
    procedencia:{tipo:a.primera_vez?'analisis':'reanalisis',intento:id,original_huella:a.original_huella,nueva_huella:a.huella,version_original:hasOriginal&&!a.primera_vez?json(origFile).resultado.modo_version:null,version_nueva:wrapper.resultado.modo_version,foto_sha256:row.sha256,entrada_sha256:row.sha256_api,cache:wrapper.cache},anterior:hasOriginal&&!a.primera_vez?json(origFile).resultado:null,
    aviso_reanalisis:[a.aviso,wrapper.cache?'Respuesta recuperada de caché: no se ejecutó otra inferencia.':null,hasOriginal&&!a.primera_vez&&json(origFile).resultado.modo_version===wrapper.resultado.modo_version?'La versión coincide con la respuesta anterior. No representa una versión corregida distinta.':null].filter(Boolean).join(' ')||null};
   return fs.readFileSync(path.join(__dirname,'pagina.html'),'utf8').replace('/* ESTILOS */',()=>fs.readFileSync(path.join(__dirname,'pagina.css'),'utf8')).replace('/* APLICACION */',()=>fs.readFileSync(path.join(__dirname,'pagina.js'),'utf8')).replace('DATOS_JSON',()=>JSON.stringify(D).replaceAll('<','\\u003c'));
  }
 const server=http.createServer(async(req,res)=>{
  const host='127.0.0.1:'+server.address().port;
  const respond=(status,data,type='application/json')=>{res.writeHead(status,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"frame-ancestors 'none'"});res.end(type==='application/json'?JSON.stringify(data):data);};
  if(req.headers.host!==host)return respond(403,{error:'Host no permitido.'});
  const origin=req.headers.origin;if(origin&&origin!=='null'&&origin!=='http://'+host)return respond(403,{error:'Origen no permitido.'});
  if(origin){res.setHeader('Access-Control-Allow-Origin',origin);res.setHeader('Vary','Origin');res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');res.setHeader('Access-Control-Allow-Headers','Content-Type');res.setHeader('Access-Control-Allow-Private-Network','true');}
  const url=new URL(req.url,'http://'+host),parts=url.pathname.split('/');
  if(parts[1]!==config.token)return respond(403,{error:'Acceso local inválido.'});
  if(req.method==='OPTIONS'){res.writeHead(204);return res.end();}
  try{
   if(req.method==='GET'&&!parts[2]){
    const build=spawnSync(config.python||'python3',[path.join(__dirname,'generar.py'),base],{encoding:'utf8'});
    if(build.status!==0)throw Error('No se pudo actualizar la página de revisión.');
    const html=fs.readFileSync(path.join(base,'entrega/revision.html'),'utf8');
    const D=JSON.parse(html.match(/id="datos">(.*?)<\/script>/s)[1]);
    D.originales=structuredClone(D.resultados);
    for(const a of ledger.intentos.filter(a=>a.estado==='listo')){
     const v=intento(a.id);D.resultados[a.foto]={respuesta:v.resultado,huella:a.huella,fecha:a.fecha,intento:a.id};
    }
    D.fotos=D.fotos.map(row=>({...row,archivo:'/'+config.token+'/foto/'+row.foto,entrada_api:'/'+config.token+'/entrada/'+row.foto}));
    return respond(200,html.replace(/(id="datos">).*?(<\/script>)/s,(_,start,end)=>start+JSON.stringify(D).replaceAll('<','\\u003c')+end),'text/html');
   }
   if(req.method==='GET'&&parts[2]==='estado')return respond(200,estado());
   if(req.method==='POST'&&parts[2]==='reanalizar'){
    if(req.headers['content-type']!=='application/json')return respond(415,{error:'Se requiere JSON.'});
    let body='';for await(const chunk of req){body+=chunk;if(body.length>2048)throw Error('Pedido demasiado grande.');}
    const {foto,solicitud}=JSON.parse(body);return respond(202,iniciar(foto,solicitud));
   }
   if(req.method==='GET'&&parts[2]==='intento')return respond(200,intento(parts[3]));
   if(req.method==='GET'&&parts[2]==='revision')return respond(200,pagina(parts[3]),'text/html');
   if(req.method==='GET'&&['foto','entrada'].includes(parts[2])){const {row,raw}=validarFoto(parts[3]);const bytes=parts[2]==='entrada'?raw:fs.readFileSync(path.join(base,'entrega',row.archivo));res.writeHead(200,{'Content-Type':'image/jpeg','Cache-Control':'no-store','Referrer-Policy':'no-referrer'});return res.end(bytes);}
   return respond(404,{error:'Ruta inexistente.'});
  }catch(e){return respond(409,{error:e.message});}
 });
 const pendiente=byId(ledger.activo);
 if(pendiente&&(pendiente.trabajo||fs.existsSync(path.join(out,pendiente.id+'.json'))))void continuar(pendiente);
 else if(pendiente){
  if(pendiente.envio_iniciado){pendiente.estado='pendiente_conciliacion';reservarIncierto(pendiente);ledger.bloqueo='Hay un pedido pendiente de conciliación. No se generan más cargos.';}
  else{pendiente.estado='no_enviado';pendiente.error='El servicio se interrumpió antes del envío.';ledger.activo=null;}
  guardar();
 }
 return {server,estado,iniciar,pagina,intento};
}
async function transportePublico(config,pp=require(config.puppeteer)){
 let browser,page,cerrado=false,conexion;
 async function conectar(renovar=false){
  if(cerrado)throw Error('La conexión está cerrada.');
  if(conexion)return conexion;
  conexion=(async()=>{
   if(!browser?.isConnected())browser=await pp.launch({headless:true});
   if(renovar||!page||page.isClosed()){
    if(page&&!page.isClosed())await page.close().catch(()=>{});
    page=await browser.newPage();
    try{await page.goto(config.url,{waitUntil:'domcontentloaded'});}
    catch(e){await page.close().catch(()=>{});page=null;throw e;}
   }
  })();
  try{await conexion;}finally{conexion=null;}
 }
 async function fetchPublic(method,url,raw){
  for(let intento=0;intento<2;intento++){
   try{
    await conectar(intento>0);
    return await page.evaluate(async({method,url,raw})=>{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);try{const options={method,signal:controller.signal};if(raw){const f=new FormData();f.append('file',new File([Uint8Array.from(atob(raw),c=>c.charCodeAt(0))],'foto.jpg',{type:'image/jpeg'}));f.append('modo','alto');f.append('contexto','');f.append('verificar','1');options.body=f;}const r=await fetch(url,options),d=await r.json();if(!r.ok)throw Error('HTTP '+r.status+': '+(d.detail||'Error del servicio'));return d;}finally{clearTimeout(timer);}}, {method,url,raw:raw?.toString('base64')});
   }catch(e){
    // Solo las consultas se repiten: un POST puede haberse recibido sin respuesta.
    const sesionPerdida=/detached|Execution context|Target closed|Session closed|Connection closed|Protocol error/i.test(e.message);
    if(method!=='GET'||intento||!sesionPerdida)throw e;
   }
  }
 }
 await conectar();
 return {cerrar:async()=>{cerrado=true;await browser?.close();},version:async()=>{const s=await fetchPublic('GET','./salud/');return s.modos_analisis.find(m=>m.modo==='alto'&&m.disponible)?.modo_version;},enviar:raw=>fetchPublic('POST','./trabajos/',raw),consultar:id=>fetchPublic('GET','./trabajos/?id='+encodeURIComponent(id))};
}
module.exports={crearServicio,transportePublico};
if(require.main===module){(async()=>{const base=path.resolve(process.argv[2]||''),config=json(path.join(base,'analisis/reanalisis-config.json')),transport=await transportePublico(config),service=crearServicio(base,config,transport);service.server.listen(config.puerto,'127.0.0.1',()=>console.log('Reanálisis local disponible.'));const close=()=>service.server.close(async()=>{await transport.cerrar();process.exit(0)});process.on('SIGTERM',close);process.on('SIGINT',close);})().catch(e=>{console.error(e.message);process.exitCode=1});}
