function mapearPropuesta(r, keys) {
  const campos = {}, known = new Set(keys), confirmed = new Set();
  for (const p of r.problemas || []) {
    if (known.has(p.key)) { campos[p.key] = 'si'; confirmed.add(p.key); }
  }
  const rechazadas = new Set((r.posibles || []).filter(p => p.arbitro === 'rechazar').map(p => p.key));
  for (const p of [...(r.posibles || []), ...(r.en_duda || [])]) {
    const k = typeof p === 'string' ? p : p.key;
    if (known.has(k) && !confirmed.has(k) && !rechazadas.has(k)) campos[k] = 'dudoso';
  }
  const e = r.evaluacion_foto || {}, c = r.contexto_visual || {};
  if (e.estado_calidad === 'evaluado' && typeof e.calidad_suficiente === 'boolean') {
    campos.calidad = e.calidad_suficiente ? 'suficiente' : 'insuficiente';
  }
  if (c.estado === 'evaluado' && typeof c.suficiente === 'boolean') campos.contexto = c.suficiente ? 'suficiente' : 'insuficiente';
  if (e.estado_ambito === 'evaluado') {
    if (e.ambito === 'publica') campos.ambito = 'via_publica';
    else if (['privada', 'interior'].includes(e.ambito)) campos.ambito = 'privado_interior';
    else campos.ambito = 'dudoso';
  }
  const t = r.contenedores;
  if (t?.estado === 'confirmado' && Array.isArray(t.tipos)) {
    campos.contenedor = t.tipos.length ? 'si' : 'no';
    for (const k of t.tipos) if (known.has(k)) campos[k] = 'si';
  }
  const pp = r.problema_principal;
  const principal = pp?.estado === 'seleccionado' && confirmed.has(pp.key) ? pp.key : '';
  const servicios = [...new Set(r.solicitudes_sugeridas?.servicios || [])].filter(k => confirmed.has(k) && known.has(k));
  const principal_estado = principal ? 'seleccionado' : e.rechazada ? 'foto_invalida' :
    c.suficiente === false || r.solicitudes_sugeridas?.estado === 'requiere_contexto' ? 'necesita_contexto' :
    servicios.length > 1 ? 'varios_servicios' : 'pendiente';
  return {campos, principal, servicios, principal_estado, rechazadas: [...rechazadas]};
}
function tieneCorrecciones(revision, propuesta, keys) {
  const base = mapearPropuesta(propuesta, keys);
  return (revision.principal || '') !== base.principal ||
    (revision.principal_estado !== undefined && revision.principal_estado !== base.principal_estado) ||
    (revision.servicios !== undefined && JSON.stringify([...revision.servicios].sort()) !== JSON.stringify([...base.servicios].sort())) || keys.some(k =>
    (revision.campos[k] || 'sin_revisar') !== (base.campos[k] || 'sin_revisar'));
}
if (typeof module !== 'undefined') module.exports = {mapearPropuesta, tieneCorrecciones};
