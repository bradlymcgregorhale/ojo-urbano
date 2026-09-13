"""Interfaz de revisión humana de respuestas guardadas por modo (#41)."""
import argparse
import html
import hashlib
import json
import re
from pathlib import Path

RECURSOS = Path(__file__).parent

def mejorar(page, base, ejemplos=()):
    base = Path(base)
    if 'id="human-app"' in page:
        raise ValueError('La interfaz ya está incorporada')
    manifest = json.loads((base / 'entrega/manifest.json').read_text())
    photos = [{k: p[k] for k in ('foto', 'archivo', 'sha256')} for p in manifest['fotos']]
    dataset = hashlib.sha256(json.dumps(photos, sort_keys=True).encode()).hexdigest()
    ids = {p['foto'] for p in photos}
    if not photos or any(e['foto'] not in ids for e in ejemplos):
        raise ValueError('Conjunto vacío o ejemplo de una foto ajena al conjunto')
    modes = {}
    for p in photos:
        modes[p['foto']] = {}
        for mode in ('bajo', 'medio', 'alto'):
            r = json.loads((base / 'analisis' / f"{p['foto']}-{mode}.json").read_text())['resultado']
            keys = lambda values: sorted({v.get('key') or v.get('codigo') for v in values if v.get('key') or v.get('codigo')})
            modes[p['foto']][mode] = {'confirmados': keys(r.get('problemas', []) + r.get('elementos_detectados', [])), 'posibles': keys(r.get('posibles', [])), 'estado': r.get('analisis_estado'), 'costo': r.get('costo_api'), 'consumo_completo': r.get('tokens_api_completos')}
    data = json.dumps({'conjunto': dataset, 'fotos': photos, 'modos': modes, 'ejemplos': [e['foto'] for e in ejemplos]}, ensure_ascii=False).replace('<', '\\u003c')
    css = (RECURSOS / 'revision-humana.css').read_text()
    js = (RECURSOS / 'revision-humana.js').read_text()
    header_end = page.index('</style>') + len('</style>')
    prefix, legacy = page[:header_end], page[header_end:]
    prefix = re.sub(r'(<head\b[^>]*>)', r'\1<meta charset="utf-8">', prefix, count=1, flags=re.I)
    prefix = re.sub(r'<title>.*?</title>', '<title>Ojo Urbano: revisión humana de fotos</title>', prefix)
    legacy = legacy.replace('</html>', '')
    legacy = legacy.replace("document.querySelectorAll('select,input')", "document.querySelectorAll('#legacy select,#legacy input')")
    if 'id="advertencia-referencia"' not in legacy:
        legacy = legacy.replace('<header>', '<header><p id="advertencia-referencia" class="human-warn">La referencia provisional tiene inconsistencias de contenedores y de objetos voluminosos. Estos porcentajes históricos no permiten juzgar precisión hasta revisar la referencia. Las etiquetas originales se conservan.</p>', 1)
    app = '''<section id="human-app" aria-label="Revisión humana"><div class="human-top"><div><div class="human-kicker">OJO URBANO / REVISIÓN HUMANA</div><h1>¿Qué ves en esta foto?</h1><p>Marcá tu lectura antes de revelar los resultados. Podés elegir varias categorías y dejar dudas.</p></div><div><span class="human-count" id="avance">0 / 100</span><p class="human-muted">fotos con revisión terminada</p></div></div>
<progress class="human-progress" id="progreso" max="100" value="0"></progress>
<div class="human-toolbar"><button id="exportar">Exportar mi revisión</button><button id="importar">Importar revisión</button><input id="archivo-revision" type="file" accept=".json,application/json" hidden><button id="ver-puntajes">Ver mis puntajes</button><button id="ver-informe">Ver informe anterior</button></div>
<p class="human-status" id="guardado" role="status" aria-live="polite"></p><p class="human-muted">Se guarda en este navegador, sin enviar tus decisiones al servidor. Exportá una copia al terminar cada tanda. Para cambiar de dispositivo o de enlace, importá ese archivo. Las fotos omitidas quedan pendientes.</p>
<div id="import-preview" class="human-warn" hidden></div>
<details id="guia"><summary>Criterios y ejemplos de referencia</summary><div class="human-card"><p><strong>Primero, lo visible.</strong> Escombros: restos de mampostería o construcción. Una bolsa cerrada sin señales suficientes queda como duda. Residuos comunes: bolsas, envases, papeles o reciclables. Poda: ramas cortadas y restos de jardinería, no vegetación viva.</p><p><strong>Voluminosos incluye más que muebles:</strong> aberturas, sanitarios, placas grandes retiradas y envases de pintura o químicos descartados. Cascotes o fragmentos de mampostería corresponden a escombros. Un objeto en uso no prueba que haya que retirarlo. Cartón no equivale a madera.</p><p><strong>Después, el servicio.</strong> Marcá Sí cuando la escena respalde el reclamo. Usá No evaluable si se ve el material pero no alcanza para decidir el servicio, por ejemplo en un interior. No conviertas una duda en No.</p><p><strong>Contenedores, según los criterios del proyecto:</strong> lateral de húmedos con cuerpo negro u oliva y paredes curvas; bilateral con cuerpo gris y paredes más planas. Los postes no distinguen por sí solos. Sombras y reflejos pueden engañar; ante duda, no fuerces el subtipo. Secos se refiere al contenedor identificado para reciclables. Puede haber varios tipos en la misma foto.</p><!-- EJEMPLOS --><p class="human-muted">Si estos criterios no coinciden con tu interpretación del servicio, explicalo en la nota. Los puntajes no incluyen cantidad de objetos, gravedad ni categorías fuera de esta lista.</p></div></details>
<div class="human-toolbar"><button id="anterior">Anterior</button><label>Foto <select id="foto-select"></select></label><button id="siguiente">Siguiente</button><button id="pendiente">Próxima pendiente</button></div>
<div class="human-grid"><section class="human-image"><a id="foto-grande" target="_blank" rel="noopener"><img id="foto-humana" alt=""><span>Abrir foto en tamaño completo</span></a></section><section class="human-card"><h2 id="foto-titulo"></h2><p id="foto-estado" class="human-muted"></p><form id="form-revision"><h3>1. Material u objeto visible</h3><p class="human-muted">Aunque esté en un interior o en uso.</p><div id="materiales"></div><h3>2. ¿Corresponde confirmar el servicio?</h3><p class="human-muted">Evaluá la escena, no la categoría del vecino.</p><div id="servicios"></div><h3>3. Contenedores presentes</h3><div id="contenedores"></div><label>Notas u otros problemas visibles<textarea id="notas" class="human-text" maxlength="3000" placeholder="Qué se ve, qué no se puede determinar o por qué cambiaste una decisión"></textarea></label><p><label><input type="checkbox" id="exposicion-previa"> Ya había visto resultados o etiquetas de esta foto antes de revisarla.</label></p><p id="exposicion-aviso" class="human-muted"></p><div class="human-toolbar"><button type="submit" class="human-primary" id="terminar">Terminar revisión y seguir</button><button type="button" id="revelar">Revelar resultados de esta foto</button></div><p id="form-mensaje" role="status" aria-live="polite"></p></form></section></div>
<section id="resultados-revelados" class="human-revealed" hidden><h2>Resultados guardados para esta foto</h2><p class="human-warn">La lectura provisional puede contener errores. Tu revisión se guarda aparte. Revelar las respuestas queda registrado.</p><div id="resultados-foto"></div></section>
<section id="puntajes" class="human-card human-metrics" hidden><h2>Puntajes con tu revisión</h2><p>Se calculan solo sobre fotos con revisión terminada. Precisión = confirmaciones correctas / todas las confirmaciones evaluables. Detección = positivos confirmados / positivos humanos evaluables. Un positivo dejado como posible cuenta como pendiente y reduce detección; no se suma a las omisiones. Las dudas y los servicios no evaluables quedan fuera de ambos denominadores.</p><p class="human-muted">El material visible se conserva para revisar reconocimiento, pero no se puntúa automáticamente contra descripciones de texto libre. Esta evaluación mide cuatro servicios y tres presencias de contenedor. No demuestra el aporte aislado del tercer modelo, porque Completo también incluye el especialista.</p><div id="puntajes-contenido"></div></section></section>'''
    imagenes = {p['foto']: p['archivo'] for p in photos}
    guia = '<div class="human-help-grid">' + ''.join(
        '<figure><img src="' + html.escape(imagenes[e['foto']], quote=True) +
        '" loading="lazy" alt="' + html.escape(e['descripcion'], quote=True) +
        '"><figcaption>' + html.escape(e['descripcion']) +
        '. Esta guía registra la exposición de esta foto.</figcaption></figure>'
        for e in ejemplos) + '</div>'
    app = app.replace('<!-- EJEMPLOS -->', guia)
    app = app.replace('0 / 100', f'0 / {len(photos)}').replace('max="100"', f'max="{len(photos)}"')
    return prefix + f'<style>{css}</style><body>' + app + '<div id="legacy" hidden><div class="human-legacy-exit"><button id="volver-revision">Volver a mi revisión</button></div>' + legacy + '</div><noscript>Activá JavaScript para completar y guardar tu revisión.</noscript><script type="application/json" id="human-data">' + data + '</script><script>' + js + '</script></body></html>'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--entrada', required=True, help='Informe anterior sin interfaz de revisión')
    parser.add_argument('--salida', required=True, help='HTML nuevo; no sobrescribe un archivo existente')
    parser.add_argument('--ejemplos', help='JSON privado opcional: lista de foto y descripcion')
    args = parser.parse_args()
    ejemplos = json.loads(Path(args.ejemplos).read_text()) if args.ejemplos else []
    page = mejorar(Path(args.entrada).read_text(), args.base, ejemplos)
    with Path(args.salida).open('x') as f:
        f.write(page)
    print('Interfaz de revisión humana creada, sin inferencias.')
