/* Revisión ciega, conservación y métricas sobre respuestas sintéticas (#41). */
const fs=require('fs'),path=require('path'),assert=require('assert');
const puppeteer=require(process.env.OJO_PUPPETEER||'puppeteer'),os=require('os'),{spawnSync}=require('child_process');
const base=fs.mkdtempSync(path.join(os.tmpdir(),'ojo-modos-prueba-')),downloads=path.join(base,'descargas');
fs.mkdirSync(downloads);fs.mkdirSync(path.join(base,'entrega'));fs.mkdirSync(path.join(base,'analisis'));
const image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
const fotos=Array.from({length:100},(_,i)=>({foto:'X'+String(i+1).padStart(3,'0'),archivo:image,sha256:String(i).padStart(64,'0')}));
fs.writeFileSync(path.join(base,'entrega/manifest.json'),JSON.stringify({fotos}));
for(const p of fotos)for(const modo of ['bajo','medio','alto'])fs.writeFileSync(path.join(base,'analisis',p.foto+'-'+modo+'.json'),JSON.stringify({resultado:{problemas:modo==='bajo'?[]:[{key:'recoleccion'}],elementos_detectados:[],posibles:[],analisis_estado:'completo',costo_api:0.001,tokens_api_completos:true}}));
const entrada=path.join(base,'anterior.html');fs.writeFileSync(entrada,'<!DOCTYPE html><html><head><title>Prueba</title><style>body{margin:0}</style></head><body>'+fotos.map(p=>'<section id="'+p.foto+'"><div class="mode">Resultado sintético</div><div class="review">Referencia sintética</div></section>').join('')+'</body></html>');
const ejemplos=path.join(base,'ejemplos.json');fs.writeFileSync(ejemplos,JSON.stringify([{foto:'X019',descripcion:'Ejemplo sintético A'},{foto:'X011',descripcion:'Ejemplo sintético B'}]));
const built=spawnSync(process.env.OJO_PYTHON||'python3',[path.join(__dirname,'revision_humana.py'),'--base',base,'--entrada',entrada,'--salida',path.join(base,'entrega/comparacion.html'),'--ejemplos',ejemplos],{encoding:'utf8'});assert.equal(built.status,0,built.stderr);
(async()=>{
 const b=await puppeteer.launch({headless:true});
 try{
 const p=await b.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.setViewport({width:1440,height:1050});await p.goto('file://'+base+'/entrega/comparacion.html');
 assert(await p.$eval('#legacy',e=>e.hidden));assert.equal(await p.$eval('#foto-select',e=>e.value),'X001');assert.equal(await p.$$eval('#form-revision input:checked',a=>a.length),0);
 await p.click('#terminar');assert((await p.$eval('#form-mensaje',e=>e.textContent)).includes('Falta responder'));
 await p.screenshot({path:base+'/analisis/revision-humana-escritorio.png'});
 const radio=async(g,k,v)=>{await p.$eval(`input[name="${g}.${k}"][value="${v}"]`,e=>e.click())};
 const keys=['recoleccion','retiro_escombros','retiro_muebles','retiro_poda'];
 for(const k of keys){await radio('materiales',k,'si');await radio('servicios',k,k==='recoleccion'?'si':k==='retiro_escombros'?'no':'duda');}
 for(const k of ['contenedor_humedos_lateral','contenedor_humedos_bilateral','contenedor_secos'])await radio('contenedores',k,'duda');
 await p.type('#notas','Prueba de conservación <img src=x onerror=alert(1)>');await p.click('#terminar');
 assert.equal(await p.$eval('#avance',e=>e.textContent),'1 / 100');assert.equal(await p.$eval('#foto-select',e=>e.value),'X002');
 await p.reload();assert.equal(await p.$eval('#avance',e=>e.textContent),'1 / 100');
 await p.select('#foto-select','X001');assert((await p.$eval('#notas',e=>e.value)).includes('<img'));assert.equal(await p.$$eval('#form-revision input[type=radio]:checked',a=>a.length),11);
 await p.click('#ver-puntajes');
 const d=await p.$eval('#human-data',e=>JSON.parse(e.textContent)), rows=await p.$$eval('#puntajes-contenido>div table tbody tr',a=>a.map(r=>Array.from(r.cells).map(c=>c.textContent)));
 const expected=[];
 for(const mode of ['bajo','medio','alto']){const out=d.modos.X001[mode],has=k=>out.confirmados.includes(k),possible=k=>!has(k)&&out.posibles.includes(k);const tp=has('recoleccion')?1:0,fp=has('retiro_escombros')?1:0,pending=possible('recoleccion')?1:0,fn=1-tp-pending;const pct=(n,den)=>den?(100*n/den).toFixed(1)+'% ('+n+'/'+den+')':'Sin casos';expected.push({mode,tp,fp,fn,pending});const row=rows[expected.length-1];assert.equal(row[1],pct(tp,tp+fp));assert.equal(row[2],pct(tp,1));assert.equal(+row[3],fp);assert.equal(+row[4],fn);assert.equal(+row[5],pending);assert(row[7].startsWith('2 ('));}
 const cdp=await p.createCDPSession();await cdp.send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});await p.click('#exportar');
 let exported;for(let n=0;n<40;n++){exported=fs.readdirSync(downloads).filter(f=>f.endsWith('.json')).sort().pop();if(exported)break;await new Promise(r=>setTimeout(r,50));}assert(exported);exported=path.join(downloads,exported);const saved=JSON.parse(fs.readFileSync(exported));assert(saved.revisiones.X001.completa);
 const ctx=await b.createBrowserContext(),p2=await ctx.newPage();await p2.goto('file://'+base+'/entrega/comparacion.html');await (await p2.$('#archivo-revision')).uploadFile(exported);await p2.waitForSelector('#import-preview:not([hidden])');await p2.$eval('#import-preview button',e=>e.click());assert.equal(await p2.$eval('#avance',e=>e.textContent),'1 / 100');
 await p2.type('#notas',' Cambio local');await (await p2.$('#archivo-revision')).uploadFile(exported);await p2.waitForSelector('#import-preview:not([hidden])');assert((await p2.$eval('#import-preview',e=>e.textContent)).includes('1 tienen diferencias'));await p2.$eval('#import-preview button',e=>e.click());assert((await p2.$eval('#notas',e=>e.value)).includes('Cambio local'));
 const invalid=path.join(downloads,'incompatible.json');fs.writeFileSync(invalid,JSON.stringify({...saved,conjunto:'otro'}));await (await p2.$('#archivo-revision')).uploadFile(invalid);await p2.waitForFunction(()=>document.querySelector('#guardado').textContent.includes('No se importó'));assert((await p2.$eval('#notas',e=>e.value)).includes('Cambio local'));
 await p2.click('#revelar');assert(!(await p2.$eval('#resultados-revelados',e=>e.hidden)));assert((await p2.$eval('#exposicion-aviso',e=>e.textContent)).includes('Resultados revelados'));await p2.reload();assert(await p2.$eval('#resultados-revelados',e=>e.hidden));assert((await p2.$eval('#exposicion-aviso',e=>e.textContent)).includes('Resultados revelados'));
 await p2.setViewport({width:390,height:844});await p2.screenshot({path:base+'/analisis/revision-humana-celular.png'});assert(await p2.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await p2.evaluate(()=>{Storage.prototype.setItem=function(){throw Error('Sin espacio')};});await p2.type('#notas',' respaldo');assert((await p2.$eval('#guardado',e=>e.textContent)).includes('No se pudo guardar'));
 assert.deepEqual(errors,[]);const report={inicio_sin_predicciones:true,sin_preselecciones:true,validacion_completitud:true,recarga_conserva:true,conteos_independientes:expected,exportacion_importacion:true,conflictos_conservan_local:true,conjunto_incompatible_rechazado:true,exposicion_persistente:true,celular_sin_desborde:true,fallo_guardado_visible:true,errores:errors};fs.writeFileSync(base+'/analisis/validacion-revision-humana.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await b.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
