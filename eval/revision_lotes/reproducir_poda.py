"""Reproducción acotada del descarte de poda (#45, #51), sin red ni pesos.

El JSON público no contiene toda la entrada interna previa al descarte.
Se reconstruye un escenario mínimo, no una ejecución completa de la API.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))


def reproducir(archivo):
    import politica_escombros as P
    import verificador as V
    raw = Path(archivo).read_bytes()
    r = json.loads(raw)['resultado']
    revisiones = r['verificacion_escombros']['revisiones']
    respuestas = {x['modelo']: x['respuesta'] for x in revisiones if x['estado'] == 'ok'}
    modelos = list(respuestas)
    votos = [v for v in r['modelos'] if any(c['key'] == 'retiro_poda' for c in v['categorias'])]
    if len(votos) < 2 or len(modelos) < 2:
        raise ValueError('Este escenario requiere poda corroborada y revisiones guardadas')
    llamadas = []

    def guardada(modelo, mensajes, **kwargs):
        llamadas.append(modelo)
        return json.dumps(respuestas[modelo])

    with patch.object(socket, 'socket', side_effect=AssertionError('Red prohibida')), \
            patch.object(V, 'modelos_activos', return_value=modelos), \
            patch.object(V, '_imagen_data_url', return_value='imagen_sustituida_sin_inferencia'), \
            patch.object(V, '_llamar', side_effect=guardada):
        revision = V.validar_alcance_escombros(None, '')
    assert sorted(llamadas) == sorted(modelos)
    cat = {'key': 'retiro_poda', 'nombre': 'Retiro de restos de poda',
           'gravedad': 3, 'fuentes': [v['modelo'] for v in votos]}
    entrada = {'problemas': [cat], 'posibles': [{'key': P.KEY, 'nombre': 'Retiro de escombros',
                'gravedad': None, 'fuentes': ['modelo_local']}],
               'categorias_contexto': [], 'descartados_por_foto': [],
               'elementos_detectados': [], 'en_duda': [], 'descripcion': '',
               'foto_valida': None, 'foto_valida_estado': 'sin_contexto',
               'detalle': {'modelo_local': {}, 'verificacion': {
                   'activa': True, 'verificadores': copy.deepcopy(r['modelos'])}}}
    antes = copy.deepcopy(entrada)
    with patch.object(socket, 'socket', side_effect=AssertionError('Red prohibida')):
        salida = P.aplicar(entrada, revision, {P.KEY: {'nombre': 'Retiro de escombros'}})
    assert entrada == antes and Path(archivo).read_bytes() == raw
    return {'foto': json.loads(raw)['foto'], 'original_sha256': hashlib.sha256(raw).hexdigest(),
            'alcance': 'Escenario mínimo reconstruido de consenso de alcance y descarte; no reproduce la API completa ni vuelve a interpretar la foto.',
            'llamadas_openrouter': 0, 'respuestas_guardadas_reutilizadas': len(llamadas),
            'estado_alcance': revision['estado'],
            'retiros_independientes': revision['otros_retiros_independientes'],
            'cumple_poda_humana': any(c['key'] == 'retiro_poda' for c in salida['problemas']),
            'entrada_reconstruida': entrada, 'revision_recalculada': revision,
            'decision': salida['detalle']['verificacion']['decision_alcance'],
            'codigo_sha256': {p: hashlib.sha256((RAIZ / p).read_bytes()).hexdigest()
                              for p in ['politica_escombros.py', 'verificador.py']}}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('archivos', nargs='+')
    p.add_argument('--salida', required=True)
    args = p.parse_args()
    resultado = [reproducir(f) for f in args.archivos]
    with Path(args.salida).open('x') as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(json.dumps([{'foto': r['foto'], 'cumple_poda_humana': r['cumple_poda_humana'],
                       'llamadas_openrouter': 0} for r in resultado]))
    sys.exit(0 if all(r['cumple_poda_humana'] for r in resultado) else 1)
