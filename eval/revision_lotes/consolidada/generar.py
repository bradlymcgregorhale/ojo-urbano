"""Actualiza la página de una ronda existente sin modificar fotos ni revisiones."""
import argparse
import json
from pathlib import Path
import shutil


def generar(destino, revision=None):
    base = Path(__file__).resolve().parent
    destino = Path(destino).resolve()
    datos = json.loads((destino / 'datos.json').read_text())
    catalogo = json.loads((base.parents[2] / 'categorias.json').read_text())
    guardada = json.loads(Path(revision).read_text()) if revision else None
    if guardada:
        fotos = {p['id']: p['sha256'] for p in datos['fotos']}
        if (guardada.get('conjunto') != datos['conjunto'] or
                any(fotos.get(k) != v.get('sha256') for k, v in guardada['revisiones'].items())):
            raise ValueError('La revisión no corresponde a estas fotos.')
    def incrustar(valor):
        return json.dumps(valor, ensure_ascii=False).replace('<', '\\u003c')
    html = (base / 'revision.html').read_text().replace('__DATOS__', incrustar(datos))
    html = html.replace('__CATALOGO__', incrustar(catalogo))
    html = html.replace('__REVISION_BASE__', incrustar(guardada))
    (destino / 'revision.html').write_text(html)
    for nombre in ('mapeo.js', 'propuestas.js', 'solicitudes.js', 'comparacion.js'):
        shutil.copyfile(base / nombre, destino / nombre)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destino')
    parser.add_argument('--revision', help='Exportación humana para recuperar sólo si el navegador no tiene una revisión.')
    args = parser.parse_args()
    generar(args.destino, args.revision)
