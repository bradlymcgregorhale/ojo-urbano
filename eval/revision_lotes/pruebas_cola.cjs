/* Pruebas de recuperación y presupuesto sin red ni inferencias. */
const fs=require('fs'),os=require('os'),path=require('path'),crypto=require('crypto'),assert=require('assert'),{spawnSync}=require('child_process');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ojo-cola-prueba-')),runner=path.join(__dirname,'procesar.cjs');
function test(name,setup,check){
 const base=path.join(root,name),out=path.join(base,'analisis'),ent=path.join(base,'entrega');fs.mkdirSync(out,{recursive:true});fs.mkdirSync(ent);
 const raw=Buffer.from('foto sintética'),sha=crypto.createHash('sha256').update(raw).digest('hex');fs.writeFileSync(path.join(ent,'foto.jpg'),raw);
 const manifest={fotos:[{foto:'H0001',archivo:'foto.jpg',sha256:sha,entrada_api:'foto.jpg',sha256_api:sha}]};const mr=JSON.stringify(manifest);fs.writeFileSync(path.join(ent,'manifest.json'),mr);
 fs.writeFileSync(path.join(out,'entradas-api.json'),JSON.stringify({H0001:{archivo:'foto.jpg',original_sha256:sha,sha256:sha}}));
 const calls=path.join(base,'llamadas.jsonl'),mock=path.join(base,'mock.cjs');
 fs.writeFileSync(mock,`const fs=require('fs');module.exports={launch:async()=>({newPage:async()=>({goto:async()=>{},evaluate:async(fn,p)=>{fs.appendFileSync(${JSON.stringify(calls)},JSON.stringify(p)+'\\n');const scenario=JSON.parse(fs.readFileSync(${JSON.stringify(path.join(base,'respuesta.json'))}));if(scenario.error)throw Error(scenario.error);return scenario;}}),close:async()=>{}})};`);
 const plan={autorizado:true,tope_usd:25,reserva_antes_de_foto_usd:1,manifest_sha256:crypto.createHash('sha256').update(mr).digest('hex'),puppeteer:mock,python:process.env.OJO_PYTHON||'python3',url:'https://prueba.invalid'};
 const state={estado:'preparado',resultados:[],gasto_observado_usd:0,reserva_incierta_usd:0,reserva_acotada_usd:0,activo:null};
 const response={http:200,data:{estado:'listo',resultado:{modo:'alto',modo_version:'prueba',costo_api:.015,tokens_api_completos:true,analisis_estado:'completo'}}};
 setup({plan,state,response,base,out,ent});fs.writeFileSync(path.join(out,'plan.json'),JSON.stringify(plan));fs.writeFileSync(path.join(out,'estado.json'),JSON.stringify(state));fs.writeFileSync(path.join(base,'respuesta.json'),JSON.stringify(response));
 const run=spawnSync(process.execPath,[runner,base],{encoding:'utf8',timeout:15000});const final=JSON.parse(fs.readFileSync(path.join(out,'estado.json')));const requests=fs.existsSync(calls)?fs.readFileSync(calls,'utf8').trim().split('\n').map(JSON.parse):[];
 check({run,final,requests,base,out});assert(!fs.existsSync(path.join(out,'proceso.lock')));console.log(name,'OK');
}
test('sin_autorizacion',({plan})=>{plan.autorizado=false},({run,requests})=>{assert.notEqual(run.status,0);assert.equal(requests.length,0)});
for(const campo of ['pausa_usuario','motivo_pausa'])test('pausa_usuario_'+campo,({state})=>{state[campo]=campo==='pausa_usuario'?true:'pausa_usuario_para_correcciones'},({run,requests,final})=>{assert.notEqual(run.status,0);assert.equal(requests.length,0);assert.equal(final.estado,'preparado');assert.equal(final.resultados.length,0);assert.equal(final[campo],campo==='pausa_usuario'?true:'pausa_usuario_para_correcciones')});
test('tope',({plan})=>{plan.tope_usd=.5},({final,requests})=>{assert.equal(final.motivo_pausa,'presupuesto');assert.equal(requests.length,0)});
test('envio_incierto',({response})=>{response.error='Conexión interrumpida'},({final,requests})=>{assert.equal(requests.length,1);assert.equal(final.activo.foto,'H0001');assert(!final.activo.trabajo)});
test('no_repetir_envio_incierto',({state})=>{state.activo={foto:'H0001'}},({requests})=>assert.equal(requests.length,0));
test('reanudar_trabajo',({state})=>{state.activo={foto:'H0001',trabajo:'trabajo-prueba',inicio:Date.now()}},({final,requests})=>{assert.equal(requests.length,1);assert.equal(requests[0].method,'GET');assert.equal(final.estado,'terminado');assert(Math.abs(final.gasto_observado_usd-.015001)<1e-9)});
test('uso_incompleto',({state,response})=>{state.activo={foto:'H0001',trabajo:'prueba',inicio:Date.now()};response.data.resultado.tokens_api_completos=false},({final})=>{assert.equal(final.resultados.length,1);assert.equal(final.reserva_incierta_usd,1);assert.equal(final.estado,'pausado')});
test('version_cambio',({state})=>{state.modo_version='anterior'},({final})=>{assert.equal(final.motivo_pausa,'cambio_de_version');assert.equal(final.resultados.length,1)});
test('version_no_reanuda',({state})=>{state.motivo_pausa='cambio_de_version'},({requests})=>assert.equal(requests.length,0));
test('respuesta_sin_asiento',({out})=>fs.writeFileSync(path.join(out,'H0001-alto.json'),'{}'),({requests,final})=>{assert.equal(requests.length,0);assert(final.error_ejecucion.includes('sin asiento'))});
test('foto_alterada',({ent})=>fs.writeFileSync(path.join(ent,'foto.jpg'),'cambio'),({requests})=>assert.equal(requests.length,0));
console.log(JSON.stringify({pruebas:12,carpeta:root,red:false}));
