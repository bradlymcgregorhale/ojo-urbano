"""Descarte independiente de pertenencias, sin pesos ni inferencias reales."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import verificador as V

CATS = json.loads((Path(__file__).parent / 'categorias.json').read_text())
LOCAL = {'predichas': [{'key': 'situacion_calle', 'score': 1}],
         'probabilidades': [{'key': 'situacion_calle', 'score': 1},
                            {'key': 'recoleccion', 'score': .0001}],
         'gravedad': {'value': 3, 'raw': 3}}


def lectura(estado, **cambios):
    d = {'estado': estado, 'objeto': 'bolsas de basura descartadas',
         'ubicacion': 'junto al contenedor, aparte del cartón usado por la persona',
         'evidencia': 'bolsas rotas con residuos derramados fuera del lugar de descanso'}
    d.update(cambios)
    return {'veredicto': 'identificado', 'que_es': 'persona, cartón y mantas',
            'ubicacion': 'vereda', 'evidencia': 'cartones bajo la persona',
            'descarte_independiente': d}


class RecoleccionPertenenciasTest(unittest.TestCase):
    def correr(self, respuestas, *, consenso=False, dos=False, persona=True, arbitro=False, retiro="recoleccion",
               ambos=False, figura=False):
        modelos = ['m1', 'm2'] if dos else ['m1', 'm2', 'm3']
        votos = {}
        for m in modelos:
            cats = []
            if persona and ((m == 'm2') if figura else (m != 'm3' or dos)):
                cats.append({'key': 'situacion_calle', 'gravedad': 3,
                             'evidencia': 'persona durmiendo sobre cartón y frazada'})
            retiro_propuesto = (m != 'm2') if figura else (consenso or m == modelos[-1])
            if retiro_propuesto:
                cats.append({'key': retiro, 'gravedad': 2,
                             'evidencia': 'textiles plegados y cartón sobre la vereda'})
            if ambos:
                cats.append({'key': 'recoleccion', 'gravedad': 2,
                             'evidencia': 'cartones y textiles junto a la persona'})
            votos[m] = {'modelo': m, 'ok': True, 'categorias': cats,
                        'sin_problema': False, 'foto_corresponde': None,
                        'categorias_contexto': [], 'descripcion': 'Cartones bajo la persona.'}
        calls = []

        def llamar(m, mensajes, **kwargs):
            calls.append(m)
            self.assertIn(mensajes[0]['content'], (V._PROMPT_RECOLECCION_PERTENENCIAS,
                                                   V._PROMPT_VOLUMINOSOS_PERTENENCIAS))
            if not ambos:
                self.assertEqual(mensajes[0]['content'],
                                 V._PROMPT_VOLUMINOSOS_PERTENENCIAS if retiro == 'retiro_muebles'
                                 else V._PROMPT_RECOLECCION_PERTENENCIAS)
            self.assertEqual(kwargs['max_tokens'], 2000)
            r = respuestas.get(m, {})
            if isinstance(r, Exception):
                raise r
            return json.dumps(r)

        local = copy.deepcopy(LOCAL)
        if not persona:
            local = {'predichas': [], 'probabilidades': [], 'gravedad': {'value': 2}}
        with patch.multiple(V, VERIFICADORES=modelos, CONSENSO_VLM_SOLO='confirma',
                            ARBITRO_CONFIRMA=arbitro), \
                patch.object(V, '_verificar_uno', side_effect=lambda m, *a: copy.deepcopy(votos[m])), \
                patch.object(V, '_llamar', side_effect=llamar), \
                patch.object(V, '_arbitrar', return_value={'ok': True, 'decisiones': [
                    {'key': retiro, 'veredicto': 'confirmar'}], 'descripcion': ''}):
            r = V.verificar(Image.new('RGB', (20, 20)), CATS, local)
        return r, calls

    def claves(self, r):
        return {c['key'] for c in r['confirmadas']}

    def test_pregunta_habitual_conserva_su_limite(self):
        with patch.object(V, '_llamar', return_value=json.dumps({
                'veredicto': 'identificado', 'que_es': 'bolsas', 'ubicacion': 'cordón'})) as llamar:
            V._pregunta_abierta(Image.new('RGB', (20, 20)), ['m1'])
        self.assertEqual(llamar.call_args.kwargs['max_tokens'], 400)
        self.assertEqual(llamar.call_args.args[1][0]['content'], V._PROMPT_PREGUNTA_ABIERTA)

    def test_carton_y_mantas_en_uso_no_confirman(self):
        r, calls = self.correr({m: lectura('ausente', objeto=None,
                                         evidencia='cartones bajo la persona y manta que la cubre')
                               for m in ('m1', 'm2')})
        self.assertEqual(self.claves(r), {'situacion_calle'})
        self.assertEqual(set(calls), {'m1', 'm2'})
        self.assertTrue(r['repreguntas'][0]['descarte_independiente'])

    def test_escena_mixta_conserva_dos_problemas(self):
        r, calls = self.correr({m: lectura('visible') for m in ('m1', 'm2')})
        self.assertEqual(self.claves(r), {'situacion_calle', 'recoleccion'})
        rec = next(c for c in r['confirmadas'] if c['key'] == 'recoleccion')
        self.assertEqual(set(rec['fuentes']), {'m1', 'm2'})
        self.assertTrue(rec['repregunta'])
        self.assertEqual(len(calls), 2)

    def test_consenso_inicial_no_saltea_el_descarte(self):
        for dos in (False, True):
            with self.subTest(dos=dos):
                r, calls = self.correr({m: lectura('ausente') for m in ('m1', 'm2')},
                                      consenso=True, dos=dos)
                self.assertNotIn('recoleccion', self.claves(r))
                self.assertIn('situacion_calle', self.claves(r))
                self.assertEqual(len(calls), 2)

    def test_desacuerdo_fallo_o_datos_incompletos_no_confirman(self):
        variantes = [lectura('ausente'), lectura('indeterminado'), {},
                     RuntimeError('lector no responde'),
                     lectura('visible', objeto=None), lectura('visible', ubicacion=None),
                     lectura('visible', evidencia=None), lectura('visible', objeto=['cartón']),
                     lectura('visible', ubicacion=123), lectura('visible', evidencia=True),
                     dict(lectura('visible'), veredicto='no_identificable'),
                     dict(lectura('visible'), descarte_independiente='visible')]
        for segundo in variantes:
            with self.subTest(segundo=segundo):
                r, _ = self.correr({'m1': lectura('visible'), 'm2': segundo}, consenso=True,
                                  arbitro=True)
                self.assertNotIn('recoleccion', self.claves(r))
                self.assertIn('situacion_calle', self.claves(r))
                self.assertIn('recoleccion', {c['key'] for c in r['posibles']})

    def test_recoleccion_sin_persona_no_agrega_pasadas(self):
        r, calls = self.correr({}, consenso=True, persona=False)
        self.assertEqual(self.claves(r), {'recoleccion'})
        self.assertEqual(calls, [])

    def test_cupo_cero_no_deja_pasar_consenso_sin_evaluar(self):
        with patch.object(V, 'REPREGUNTA_MAX', 0):
            r, calls = self.correr({}, consenso=True)
        self.assertNotIn('recoleccion', self.claves(r))
        self.assertEqual(len(calls), 2)


    def test_voluminoso_cubierto_no_se_confirma_por_consenso(self):
        for dos in (False, True):
            for estado in ('ausente', 'indeterminado'):
                with self.subTest(dos=dos, estado=estado):
                    r, calls = self.correr({m: lectura(estado, objeto=None,
                        evidencia='figura humana acostada cubierta con una manta')
                        for m in ('m1', 'm2')}, consenso=True, dos=dos,
                        retiro='retiro_muebles', arbitro=True)
                    self.assertEqual(self.claves(r), {'situacion_calle'})
                    self.assertEqual(len(calls), 2)
                    self.assertTrue(r['repreguntas'][0]['descarte_independiente'])

    def test_dos_lectores_confunden_figura_y_otro_reconoce_persona(self):
        r, calls = self.correr({m: lectura('ausente', objeto=None,
            evidencia='persona cubierta por una manta, sin otro objeto descartado')
            for m in ('m1', 'm2')}, figura=True, retiro='retiro_muebles', arbitro=True)
        self.assertEqual(self.claves(r), {'situacion_calle'})
        self.assertEqual(len(calls), 2)

    def test_mueble_descartado_separado_conserva_escena_mixta(self):
        r, calls = self.correr({m: lectura('visible', objeto='sillón roto',
            ubicacion='al lado del contenedor, separado de la persona',
            evidencia='sillón con patas rotas y relleno desprendido fuera del lugar de descanso')
            for m in ('m1', 'm2')}, consenso=True, retiro='retiro_muebles')
        self.assertEqual(self.claves(r), {'situacion_calle', 'retiro_muebles'})
        self.assertEqual(len(calls), 2)

    def test_voluminoso_sin_jurado_completo_no_se_confirma(self):
        for segundo in ({}, RuntimeError('sin respuesta'), lectura('indeterminado'),
                        lectura('visible', objeto=None), lectura('visible', evidencia=None)):
            with self.subTest(segundo=segundo):
                r, _ = self.correr({'m1': lectura('visible'), 'm2': segundo},
                    consenso=True, retiro='retiro_muebles', arbitro=True)
                self.assertNotIn('retiro_muebles', self.claves(r))
                self.assertIn('situacion_calle', self.claves(r))

    def test_voluminoso_sin_persona_conserva_consenso_sin_auditoria(self):
        r, calls = self.correr({}, consenso=True, persona=False, retiro='retiro_muebles')
        self.assertEqual(self.claves(r), {'retiro_muebles'})
        self.assertEqual(calls, [])

    def test_dos_retiros_no_saltan_auditoria_por_cupo(self):
        with patch.object(V, 'REPREGUNTA_MAX', 0):
            r, calls = self.correr({m: lectura('indeterminado') for m in ('m1', 'm2')},
                consenso=True, retiro='retiro_muebles', ambos=True)
        self.assertEqual(self.claves(r), {'situacion_calle'})
        self.assertEqual(len(calls), 4)
        self.assertEqual({p['key'] for p in r['repreguntas']}, {'retiro_muebles', 'recoleccion'})


if __name__ == '__main__':
    unittest.main()
