const test = require('node:test');
const assert = require('node:assert/strict');
const {mapearPropuesta, tieneCorrecciones} = require('./mapeo.js');
const keys = ['tapa_vereda', 'reparacion_vereda', 'retiro_escombros', 'retiro_muebles', 'calidad', 'contexto', 'ambito', 'contenedor'];

test('una categoría rechazada no se transforma en duda ni en negativo humano', () => {
  const p = mapearPropuesta({posibles:[{key:'retiro_escombros',arbitro:'rechazar'}], en_duda:['retiro_escombros']}, keys);
  assert.equal(p.campos.retiro_escombros, undefined);
  assert.deepEqual(p.rechazadas, ['retiro_escombros']);
});
test('una categoría confirmada prevalece sobre el registro de una disputa anterior', () => {
  const p = mapearPropuesta({problemas:[{key:'retiro_muebles'}], posibles:[{key:'retiro_muebles',arbitro:'rechazar'}]}, keys);
  assert.equal(p.campos.retiro_muebles,'si');
});
test('campos no evaluados conservan el estado sin revisar', () => {
  const p = mapearPropuesta({evaluacion_foto:{ambito:null,estado_ambito:'indeterminado'}, contenedores:{estado:'revision',tipos:null}}, keys);
  assert.deepEqual(p.campos, {});
  assert.equal(p.principal_estado, 'pendiente');
});
test('servicios propuestos tienen que estar confirmados y dentro del catálogo', () => {
  const p = mapearPropuesta({problemas:[{key:'tapa_vereda'}], solicitudes_sugeridas:{servicios:['tapa_vereda','inventado','retiro_escombros','tapa_vereda']}}, keys);
  assert.deepEqual(p.servicios,['tapa_vereda']);
});
test('varios servicios y falta de contexto conservan motivos diferentes', () => {
  const r = {problemas:[{key:'retiro_muebles'},{key:'retiro_escombros'}],solicitudes_sugeridas:{servicios:['retiro_muebles','retiro_escombros']}};
  assert.equal(mapearPropuesta(r,keys).principal_estado,'varios_servicios');
  r.contexto_visual = {suficiente:false,estado:'evaluado'};
  assert.equal(mapearPropuesta(r,keys).principal_estado,'necesita_contexto');
});
test('guardar sin cambiar no se registra como una corrección', () => {
  const r = {problemas:[{key:'tapa_vereda'}],problema_principal:{estado:'seleccionado',key:'tapa_vereda'},solicitudes_sugeridas:{servicios:['tapa_vereda']}};
  const revision = mapearPropuesta(r,keys);
  assert.equal(tieneCorrecciones(revision,r,keys),false);
  revision.campos.reparacion_vereda = 'dudoso';
  assert.equal(tieneCorrecciones(revision,r,keys),true);
  revision.campos.reparacion_vereda = 'sin_revisar';revision.servicios=[];
  assert.equal(tieneCorrecciones(revision,r,keys),true);
});
