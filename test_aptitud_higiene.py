"""Los ejes de una foto y sus fallos conservan decisiones independientes."""
import copy
import json
import unittest
from unittest.mock import patch

import evaluacion_foto
import modos_analisis
import prioridad
import verificador as V


class AptitudHigieneTests(unittest.TestCase):
    def lectura(self, **cambios):
        return dict(calidad_suficiente=True, motivo_calidad=None,
                    evidencia_calidad='Se ven las ramas y sus hojas.',
                    ambito='publica', evidencia_ambito='Vereda junto al cordón.',
                    **cambios)

    def test_respuestas_invalidas_no_suman_votos(self):
        buena = self.lectura()
        for cambios in ({'calidad_suficiente': 'false'}, {'ambito': 'privado'},
                        {'calidad_suficiente': False, 'motivo_calidad': 'inventado'},
                        {'evidencia_ambito': ''}, {'evidencia_calidad': None}):
            with self.subTest(cambios=cambios), patch.object(V, 'VERIFICADORES', ['a', 'b']), \
                    patch.object(V, '_llamar', side_effect=lambda m, *a, **kw:
                        json.dumps(buena if m == 'a' else dict(buena, **cambios))):
                self.assertEqual(set(V._pregunta_aptitud('foto')), {'a'})

    def test_calidad_y_ambito_no_reemplazan_encuadre_ni_categorias(self):
        anterior = {'ambito': 'publica', 'calidad_suficiente': True,
                    'motivos_calidad': [], 'contexto_suficiente': False,
                    'motivos_contexto': ['entorno_no_visible']}
        fuente = {'modelo': 'a', 'ok': True, 'evaluacion_foto': anterior,
                  'categorias': [{'key': 'retiro_poda'}]}
        lectura = {'calidad_suficiente': True, 'motivos_calidad': [],
                   'ambito': 'indeterminado', 'contexto_suficiente': True}
        V._aplicar_aptitud([fuente], {'a': lectura})
        self.assertFalse(fuente['evaluacion_foto']['contexto_suficiente'])
        self.assertEqual(fuente['evaluacion_foto']['ambito'], 'indeterminado')
        self.assertEqual(fuente['categorias'], [{'key': 'retiro_poda'}])
        self.assertEqual(anterior['ambito'], 'publica')

    def test_fallo_conserva_lectura_y_marca_resultado_parcial(self):
        v = {'modelo': 'a', 'ok': True, 'evaluacion_foto': {'ambito': 'publica'}}
        antes = copy.deepcopy(v)
        V._aplicar_aptitud([v], {})
        self.assertEqual(v['evaluacion_foto'], antes['evaluacion_foto'])
        self.assertTrue(modos_analisis._fallos_en(v))

    def test_no_preselecciona_servicios_con_objecion_de_calidad_o_ambito(self):
        publica = {'problemas': [{'key': 'retiro_poda'}],
                   'evaluacion_foto': {'requiere_revision': True},
                   'problema_principal': {'key': 'retiro_poda', 'estado': 'seleccionado'}}
        propuesta = prioridad.proponer_solicitudes(publica)
        self.assertEqual(propuesta['servicios'], [])
        self.assertEqual(propuesta['estado'], 'requiere_contexto')

    def test_dos_calidades_insuficientes_rechazan_sin_volver_privado(self):
        vs = [{'modelo': m, 'ok': True, 'evaluacion_foto': {
            'ambito': 'publica', 'calidad_suficiente': m == 'c',
            'motivos_calidad': [] if m == 'c' else ['desenfoque'],
            'contexto_suficiente': True, 'motivos_contexto': []}} for m in 'abc']
        resultado, contexto = evaluacion_foto.resumir(vs)
        self.assertTrue(resultado['rechazada'])
        self.assertEqual(resultado['ambito'], 'publica')
        self.assertTrue(contexto['suficiente'])


if __name__ == '__main__':
    unittest.main()
