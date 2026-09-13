"""Reproducción acotada del descarte de poda y voluminosos (#45, #51), sin red ni pesos.

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


def reproducir(archivo, retiros=("retiro_poda",)):
    nombres = {"retiro_poda": "Retiro de restos de poda",
               "retiro_muebles": "Retiro de muebles y voluminosos"}
    if not retiros or any(k not in nombres for k in retiros) or len(set(retiros)) != len(retiros):
        raise ValueError("Seleccioná retiros distintos entre poda y voluminosos")
    import politica_escombros as P
    import verificador as V
    raw = Path(archivo).read_bytes()
    r = json.loads(raw)['resultado']
    revisiones = r['verificacion_escombros']['revisiones']
    respuestas = {x['modelo']: x['respuesta'] for x in revisiones if x['estado'] == 'ok'}
    modelos = list(respuestas)
    votos = {key: {v['modelo'] for v in r['modelos']
                   if v.get('ok') is True and v.get('modelo') and any(
                       c['key'] == key and not c.get('anulada_por') and str(c.get('evidencia') or '').strip()
                       for c in v['categorias'])} for key in retiros}
    if any(len(fuentes) < 2 for fuentes in votos.values()) or len(modelos) < 2:
        raise ValueError('Este escenario requiere cada retiro corroborado y revisiones guardadas')
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
    categorias = [{'key': key, 'nombre': nombres[key], 'gravedad': 3,
                   'fuentes': sorted(votos[key])} for key in retiros]
    entrada = {'problemas': categorias, 'posibles': [{'key': P.KEY, 'nombre': 'Retiro de escombros',
                'gravedad': None, 'fuentes': ['modelo_local']}],
               'categorias_contexto': [], 'descartados_por_foto': [],
               'elementos_detectados': [], 'en_duda': [], 'descripcion': '',
               'foto_valida': None, 'foto_valida_estado': 'sin_contexto',
               'detalle': {'modelo_local': {}, 'verificacion': {
                   'activa': True, 'verificadores': copy.deepcopy(r['modelos'])}}}
    antes = copy.deepcopy(entrada)
    with patch.object(socket, 'socket', side_effect=AssertionError('Red prohibida')):
        salida = P.aplicar(entrada, revision, {P.KEY: {'nombre': 'Retiro de escombros'}})
    # Aísla el aporte de la regla nueva sin reinterpretar la foto.
    with patch.object(socket, 'socket', side_effect=AssertionError('Red prohibida')), \
            patch.object(P, '_retiros_visibles_publicos', return_value=set()):
        sin_preservacion = P.aplicar(entrada, revision, {P.KEY: {'nombre': 'Retiro de escombros'}})
    conservados = {c['key'] for c in salida['problemas']}
    anteriores = {c['key'] for c in sin_preservacion['problemas']}
    aporte = {key: key in conservados and key not in anteriores for key in retiros}
    assert entrada == antes and Path(archivo).read_bytes() == raw
    return {'foto': json.loads(raw)['foto'], 'original_sha256': hashlib.sha256(raw).hexdigest(),
            'alcance': 'Escenario mínimo reconstruido de consenso de alcance y descarte; no reproduce la API completa ni vuelve a interpretar la foto.',
            'llamadas_openrouter': 0, 'respuestas_guardadas_reutilizadas': len(llamadas),
            'estado_alcance': revision['estado'],
            'retiros_independientes': revision['otros_retiros_independientes'],
            'retiros_evaluados': list(retiros),
            'regla_preservacion_necesaria': aporte,
            'problemas_sin_regla_preservacion': sorted(anteriores),
            'cumple_retiros': {key: any(c['key'] == key for c in salida['problemas']) for key in retiros},
            'cumple_poda_humana': (any(c['key'] == 'retiro_poda' for c in salida['problemas'])
                                   if 'retiro_poda' in retiros else None),
            'entrada_reconstruida': entrada, 'revision_recalculada': revision,
            'decision': salida['detalle']['verificacion']['decision_alcance'],
            'codigo_sha256': {p: hashlib.sha256((RAIZ / p).read_bytes()).hexdigest()
                              for p in ['politica_escombros.py', 'verificador.py']}}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('archivos', nargs='+')
    p.add_argument('--salida', required=True)
    p.add_argument('--retiro', action='append', choices=('retiro_poda', 'retiro_muebles'),
                   help='Se puede repetir para comprobar ambos retiros; por defecto, poda')
    args = p.parse_args()
    resultado = [reproducir(f, args.retiro or ('retiro_poda',)) for f in args.archivos]
    with Path(args.salida).open('x') as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(json.dumps([{'foto': r['foto'], 'cumple_poda_humana': r['cumple_poda_humana'],
                       'cumple_retiros': r['cumple_retiros'],
                       'regla_preservacion_necesaria': r['regla_preservacion_necesaria'],
                       'llamadas_openrouter': 0} for r in resultado]))
    sys.exit(0 if all(all(r['regla_preservacion_necesaria'].values()) for r in resultado) else 1)
