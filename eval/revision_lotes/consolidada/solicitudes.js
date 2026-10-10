'use strict';
const requestPanel = document.createElement('fieldset');
const requestLegend = document.createElement('legend');
requestLegend.textContent = 'Servicios para solicitar';
requestPanel.append(requestLegend);
const requestHelp = document.createElement('p');
requestHelp.textContent = 'Podés elegir varios. Los problemas visibles se conservan aunque no solicites todos los servicios.';
requestPanel.append(requestHelp);
const requestChoices = document.createElement('div');
requestPanel.append(requestChoices);
$('categorias').after(requestPanel);
const principalStateLabel = document.createElement('label');
principalStateLabel.textContent = 'Si no hay un principal único';
const principalStateSelect = document.createElement('select');
principalStateSelect.id = 'principalEstado';
principalStateSelect.setAttribute('aria-label', 'Motivo sin principal único');
const principalStates = {
  pendiente: 'Todavía no lo decidí',
  varios_servicios: 'Hay varios servicios sin uno principal',
  necesita_contexto: 'Hace falta más información',
  foto_invalida: 'Hace falta otra foto',
  sin_problema: 'No identifico un problema',
};
for (const [value, text] of Object.entries(principalStates)) {
  const option = document.createElement('option');
  option.value = value; option.textContent = text; principalStateSelect.append(option);
}
principalStateLabel.append(principalStateSelect);
$('principal').after(principalStateLabel);
const selectedRequests = new Set();
const validBeforeRequests = valid;
valid = function (incoming) {
  validBeforeRequests(incoming);
  for (const row of Object.values(incoming.revisiones)) {
    if (row.principal_estado && row.principal_estado !== 'seleccionado' && !Object.hasOwn(principalStates, row.principal_estado)) {
      throw Error('El motivo del principal no es válido.');
    }
    if (row.servicios !== undefined && (!Array.isArray(row.servicios) ||
        new Set(row.servicios).size !== row.servicios.length ||
        row.servicios.some(k => !categoryKeys.includes(k) || k === 'sin_problema' || row.campos[k] !== 'si'))) {
      throw Error('Hay un servicio fuera del catálogo.');
    }
  }
  return incoming;
};
function renderRequests() {
  const row = state.revisiones[D.fotos[state.actual].id];
  principalStateLabel.hidden = !!$('principal').value;
  principalStateSelect.value = Object.hasOwn(principalStates, row?.principal_estado) ? row.principal_estado : 'pendiente';
  selectedRequests.clear();
  // Una revisión anterior sin servicios sigue pendiente en este campo.
  for (const k of row?.servicios || []) selectedRequests.add(k);
  requestChoices.replaceChildren();
  const candidates = categoryKeys.filter(k => k !== 'sin_problema' && $(k).value === 'si');
  if (!candidates.length) {
    const empty = document.createElement('p');
    empty.textContent = 'Confirmá un problema visible para poder elegir su servicio.';
    requestChoices.append(empty);
  }
  for (const k of candidates) {
    const label = document.createElement('label'), input = document.createElement('input');
    input.type = 'checkbox'; input.value = k; input.checked = selectedRequests.has(k);
    input.setAttribute('aria-label', 'Solicitar: ' + catalogo[k].nombre);
    label.append(input, document.createTextNode(catalogo[k].nombre));
    input.onchange = () => {
      if (input.checked) selectedRequests.add(k); else selectedRequests.delete(k);
      try { draft(); } catch (error) { $('status').textContent = error.message; }
    };
    requestChoices.append(label);
  }
}
const renderBeforeRequests = render;
render = function () { renderBeforeRequests(); renderRequests(); };
draftMetadata = () => ({
  servicios: [...selectedRequests].filter(k => $(k).value === 'si'),
  principal_estado: $('principal').value ? 'seleccionado' : principalStateSelect.value,
});
const draftBeforeRequests = draft;
draft = function () {
  draftBeforeRequests();
  renderRequests();
};
principalStateSelect.onchange = () => {
  try { draft(); } catch (error) { $('status').textContent = error.message; }
};
try { valid(state); } catch (error) { blocked = true; $('status').textContent = error.message; }
renderRequests();
