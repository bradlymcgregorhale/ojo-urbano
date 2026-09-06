"""Regresiones del desacuerdo secos/húmedos, sin cargar pesos ni llamar APIs."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import verificador as V

SECO = 'contenedor_secos'
LATERAL = 'contenedor_humedos_lateral'
BILATERAL = 'contenedor_humedos_bilateral'
LOCAL = {
    'predichas': [{'key': k, 'score': s} for k, s in
                 [('recoleccion', 1.0), (SECO, 0.9949), ('retiro_muebles', 0.6696)]],
    'probabilidades': [{'key': k, 'score': s} for k, s in
                       [('recoleccion', 1.0), (SECO, 0.9949),
                        ('retiro_muebles', 0.6696), (LATERAL, 0.0399),
                        (BILATERAL, 0.0092)]],
    'gravedad': {'value': 2, 'raw': 2.85},
}
VOTOS = [
    {'modelo': m, 'ok': True, 'sin_problema': False, 'foto_corresponde': None,
     'categorias_contexto': [],
     'categorias': [{'key': k, 'gravedad': 1, 'evidencia': e},
                    {'key': 'recoleccion', 'gravedad': 3,
                     'evidencia': 'bolsas y basura suelta junto al cordón'}],
     'descripcion': desc}
    for m, k, e, desc in [
        ('m1', SECO, 'contenedor verde reciclables visible en la vereda',
         'Contenedor verde de reciclables junto a bolsas de basura.'),
        ('m2', LATERAL, 'contenedor lateral verde visible',
         'Contenedor verde lateral desplazado de su base.'),
        ('m3', SECO, 'contenedor municipal verde brillante en primer plano',
         'Contenedor verde municipal y residuos domésticos sobre la vereda.')]]
VOTOS[1]['categorias'].append({
    'key': 'reposicion_contenedor', 'gravedad': 3,
    'evidencia': 'contenedor lateral inclinado y fuera de sus topes'})
DESCRIPCION = ('La fotografía muestra un contenedor verde de reciclables y un '
               'contenedor lateral para residuos húmedos, ambos visibles en la vereda. '
               'Además, se observan bolsas de basura y residuos sueltos acumulados '
               'sobre la vereda y junto al cordón, lo que requiere recolección.')


class ContenedoresTest(unittest.TestCase):
    @staticmethod
    def oscuro():
        local = copy.deepcopy(LOCAL)
        local['revision_contenedores'] = 'contenedores-preservacion-20260906'
        local['probabilidades'] = [{'key': k, 'score': s} for k, s in
                                   [(SECO, .999), (LATERAL, .001), (BILATERAL, .001),
                                    ('recoleccion', 1)]]
        votes = copy.deepcopy(VOTOS)
        for v in votes:
            v['categorias'] = [dict(c, key=LATERAL) if c['key'] == SECO else c
                               for c in v['categorias'] if c['key'] != 'reposicion_contenedor']
            v['descripcion'] = 'Contenedor oscuro y bolsas de basura.'
        return local, votes

    def test_discrepancia_oscura_exige_revision_y_tres_votos_independientes(self):
        local, votes = self.oscuro()
        self.assertEqual(V._secos_local_discrepante(local, votes), LATERAL)
        with_repair = copy.deepcopy(votes)
        for v in with_repair:
            v['categorias'].append({'key': 'reparacion_contenedor'})
        self.assertEqual(V._secos_local_discrepante(local, with_repair), LATERAL)
        for score in (None, float('nan'), float('inf'), -.1):
            invalid = copy.deepcopy(local)
            invalid['probabilidades'][0]['score'] = score
            self.assertIsNone(V._secos_local_discrepante(invalid, votes))
        for variant in ('revision', 'debil', 'ausente', 'duplicado', 'fallo', 'verde', 'dos', 'mixto'):
            l, v = copy.deepcopy(local), copy.deepcopy(votes)
            if variant == 'revision': l.pop('revision_contenedores')
            elif variant == 'debil': l['probabilidades'][0]['score'] = .98
            elif variant == 'ausente': l['probabilidades'].pop(2)
            elif variant == 'duplicado': v[1]['modelo'] = v[0]['modelo']
            elif variant == 'fallo': v[1]['ok'] = False
            elif variant == 'verde': v[1]['categorias'][0]['key'] = SECO
            elif variant == 'dos': v[1]['categorias'].append({'key': SECO})
            elif variant == 'mixto': v[1]['categorias'][0]['key'] = BILATERAL
            with self.subTest(variant=variant):
                self.assertIsNone(V._secos_local_discrepante(l, v))

    def test_verde_explicito_no_cuenta_color_ambiguo_ni_negado(self):
        self.assertEqual(V._verdes_explicitos([
            ('m1', 'Contenedor verde oscuro'), ('m2', 'Contenedor oscuro'),
            ('m3', 'Contenedor negro o verde'), ('m4', 'No es verde'),
            ('m5', 'Contenedor gris oliva')]), [('m1', 'Contenedor verde oscuro')])

    def test_omision_de_tipo_no_equivale_a_ausencia_del_contenedor(self):
        local, votes = self.oscuro()
        votes[0]['categorias'] = [c for c in votes[0]['categorias'] if c['key'] != LATERAL]
        self.assertEqual(V._secos_local_discrepante(local, votes), LATERAL)
        votes[1]['categorias'] = [c for c in votes[1]['categorias'] if c['key'] != LATERAL]
        self.assertIsNone(V._secos_local_discrepante(local, votes))

    def test_segunda_mirada_oscura_necesita_color_y_ausencia_de_otro_contenedor(self):
        green = [('m1', 'Contenedor verde'), ('m2', 'Cuerpo pintado de verde')]
        absent = [('m1', 'No hay otro contenedor'), ('m2', 'Solo hay un contenedor')]
        cases = [
            ((green, [], False), ([], absent, False), True),
            ((green[:1], [], False), None, False),
            ((green, [('m3', 'No es verde')], False), None, False),
            ((green, [], True), None, False),
            ((green, [], False), (green, [], False), False),
            ((green, [], False), ([], absent, True), False),
        ]
        cats = json.loads((Path(__file__).parent / 'categorias.json').read_text())
        for first, second, expected in cases:
            local, votes = self.oscuro()
            by_model = {v['modelo']: v for v in votes}
            with self.subTest(first=first, second=second), \
                    patch.multiple(V, VERIFICADORES=list(by_model), CONSENSO_VLM_SOLO='confirma',
                                   ARBITRO_CONFIRMA=False, ARBITRO='arbitro'), \
                    patch.object(V, '_verificar_uno', side_effect=lambda m, *a: copy.deepcopy(by_model[m])), \
                    patch.object(V, '_segunda_mirada_presencia', side_effect=[first, second]) as review, \
                    patch.object(V, '_arbitrar', return_value={'ok': True, 'decisiones': [],
                                                              'descripcion': 'Contenedor y bolsas de basura.'}), \
                    patch.object(V, '_llamar', side_effect=AssertionError('Unexpected API request')):
                result = V.verificar(Image.new('RGB', (10, 10)), cats, local)
            keys = {c['key'] for c in result['confirmadas']}
            self.assertEqual(SECO in keys, expected)
            self.assertEqual(LATERAL in keys, not expected)
            self.assertIn('recoleccion', keys)
            self.assertEqual(result['segunda_mirada_secos']['promovio'], expected)
            self.assertEqual(review.call_count, 2 if second is not None else 1)
            self.assertEqual(result['verificadores'], votes)

    def test_relacion_retira_base_falsa_y_promueve_identidad_visual_sin_api(self):
        models = ['m1', 'm2', 'm3']
        cats = json.loads((Path(__file__).parent / 'categorias.json').read_text())
        votes = {m: dict(modelo=m, ok=True, sin_problema=False, foto_corresponde=None,
                        categorias_contexto=[], descripcion='Contenedor con base desplazada.',
                        categorias=[{'key': LATERAL, 'gravedad': 1, 'evidencia': 'contenedor lateral'},
                                    {'key': 'reparacion_contenedor', 'gravedad': 3,
                                     'evidencia': 'base metálica vacía y desplazada'}]) for m in models}
        local = {'predichas': [{'key': LATERAL, 'score': 1}, {'key': 'retiro_muebles', 'score': .999}],
                 'probabilidades': [{'key': LATERAL, 'score': 1}, {'key': 'retiro_muebles', 'score': .999}],
                 'gravedad': {'value': 3, 'raw': 3}}
        rows = [dict(modelo=m, objeto='somier', ubicacion='delante', rasgos='resortes y listones',
                     relacion='descarte_independiente', otro_dano_contenedor='no') for m in models]
        with patch.multiple(V, VERIFICADORES=models, CONSENSO_VLM_SOLO='confirma',
                            ARBITRO_CONFIRMA=False, ARBITRO='arbitro'), \
                patch.object(V, '_verificar_uno', side_effect=lambda m, *a: copy.deepcopy(votes[m])), \
                patch.object(V, '_segunda_mirada_relacion', return_value=(rows, False)), \
                patch.object(V, '_arbitrar', return_value={'ok': True, 'decisiones': [], 'descripcion': ''}), \
                patch.object(V, '_llamar', side_effect=AssertionError('Unexpected API request')) as api:
            result = V.verificar(Image.new('RGB', (10, 10)), cats, local)
        self.assertEqual({c['key'] for c in result['confirmadas']}, {LATERAL, 'retiro_muebles'})
        self.assertTrue(result['segunda_mirada_relacion']['retiro_votos'])
        self.assertFalse(api.called)
        for evidence in ('Base vacía y tapa rota', 'Base desplazada y cuerpo quemado',
                         'Riel de izado torcido en diagonal'):
            self.assertFalse(V._reparacion_solo_base({'key': 'reparacion_contenedor', 'evidencia': evidence}))

    def test_descarte_ajeno_no_sustituye_base_dano_real_o_cartones(self):
        rows = [dict(modelo=m, objeto='somier', ubicacion='delante', rasgos='resortes y listones',
                     relacion='descarte_independiente', otro_dano_contenedor='no') for m in ('m1', 'm2', 'm3')]
        self.assertEqual(V._descarte_ajeno_corrobado(rows, False), rows)
        self.assertFalse(V._descarte_ajeno_corrobado(rows, True))
        for field, value in [('relacion', 'base_municipal'), ('relacion', 'indeterminada'),
                             ('otro_dano_contenedor', 'si'), ('otro_dano_contenedor', 'indeterminado')]:
            bad = copy.deepcopy(rows);bad[0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(V._descarte_ajeno_corrobado(bad, False))
        for name in ('cajas de cartón', 'contenedor', 'carrito de carga', 'no es una cama'):
            bad = [dict(r, objeto=name) for r in rows]
            with self.subTest(name=name):
                self.assertFalse(V._descarte_ajeno_corrobado(bad, False))
        self.assertFalse(V._descarte_ajeno_corrobado([rows[0]] * 3, False))

    def test_contraste_quemado_exige_destruccion_corrobada_sin_negativas(self):
        review = {'dano': [{'modelo': m, 'evidencia': 'Contenedor quemado y destruido'}
                           for m in ('m1', 'm2')], 'sin_dano': [], 'fallo': False}
        self.assertTrue(V._cuerpo_quemado_destruido(review))
        for variant in ('solo_uno', 'duplicado', 'usable', 'fallo', 'intacto', 'negado'):
            r = copy.deepcopy(review)
            if variant == 'solo_uno': r['dano'].pop()
            elif variant == 'duplicado': r['dano'][1]['modelo'] = 'm1'
            elif variant == 'usable': r['sin_dano'] = [{'modelo': 'm3'}]
            elif variant == 'fallo': r['fallo'] = True
            else:
                for d in r['dano']:
                    d['evidencia'] = ('Marcas de quemadura, cuerpo intacto y lleno' if variant == 'intacto'
                                      else 'No está quemado ni destruido')
            with self.subTest(variant=variant):
                self.assertFalse(V._cuerpo_quemado_destruido(r))

    def test_vaciado_por_tapa_no_incluye_llenado_visible(self):
        def votes(*texts):
            return [{'categorias': [{'key': 'vaciado_contenedor', 'evidencia': t}]} for t in texts]
        self.assertTrue(V._vaciado_solo_por_tapa(votes('Tapa abierta y residuos visibles')))
        for texts in [(), ('Interior lleno hasta el borde',),
                      ('Tapa abierta porque está lleno',),
                      ('Tapa abierta', 'Residuos colmando la capacidad')]:
            self.assertFalse(V._vaciado_solo_por_tapa(votes(*texts)))

    def test_activa_solo_con_respaldo_local_fuerte_y_votos_separados(self):
        self.assertTrue(V._contrastar_con_secos(LATERAL, LOCAL, VOTOS))
        for seco, humedo in [(0.94, 0), (0.99, 0.051), (0.96, 0.04),
                             (None, 0.01), (1, None), (float('nan'), 0)]:
            local = {'probabilidades': [{'key': SECO, 'score': seco},
                                       {'key': LATERAL, 'score': humedo}]}
            with self.subTest(seco=seco, humedo=humedo):
                self.assertFalse(V._contrastar_con_secos(LATERAL, local, VOTOS))
        self.assertFalse(V._contrastar_con_secos(SECO, LOCAL, VOTOS))
        self.assertFalse(V._contrastar_con_secos(LATERAL, {}, VOTOS))

    def test_conserva_escenas_con_dos_tipos_y_mayoria_humedos(self):
        for votos in [VOTOS[:2], [VOTOS[1]],
                      [dict(VOTOS[0], ok=False), *VOTOS[1:]]]:
            self.assertFalse(V._contrastar_con_secos(LATERAL, LOCAL, votos))
        ambos = copy.deepcopy(VOTOS)
        ambos[1]['categorias'].append({'key': SECO})
        self.assertFalse(V._contrastar_con_secos(LATERAL, LOCAL, ambos))
        mayoria = copy.deepcopy(VOTOS)
        mayoria[0]['categorias'][0]['key'] = LATERAL
        self.assertFalse(V._contrastar_con_secos(LATERAL, LOCAL, mayoria))
        self.assertFalse(V._contrastar_con_secos(LATERAL, LOCAL, [VOTOS[0]] * 2 + [VOTOS[1]]))

    def correr(self, respuestas, fallo=False, contraste=True):
        votos = {v['modelo']: v for v in VOTOS}
        cats = json.loads((Path(__file__).parent / 'categorias.json').read_text())
        arbitro = {'ok': True, 'decisiones': [], 'descripcion': DESCRIPCION}
        with patch.multiple(V, VERIFICADORES=list(votos), REPREGUNTA_OBJETOS=True,
                            REPREGUNTA_MAX=2, CONSENSO_VLM_SOLO='confirma',
                            ARBITRO_CONFIRMA=False, ARBITRO='arbitro'), \
                patch.object(V, '_verificar_uno', side_effect=lambda m, *a: copy.deepcopy(votos[m])), \
                patch.object(V, '_arbitrar', return_value=arbitro), \
                patch.object(V, '_repregunta_objeto', return_value=(respuestas, fallo)) as pregunta, \
                patch.object(V, '_llamar', side_effect=AssertionError('Llamada inesperada')):
            if contraste:
                resultado = V.verificar(Image.new('RGB', (10, 10)), cats, copy.deepcopy(LOCAL))
            else:
                with patch.object(V, '_contrastar_con_secos', return_value=False):
                    resultado = V.verificar(Image.new('RGB', (10, 10)), cats, copy.deepcopy(LOCAL))
        return resultado, pregunta

    @staticmethod
    def respuestas(*estados):
        return [{'modelo': m, 'veredicto': estado, 'evidencia': 'observación dirigida',
                 'ubicacion': 'gris al fondo; verde en primer plano' if estado == 'presente' else '',
                 'estado': None} for m, estado in zip(['m1', 'm3'], estados)]

    def test_rechazo_dirigido_retira_tipo_y_descripcion_sin_tocar_recoleccion(self):
        r, pregunta = self.correr(self.respuestas('ausente', 'ausente'))
        self.assertIn('físicamente distinto', pregunta.call_args.args[1])
        self.assertEqual(pregunta.call_count, 1)
        self.assertEqual({c['key'] for c in r['confirmadas']}, {SECO, 'recoleccion'})
        reco = next(c for c in r['confirmadas'] if c['key'] == 'recoleccion')
        self.assertEqual(reco['gravedad'], 3)
        self.assertEqual(len(reco['fuentes']), 4)
        self.assertNotIn('contenedor lateral', r['descripcion'])
        self.assertNotIn('ambos visibles', r['descripcion'])
        self.assertIn('contenedor verde', r['descripcion'])
        self.assertIn('bolsas de basura', r['descripcion'])
        self.assertTrue(r['segunda_mirada_presencia_clave'][LATERAL]['retiro_votos'])

    def test_dos_contenedores_identificados_se_conservan(self):
        r, _ = self.correr(self.respuestas('presente', 'presente'))
        self.assertIn(LATERAL, {c['key'] for c in r['confirmadas']})
        self.assertIn(SECO, {c['key'] for c in r['confirmadas']})
        self.assertFalse(r['segunda_mirada_presencia_clave'])

    def test_empate_abstencion_y_error_no_confirman_ni_inventan_ausencia(self):
        for estados, fallo in [(('ausente', 'presente'), False),
                               (('ausente', 'no_se_distingue'), False),
                               (('no_se_distingue', 'no_se_distingue'), False),
                               ((), True)]:
            with self.subTest(estados=estados):
                r, _ = self.correr(self.respuestas(*estados), fallo)
                self.assertNotIn(LATERAL, {c['key'] for c in r['confirmadas']})
                self.assertFalse(r['segunda_mirada_presencia_clave'])

    def test_fuera_del_contraste_no_cambia_la_pregunta(self):
        r, pregunta = self.correr(self.respuestas('presente', 'presente'), contraste=False)
        self.assertNotIn('físicamente distinto', pregunta.call_args.args[1])
        self.assertNotIn('contraste_secos', r['repreguntas'][0])


if __name__ == '__main__':
    unittest.main()
