'use strict';
const comparisonPanel = document.createElement('details');
const comparisonTitle = document.createElement('summary');
comparisonTitle.textContent = 'Resultado de las correcciones';
const comparisonText = document.createElement('p');
const comparisonButton = document.createElement('button');
comparisonButton.textContent = 'Usar propuesta corregida';
comparisonPanel.append(comparisonTitle, comparisonText, comparisonButton);
box.after(comparisonPanel);
let comparisons = {};
function showComparison() {
  const id = D.fotos[state.actual].id, item = comparisons[id];
  comparisonPanel.hidden = !item;
  comparisonButton.hidden = !item?.verificado;
  if (!item) return;
  const result = item.propuesta.resultado;
  const problems = (result.problemas || []).map(p => catalogo[p.key]?.nombre || p.key);
  const services = (result.solicitudes_sugeridas?.servicios || []).map(k => catalogo[k]?.nombre || k);
  comparisonText.textContent = [item.nota,
    'Problemas confirmados: ' + (problems.join(', ') || 'ninguno') + '.',
    'Servicios propuestos: ' + (services.join(', ') || 'pendientes de revisión') + '.',
    result.solicitudes_sugeridas?.motivo,
    'Tu revisión guardada se conserva.'].filter(Boolean).join(' ');
  comparisonButton.onclick = () => applyProposal(id, true, item.propuesta);
}
const renderBeforeComparison = render;
render = function () { renderBeforeComparison(); showComparison(); };
comparisonPanel.hidden = true;
fetch('candidatos.json', {cache: 'no-store'}).then(async response => {
  if (response.status === 404) return;
  if (!response.ok) throw Error('No se pudo cargar la comparación.');
  const incoming = await response.json();
  if (incoming.conjunto !== D.conjunto) throw Error('La comparación corresponde a otra ronda.');
  for (const [id, item] of Object.entries(incoming.fotos || {})) {
    const photo = D.fotos.find(p => p.id === id);
    if (!photo || photo.sha256 !== item.sha256 || item.propuesta?.foto !== id || !item.propuesta.resultado) {
      throw Error('La comparación contiene una foto distinta.');
    }
  }
  comparisons = incoming.fotos || {};
  showComparison();
}).catch(error => {
  comparisonPanel.hidden = false;
  comparisonText.textContent = error.message;
  comparisonButton.hidden = true;
});
