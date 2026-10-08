'use strict';
const assert=require('assert'),crypto=require('crypto'),http=require('http');
const {crearValidador,crearPuerta}=require('./cloudflare.cjs');
function request(url,options={}){return new Promise((resolve,reject)=>{const r=http.request(url,options,res=>{let body='';res.on('data',c=>body+=c);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,text:async()=>body}));});r.on('error',reject);r.end(options.body);});}
(async()=>{
 const {privateKey,publicKey}=crypto.generateKeyPairSync('rsa',{modulusLength:2048}),jwk={...publicKey.export({format:'jwk'}),kid:'prueba'};
 const cfg={issuer:'https://prueba.cloudflareaccess.com',audience:'audiencia',emails:['persona@example.com'],origin:'https://revision.example.com',path:'/ojo'};
 const validar=crearValidador(cfg,async()=>[jwk]),claims={iss:cfg.issuer,aud:[cfg.audience],email:cfg.emails[0],exp:Date.now()/1000+300};
 function token(datos=claims,header={alg:'RS256',kid:'prueba'}){const s=[header,datos].map(x=>Buffer.from(JSON.stringify(x)).toString('base64url')).join('.');return s+'.'+crypto.sign('RSA-SHA256',Buffer.from(s),privateKey).toString('base64url');}
 assert.equal(await validar(token()),cfg.emails[0]);
 for(const c of [{...claims,exp:0},{...claims,aud:['otra']},{...claims,iss:'https://otra.cloudflareaccess.com'},{...claims,email:'otra@example.com'},{...claims,nbf:Date.now()/1000+600}])await assert.rejects(()=>validar(token(c)));
 await assert.rejects(()=>validar(token(claims,{alg:'none',kid:'prueba'})));await assert.rejects(()=>validar(token().slice(0,-15)+'falso'));
 let posts=0;
 const secreto='e'.repeat(64),up=http.createServer((req,res)=>{if(req.method==='POST')posts++;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({url:'/ '+secreto,ruta:'/'+secreto+'/foto/R0001'}));});
 await new Promise(r=>up.listen(0,'127.0.0.1',r));const local={puerto:up.address().port,token:secreto};
 // La configuración privada se reemplaza sin divulgar su clave ni loopback.
 up.removeAllListeners('request');up.on('request',(req,res)=>{if(req.method==='POST')posts++;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({url:'http://127.0.0.1:'+local.puerto+'/'+secreto,ruta:'/'+secreto+'/foto/R0001'}));});
 const puerta=crearPuerta(cfg,local,validar);await new Promise(r=>puerta.listen(0,'127.0.0.1',r));const url='http://127.0.0.1:'+puerta.address().port,headers={Host:'revision.example.com','Cf-Access-Jwt-Assertion':token()};
 try{
  assert.equal((await request(url+'/ojo/estado',{headers:{Host:headers.Host}})).status,403);
  assert.equal((await request(url+'/ojo/estado',{headers:{...headers,'Cf-Access-Jwt-Assertion':'falso'}})).status,403);
  assert.equal((await request(url+'/ojo/estado',{headers:{...headers,Host:'otro.example.com'}})).status,403);
  assert.equal((await request(url+'/ojo/estado',{headers:{Host:headers.Host,Cookie:'ojo_bypass=%ZZ'}})).status,403);
  assert.equal((await request(url+'/ojo/estado',{headers:{...headers,Cookie:'ojo_bypass=%ZZ'}})).status,200);
  const good=await(await request(url+'/ojo/estado',{headers})).text();assert(!good.includes(secreto));assert(!good.includes('127.0.0.1'));assert(good.includes('/ojo/foto/R0001'),good);
  assert.equal((await request(url+'/ojo/manifest.json',{headers})).status,404);
  assert.equal((await request(url+'/ojo/reanalizar',{method:'POST',headers:{...headers,'Content-Type':'application/json',Origin:'https://ajeno.example.com'},body:'{}'})).status,403);assert.equal(posts,0);
  assert.equal((await request(url+'/ojo/reanalizar',{method:'POST',headers:{...headers,'Content-Type':'application/json',Origin:cfg.origin},body:'{}'})).status,200);assert.equal(posts,1);
  // El atajo con la clave local salta el JWT, fija una cookie HttpOnly y no divulga la clave ni el loopback.
  const salto=await request(url+'/ojo?bypass='+secreto,{headers:{Host:headers.Host}});
  assert.equal(salto.status,302);assert.equal(salto.headers.location,'/ojo/');
  assert((salto.headers['set-cookie']||[]).some(c=>c.startsWith('ojo_bypass=')&&c.includes('HttpOnly')),JSON.stringify(salto.headers['set-cookie']));
  const conCookie=await request(url+'/ojo/estado',{headers:{Host:headers.Host,Cookie:'ojo_bypass='+secreto}});
  assert.equal(conCookie.status,200);assert(!(await conCookie.text()).includes(secreto));
  assert.equal((await request(url+'/ojo/estado',{headers:{Host:headers.Host,Cookie:'ojo_bypass=clave-equivocada'}})).status,403);
  assert.equal((await request(url+'/ojo/reanalizar',{method:'POST',headers:{Host:headers.Host,Cookie:'ojo_bypass='+secreto,'Content-Type':'application/json',Origin:'https://ajeno.example.com'},body:'{}'})).status,403);assert.equal(posts,1);
  console.log('Access: firma/emisor/audiencia/cuenta/vencimiento, rutas, origen, clave privada y atajo con cookie verificados.');
 }finally{puerta.closeAllConnections();up.closeAllConnections();await Promise.all([new Promise(r=>puerta.close(r)),new Promise(r=>up.close(r))]);}
})().catch(e=>{console.error(e);process.exitCode=1});
