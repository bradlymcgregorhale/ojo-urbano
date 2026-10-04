'use strict';
/* Puerta de acceso para la bandeja privada, sin publicar su clave local. */
const http=require('http'),crypto=require('crypto'),fs=require('fs'),path=require('path');
function crearValidador(config,cargarClaves){
 let claves=[],vence=0;
 const issuer=new URL(config.issuer);
 if(issuer.protocol!=='https:'||!issuer.hostname.endsWith('.cloudflareaccess.com')||issuer.pathname!=='/')throw Error('Emisor de Access inválido.');
 const carga=cargarClaves||(async()=>{const r=await fetch(new URL('/cdn-cgi/access/certs',issuer),{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error('No se pudo verificar Access.');return (await r.json()).keys;});
 return async token=>{
  if(typeof token!=='string'||token.length>20000)throw Error('Iniciá sesión con Cloudflare.');
  const partes=token.split('.');if(partes.length!==3)throw Error('Sesión inválida.');
  const cabecera=JSON.parse(Buffer.from(partes[0],'base64url')),datos=JSON.parse(Buffer.from(partes[1],'base64url')),ahora=Date.now()/1000;
  if(cabecera.alg!=='RS256'||typeof cabecera.kid!=='string'||datos.iss!==config.issuer||!Number.isFinite(datos.exp)||datos.exp<=ahora||(datos.nbf!==undefined&&(!Number.isFinite(datos.nbf)||datos.nbf>ahora+30))||!(Array.isArray(datos.aud)?datos.aud:[datos.aud]).includes(config.audience))throw Error('Sesión inválida o vencida.');
  if(Date.now()>vence){claves=await carga();vence=Date.now()+300000;}
  const jwk=claves.find(k=>k.kid===cabecera.kid&&k.kty==='RSA');
  if(!jwk||!crypto.verify('RSA-SHA256',Buffer.from(partes[0]+'.'+partes[1]),crypto.createPublicKey({key:jwk,format:'jwk'}),Buffer.from(partes[2],'base64url')))throw Error('Firma de Access inválida.');
  if(typeof datos.email!=='string'||!config.emails.map(x=>x.toLowerCase()).includes(datos.email.toLowerCase()))throw Error('Esta cuenta no tiene acceso a la revisión.');
  return datos.email;
 };
}
function crearPuerta(config,local,validador=crearValidador(config)){
 const origen=new URL(config.origin),prefijo=config.path||'/ojo';
 if(origen.protocol!=='https:'||origen.pathname!=='/'||!/^\/[a-z0-9-]+$/.test(prefijo)||!Number.isInteger(local.puerto)||!/^[a-f0-9]{48,}$/.test(local.token))throw Error('Configuración de la puerta inválida.');
 const upstream='http://127.0.0.1:'+local.puerto+'/'+local.token;
 return http.createServer(async(req,res)=>{
  const responder=(codigo,texto)=>{res.writeHead(codigo,{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store','Referrer-Policy':'no-referrer'});res.end(texto);};
  if(req.headers.host!==origen.host)return responder(403,'Destino no permitido.');
  try{await validador(req.headers['cf-access-jwt-assertion']);}catch(e){return responder(403,'Iniciá sesión con una cuenta permitida en Cloudflare Access.');}
  const u=new URL(req.url,origen);
  if(u.pathname===prefijo){res.writeHead(302,{Location:prefijo+'/','Cache-Control':'no-store'});return res.end();}
  if(!u.pathname.startsWith(prefijo+'/')||u.search)return responder(404,'Ruta inexistente.');
  const ruta=u.pathname.slice(prefijo.length);
  const lectura=/^\/$|^\/estado$|^\/(?:foto|entrada)\/[HR]\d{4}$|^\/(?:intento|revision)\/[a-f0-9-]{36}$/;
  if(!(req.method==='GET'&&lectura.test(ruta))&&!(req.method==='POST'&&ruta==='/reanalizar'))return responder(404,'Ruta inexistente.');
  if(req.method==='POST'&&(req.headers.origin!==origen.origin||req.headers['content-type']!=='application/json'))return responder(403,'Origen no permitido.');
  try{
   let body;
   if(req.method==='POST'){body='';for await(const c of req){body+=c;if(Buffer.byteLength(body)>2048)return responder(413,'Pedido demasiado grande.');}}
   const r=await fetch(upstream+ruta,{method:req.method,headers:body?{'Content-Type':'application/json'}:{},body,signal:AbortSignal.timeout(30000),redirect:'error'});
   const tipo=r.headers.get('content-type')||'application/octet-stream';let bytes=Buffer.from(await r.arrayBuffer());
   if(tipo.includes('text/html')||tipo.includes('application/json')){
    let texto=bytes.toString();texto=texto.replaceAll(upstream,prefijo).replaceAll('/'+local.token,prefijo);
    if(texto.includes(local.token))throw Error('La respuesta contiene configuración privada.');
    bytes=Buffer.from(texto);
   }
   res.writeHead(r.status,{'Content-Type':tipo,'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; img-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"});res.end(bytes);
  }catch(e){responder(502,'El servicio de revisión no responde. No se repite el pedido automáticamente.');}
 });
}
module.exports={crearValidador,crearPuerta};
if(require.main===module){const base=path.resolve(process.argv[2]),cfg=JSON.parse(fs.readFileSync(path.join(base,'analisis/cloudflare-config.json'))),local=JSON.parse(fs.readFileSync(path.join(base,'analisis/reanalisis-config.json'))),server=crearPuerta(cfg,local);server.listen(cfg.puerto,'127.0.0.1',()=>console.log('Acceso protegido a la revisión disponible.'));process.on('SIGTERM',()=>server.close(()=>process.exit(0)));}
