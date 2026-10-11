/* Prueba real de cookie sobre TLS y HTTP en un dominio sintético, sin cuentas reales ni lectores. */
'use strict';
const fs=require('fs'),os=require('os'),path=require('path'),http=require('http'),https=require('https'),assert=require('assert');
const {spawnSync}=require('child_process');const pp=require(process.env.OJO_PUPPETEER||'puppeteer');
const {crearPuerta}=require('./cloudflare.cjs');
(async()=>{const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'ojo-cookie-tls-'));let browser,gate;let setCookie='';const secret='d'.repeat(64);let gatePort;
const up=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<!doctype html><p>Puerta sintética</p>');});
const clear=http.createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({cookie:req.headers.cookie||''}));});
const cert=spawnSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',path.join(tmp,'key.pem'),'-out',path.join(tmp,'cert.pem'),'-days','1','-subj','/CN=revision.example.com','-addext','subjectAltName=DNS:revision.example.com'],{stdio:'ignore'});assert.equal(cert.status,0);
const tls=https.createServer({key:fs.readFileSync(path.join(tmp,'key.pem')),cert:fs.readFileSync(path.join(tmp,'cert.pem'))},(req,res)=>{const r=http.request({hostname:'127.0.0.1',port:gatePort,path:req.url,method:req.method,headers:req.headers},s=>{if(s.headers['set-cookie'])setCookie=s.headers['set-cookie'][0];res.writeHead(s.statusCode,s.headers);s.pipe(res)});r.on('error',()=>{res.writeHead(502);res.end()});req.pipe(r);});
const listen=s=>new Promise(r=>s.listen(0,'127.0.0.1',r));const close=s=>new Promise(r=>{if(!s)return r();s.closeAllConnections();s.close(r)});
try{await listen(up);await listen(clear);await listen(tls);const cfg={origin:'https://revision.example.com:'+tls.address().port,path:'/ojo'};gate=crearPuerta(cfg,{puerto:up.address().port,token:secret},async()=>{throw Error('Sin JWT en esta prueba')});await listen(gate);gatePort=gate.address().port;
browser=await pp.launch({headless:true,acceptInsecureCerts:true,args:['--no-proxy-server','--host-resolver-rules=MAP revision.example.com 127.0.0.1']});const page=await browser.newPage();const start=await page.goto(cfg.origin+'/ojo?bypass='+secret,{waitUntil:'networkidle0'});assert.equal(start.status(),200);assert(setCookie.includes('HttpOnly'));assert(setCookie.includes('SameSite=Lax'));
// Simular una cookie anterior sin Secure y comprobar su actualización al consultar por TLS.
const cdp=await page.createCDPSession();
await cdp.send('Network.deleteCookies',{name:'ojo_bypass',domain:'revision.example.com',path:'/'});
await cdp.send('Network.setCookie',{name:'ojo_bypass',value:secret,domain:'revision.example.com',path:'/',secure:false,httpOnly:true,sameSite:'Lax'});
assert((await page.cookies(cfg.origin)).some(c=>c.name==='ojo_bypass'&&!c.secure));
const good=await page.goto(cfg.origin+'/ojo/estado',{waitUntil:'networkidle0'});assert.equal(good.status(),200);const insecure=await page.goto('http://revision.example.com:'+clear.address().port+'/captura',{waitUntil:'networkidle0'});const raw=JSON.parse(await page.evaluate(()=>document.body.innerText));const evidence={secure_en_cabecera:/(?:^|;)\s*Secure(?:;|$)/i.test(setCookie),https_autorizado:good.status(),cookie_transmitida_http:raw.cookie.includes(secret)};console.log(JSON.stringify(evidence));assert(evidence.secure_en_cabecera);assert(!evidence.cookie_transmitida_http);
}finally{if(browser)await browser.close();await Promise.all([close(gate),close(tls),close(clear),close(up)]);fs.rmSync(tmp,{recursive:true,force:true});}})().catch(e=>{console.error(e.message);process.exitCode=1});
