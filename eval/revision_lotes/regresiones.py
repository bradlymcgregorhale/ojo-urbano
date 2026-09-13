"""Registro privado de revisiones y comparación sin inferencia (#51)."""
import argparse
from collections import defaultdict
import hashlib
import html
import json
from pathlib import Path
import re
import sys


def huella(raw):
    return hashlib.sha256(raw).hexdigest()


def leer(path):
    return json.loads(Path(path).read_bytes())


CATEGORIAS = frozenset(leer(Path(__file__).resolve().parents[2] / 'categorias.json'))


def guardar(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def objeto(root, raw):
    digest = huella(raw)
    path = root / 'objetos' / (digest + '.json')
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Objeto alterado: ' + digest)
    else:
        path.write_bytes(raw)
    return digest


def obtener(root, digest):
    if not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Huella inválida')
    raw = (root / 'objetos' / (digest + '.json')).read_bytes()
    if huella(raw) != digest:
        raise ValueError('Objeto alterado: ' + digest)
    return json.loads(raw)


def crear(base, registro, revisiones=()):
    base, registro = Path(base), Path(registro)
    registro.mkdir(parents=True, exist_ok=True)
    (registro / 'objetos').mkdir(exist_ok=True)
    manifest = leer(base / 'entrega/manifest.json')
    fotos = {x['foto']: x for x in manifest['fotos']}
    identidad = [{k: p[k] for k in ('foto', 'sha256', 'sha256_api')}
                 for p in manifest['fotos']]
    conjunto = huella(json.dumps(identidad, sort_keys=True).encode())
    privado = leer(base / 'manifest-privado.json')
    particiones = {x['foto']: x['particion'] for x in privado['fotos']}
    casos, fuentes, omitidas = {}, [], []

    def agregar(foto, r, origen, etiquetas, tipo):
        if foto not in fotos or not re.fullmatch(r'H\d{4}', foto):
            raise ValueError('Foto ajena al conjunto: ' + str(foto))
        path = base / 'analisis' / (foto + '-alto.json')
        raw = path.read_bytes()
        if r.get('original_huella') != huella(raw):
            raise ValueError('La revisión no corresponde al original: ' + foto)
        original = json.loads(raw)
        if (original['foto'] != foto or original['modo'] != 'alto'
                or original['resultado']['modo'] != 'alto'
                or original['entrada_api']['sha256'] != fotos[foto]['sha256_api']
                or original['entrada_api']['original_sha256'] != fotos[foto]['sha256']):
            raise ValueError('Identidad incompatible: ' + foto)
        particion = particiones[foto]
        if particion not in ('desarrollo', 'evaluacion_reservada'):
            raise ValueError('Partición inválida')
        c = casos.setdefault(foto, {'foto': foto, 'particion': particion,
            'original': objeto(registro, raw), 'sha256_foto': fotos[foto]['sha256'],
            'sha256_api': fotos[foto]['sha256_api'], 'modo': 'alto',
            'modo_version': original['resultado']['modo_version'], 'anotaciones': []})
        c['anotaciones'].append({'fuente': origen, 'tipo': tipo, 'revision': r,
                                'etiquetas': etiquetas})

    paths = sorted((base / 'analisis/correcciones-conversacion').glob('*.json'))
    for path in paths:
        raw = path.read_bytes()
        r = json.loads(raw)
        source = objeto(registro, raw)
        fuentes.append(source)
        # La conversación corrige solo los campos nombrados; no aprueba el resto.
        etiquetas = {}
        valores = dict(r.get('categorias', {}))
        for k, v in r.get('correcciones', {}).items():
            if k in valores and valores[k] != v:
                raise ValueError('Correcciones contradictorias: ' + path.name)
            valores[k] = v
        for k, v in valores.items():
            if k not in CATEGORIAS:
                raise ValueError('Categoría desconocida: ' + str(k))
            if v not in ('si', 'no', 'duda', 'sin_revisar'):
                raise ValueError('Etiqueta humana inválida: ' + path.name)
            etiquetas['categorias.' + k] = {'si': 'confirmado', 'no': 'no',
                                           'duda': 'duda', 'sin_revisar': 'sin_revisar'}[v]
        agregar(r['foto'], r, source, etiquetas, 'conversacion_parcial')
        if not etiquetas:
            omitidas.append({'fuente': source, 'motivo': 'Observación sin categoría adjudicada; conservada sin puntuar.'})
    for path in revisiones:
        raw = Path(path).read_bytes()
        export = json.loads(raw)
        if export.get('version') != 2 or export.get('conjunto') != conjunto:
            raise ValueError('Exportación de otro conjunto o versión')
        source = objeto(registro, raw)
        fuentes.append(source)
        for foto, r in export['revisiones'].items():
            if r.get('estado') == 'borrador':
                omitidas.append({'fuente': source, 'foto': foto, 'motivo': 'Borrador sin puntuar.'})
                continue
            if r.get('estado') not in ('aprobado', 'corregido'):
                raise ValueError('Estado humano inválido')
            if r.get('ambito') not in ('via_publica', 'interior', 'indeterminado'):
                raise ValueError('Ámbito humano inválido')
            if r.get('decision') not in ('aceptar', 'rechazar', 'revision'):
                raise ValueError('Decisión humana inválida')
            if r.get('exclusion') not in (None, 'interior'):
                raise ValueError('Exclusión inválida')
            for k, v in r.get('categorias', {}).items():
                if k not in CATEGORIAS:
                    raise ValueError('Categoría desconocida: ' + str(k))
                if v not in ('confirmado', 'posible', 'no', 'sin_revisar'):
                    raise ValueError('Categoría humana inválida')
            for v in r.get('materiales', {}).values():
                if v not in ('si', 'no', 'duda', 'sin_revisar'):
                    raise ValueError('Material humano inválido')
            interior = r.get('ambito') == 'interior'
            if interior and r['decision'] != 'rechazar':
                raise ValueError('Un interior no puede aceptarse')
            if r.get('exclusion') == 'interior' and (not interior or any(
                    v != 'sin_revisar' for g in ('categorias', 'materiales')
                    for v in r.get(g, {}).values())):
                raise ValueError('Exclusión de interior con etiquetas evaluadas')
            etiquetas = ({'interior_rechazado': True} if interior else
                         {'categorias.' + k: v for k, v in r.get('categorias', {}).items()})
            agregar(foto, r, source, etiquetas, 'exportacion_v2')
    banco = {'version': 1, 'conjunto': conjunto, 'base_origen': str(base.resolve()),
             'fuentes': sorted(set(fuentes)),
             'casos': list(casos.values()), 'observaciones_sin_puntuar': omitidas,
             'limites': ['Materiales, prioridad y prosa se conservan sin puntuación automática.',
                        'Duda y sin_revisar no son negativos.',
                        'Una comparación de JSON no evalúa un prompt nuevo.',
                        'La revisión humana tuvo las sugerencias a la vista.']}
    raw = (json.dumps(banco, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()
    nombre = 'registro-' + huella(raw) + '.json'
    destino = registro / nombre
    if destino.exists() and destino.read_bytes() != raw:
        raise ValueError('Registro alterado')
    if not destino.exists():
        destino.write_bytes(raw)
    return destino


def cargar(path):
    path = Path(path)
    raw = path.read_bytes()
    if path.name != 'registro-' + huella(raw) + '.json':
        raise ValueError('La huella del registro no coincide')
    banco = json.loads(raw)
    if banco.get('version') != 1:
        raise ValueError('Versión de registro inválida')
    for digest in banco['fuentes']:
        obtener(path.parent, digest)
    for c in banco['casos']:
        obtener(path.parent, c['original'])
    return banco


def etiquetas(c):
    valores = defaultdict(set)
    for a in c['anotaciones']:
        for campo, v in a['etiquetas'].items():
            if campo.startswith('categorias.') and campo.split('.', 1)[1] not in CATEGORIAS:
                raise ValueError('Categoría desconocida en el registro: ' + campo)
            valores[campo].add(v)
    conocidos = lambda vs: vs - {'duda', 'sin_revisar'}
    conflictos = [k for k, vs in valores.items() if len(vs) > 1 and conocidos(vs)]
    if 'interior_rechazado' in valores and any(k.startswith('categorias.') for k in valores):
        conflictos.append('interior_y_categorias')
    return {k: next(iter(vs)) for k, vs in valores.items() if len(vs) == 1 and conocidos(vs)}, conflictos


def observado(r, campo):
    if (not isinstance(r.get('en_duda', []), list) or
            any(not isinstance(k, str) for k in r.get('en_duda', []))):
        return None
    if r.get('analisis_estado') not in ('completo', 'parcial'):
        return None
    if campo == 'interior_rechazado':
        vacia = (r.get('hay_reclamo') is False and all(
            isinstance(r.get(k), list) and not r[k]
            for k in ('problemas', 'posibles', 'elementos_detectados')))
        if not vacia:
            return False
        evaluacion = r.get('evaluacion_foto')
        if (not isinstance(evaluacion, dict) or
                not isinstance(evaluacion.get('rechazada'), bool) or
                'ambito' not in evaluacion or
                evaluacion['ambito'] not in ('publica', 'interior', 'mixto', 'indeterminado', None)):
            return None
        return evaluacion.get('rechazada') is True and evaluacion.get('ambito') == 'interior'
    key = campo.split('.', 1)[1]
    if any(x.get('key') == key for k in ('problemas', 'elementos_detectados') for x in r.get(k, [])):
        return 'confirmado'
    if any(x.get('key') == key for x in r.get('posibles', [])):
        return 'posible'
    if key in (r.get('en_duda') or []):
        return None
    return 'no' if r.get('analisis_estado') == 'completo' else None


def comparar(registro, candidata=None, particion='desarrollo'):
    path = Path(registro)
    banco = cargar(path)
    filas, faltantes, conflictos, versiones = [], [], [], set()
    versiones_referencia, identicas, version_sin_cambio = set(), [], []
    for c in banco['casos']:
        if c['particion'] != particion:
            continue
        expected, conflict = etiquetas(c)
        if conflict:
            conflictos.append({'foto': c['foto'], 'campos': conflict})
            continue
        if not expected:
            continue
        original = obtener(path.parent, c['original'])
        versiones_referencia.add(c['modo_version'])
        if candidata is None:
            nuevo = original
        else:
            f = Path(candidata) / (c['foto'] + '-alto.json')
            if not f.exists():
                faltantes.append({'foto': c['foto'], 'motivo': 'Falta respuesta candidata'})
                continue
            nuevo = leer(f)
            if huella(f.read_bytes()) == c['original']:
                identicas.append(c['foto'])
            if nuevo.get('resultado', {}).get('modo_version') == c['modo_version']:
                version_sin_cambio.append(c['foto'])
        r = nuevo.get('resultado', {})
        if (nuevo.get('foto') != c['foto'] or nuevo.get('modo') != c['modo']
                or r.get('modo') != c['modo'] or not r.get('modo_version')
                or nuevo.get('entrada_api', {}).get('sha256') != c['sha256_api']
                or nuevo.get('entrada_api', {}).get('original_sha256') != c['sha256_foto']
                or not all(isinstance(r.get(k), list) for k in ('problemas', 'posibles', 'elementos_detectados'))
                or not isinstance(r.get('hay_reclamo'), bool)
                or r.get('analisis_estado') not in ('completo', 'parcial')):
            faltantes.append({'foto': c['foto'], 'motivo': 'Respuesta incompatible o sin verificación'})
            continue
        if candidata is not None and nuevo.get('contexto', '') != original.get('contexto', ''):
            faltantes.append({'foto': c['foto'], 'motivo': 'Cambió el contexto del pedido'})
            continue
        versiones.add(r['modo_version'])
        for campo, valor in expected.items():
            antes, despues = observado(original['resultado'], campo), observado(r, campo)
            if antes is None or despues is None:
                faltantes.append({'foto': c['foto'], 'campo': campo,
                                  'motivo': 'Falta una decisión verificable en la referencia o candidata'})
                continue
            ok_antes, ok_despues = antes == valor, despues == valor
            estado = ('acierto_conservado' if ok_antes and ok_despues else
                      'regresion' if ok_antes else 'corregido' if ok_despues else 'error_persistente')
            filas.append({'foto': c['foto'], 'campo': campo, 'esperado': valor,
                          'referencia': antes, 'candidata': despues, 'estado': estado})
    conteos = {k: sum(f['estado'] == k for f in filas) for k in
               ('acierto_conservado', 'regresion', 'corregido', 'error_persistente')}
    aciertos_previos = conteos['acierto_conservado'] + conteos['regresion']
    preservacion = (candidata is not None and aciertos_previos > 0 and
                   not (faltantes or conflictos or conteos['regresion'] or identicas or version_sin_cambio)
                   and len(versiones) == 1)
    return {'version': 1, 'registro': str(path.resolve()), 'particion': particion,
            'solo_referencia': candidata is None, 'versiones_candidatas': sorted(versiones),
            'versiones_referencia': sorted(versiones_referencia), 'identicas': identicas,
            'version_sin_cambio': version_sin_cambio,
            'sin_regresiones_observadas': preservacion,
            'aciertos_previos_evaluados': aciertos_previos,
            'proteccion_de_aciertos_comprobada': preservacion,
            'aprobacion_de_despliegue': False, 'conteos': conteos, 'filas': filas,
            'faltantes': faltantes, 'conflictos': conflictos, 'limites': banco['limites']}


def informe(resultado, destino):
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=False)
    guardar(destino / 'comparacion.json', resultado)
    esc = lambda x: html.escape(str(x))
    rows = ''.join('<tr>' + ''.join('<td>' + esc(f[k]) + '</td>' for k in
                   ('foto', 'campo', 'esperado', 'referencia', 'candidata', 'estado')) + '</tr>'
                   for f in resultado['filas'])
    texto = ('<html lang="es-AR"><meta charset="utf-8"><title>Regresiones de revisión humana</title>'
             '<style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse}td,th{padding:.6rem;border:1px solid #ccc}pre{white-space:pre-wrap}</style>'
             '<h1>Comparación con revisión humana</h1><p>No constituye una aprobación de despliegue. No evalúa prompts nuevos.</p>'
             '<pre>' + esc(json.dumps({k:v for k,v in resultado.items() if k != 'filas'}, ensure_ascii=False, indent=2)) + '</pre>'
             '<table><tr><th>Foto</th><th>Campo</th><th>Humano</th><th>Referencia</th><th>Candidata</th><th>Resultado</th></tr>' + rows + '</table></html>')
    (destino / 'comparacion.html').write_text(texto)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='accion', required=True)
    a = sub.add_parser('crear')
    a.add_argument('--base', required=True)
    a.add_argument('--registro', required=True)
    a.add_argument('--revision', action='append', default=[])
    a = sub.add_parser('comparar')
    a.add_argument('--registro', required=True)
    a.add_argument('--candidata')
    a.add_argument('--particion', choices=['desarrollo', 'evaluacion_reservada'], default='desarrollo')
    a.add_argument('--informe', required=True)
    args = p.parse_args()
    try:
        if args.accion == 'crear':
            print(crear(args.base, args.registro, args.revision))
            return 0
        r = comparar(args.registro, args.candidata, args.particion)
        informe(r, args.informe)
        print(json.dumps({k:r[k] for k in ('conteos', 'faltantes', 'conflictos',
              'solo_referencia', 'identicas', 'version_sin_cambio',
              'sin_regresiones_observadas', 'proteccion_de_aciertos_comprobada')}, ensure_ascii=False))
        # Un registro hecho solo de errores no comprueba preservación de aciertos.
        return 0 if r['proteccion_de_aciertos_comprobada'] else 1
    except (ValueError, KeyError, OSError, TypeError) as e:
        print('No se pudo validar: ' + str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
