"""Integridad y regresiones con datos sintéticos; sin modelos ni red (#51)."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import regresiones as R


class RegistroTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'lote'
        self.a = self.base / 'analisis'
        self.chat = self.a / 'correcciones-conversacion'
        self.chat.mkdir(parents=True)
        (self.base / 'entrega').mkdir()
        self.reg = self.root / 'banco'
        self.foto = {'foto': 'H0001', 'sha256': 'a' * 64, 'sha256_api': 'b' * 64}
        self.write(self.base / 'entrega/manifest.json', {'fotos': [self.foto]})
        self.write(self.base / 'manifest-privado.json', {'fotos': [dict(self.foto, particion='desarrollo')]})
        self.original = {'foto': 'H0001', 'modo': 'alto',
            'entrada_api': {'sha256': 'b' * 64, 'original_sha256': 'a' * 64},
            'resultado': {'modo': 'alto', 'modo_version': 'estable', 'analisis_estado': 'completo',
                          'hay_reclamo': True, 'problemas': [{'key': 'retiro_poda'}],
                          'posibles': [], 'elementos_detectados': []}}
        self.write(self.a / 'H0001-alto.json', self.original)
        self.revision = {'foto': 'H0001', 'original_huella': R.huella((self.a / 'H0001-alto.json').read_bytes()),
                         'correcciones': {'retiro_poda': 'si'}}
        self.write(self.chat / 'H0001.json', self.revision)

    def write(self, p, r):
        p.write_text(json.dumps(r))

    def crear(self, revisiones=()):
        return R.crear(self.base, self.reg, revisiones)

    def export(self, **changes):
        identity = [{k: self.foto[k] for k in ('foto', 'sha256', 'sha256_api')}]
        conjunto = R.huella(json.dumps(identity, sort_keys=True).encode())
        r = {'original_huella': self.revision['original_huella'], 'estado': 'corregido',
             'categorias': {'retiro_poda': 'confirmado'}, 'materiales': {},
             'ambito': 'via_publica', 'decision': 'aceptar'}
        r.update(changes)
        f = self.root / 'export.json'
        self.write(f, {'version': 2, 'conjunto': conjunto, 'revisiones': {'H0001': r}})
        return f

    def test_original_y_fuente_inmutables_deduplicados(self):
        p = self.crear()
        n = len(list((self.reg / 'objetos').iterdir()))
        self.assertEqual(p, self.crear())
        self.assertEqual(n, len(list((self.reg / 'objetos').iterdir())))
        self.assertEqual(R.obtener(self.reg, R.cargar(p)['casos'][0]['original']), self.original)

    def test_regresion_contra_etiqueta_no_solo_diferencia(self):
        p = self.crear()
        nueva = copy.deepcopy(self.original)
        nueva['resultado']['problemas'] = []
        self.write(self.a / 'H0001-alto.json', nueva)
        r = R.comparar(p, self.a)
        self.assertEqual(r['conteos']['regresion'], 1)
        self.assertFalse(r['sin_regresiones_observadas'])

    def test_error_previo_no_se_presenta_como_regresion(self):
        self.revision['correcciones']['retiro_poda'] = 'no'
        self.write(self.chat / 'H0001.json', self.revision)
        r = R.comparar(self.crear())
        self.assertEqual(r['conteos']['error_persistente'], 1)
        self.assertEqual(r['conteos']['regresion'], 0)
        self.assertFalse(r['aprobacion_de_despliegue'])

    def test_correccion_se_mide_sin_perder_original(self):
        self.revision['correcciones']['retiro_poda'] = 'no'
        self.write(self.chat / 'H0001.json', self.revision)
        p = self.crear()
        nueva = copy.deepcopy(self.original)
        nueva['resultado']['problemas'] = []
        self.write(self.a / 'H0001-alto.json', nueva)
        self.assertEqual(R.comparar(p, self.a)['conteos']['corregido'], 1)

    def test_duda_y_no_revisado_no_son_negativos(self):
        self.revision['correcciones'] = {'retiro_poda': 'duda', 'recoleccion': 'sin_revisar'}
        self.write(self.chat / 'H0001.json', self.revision)
        r = R.comparar(self.crear())
        self.assertEqual(r['filas'], [])
        self.assertFalse(r['sin_regresiones_observadas'])

    def test_categorias_parciales_formato_anterior(self):
        self.revision['categorias'] = self.revision.pop('correcciones')
        self.write(self.chat / 'H0001.json', self.revision)
        self.assertEqual(len(R.comparar(self.crear())['filas']), 1)

    def test_fuente_alterada_falla(self):
        p = self.crear()
        source = R.cargar(p)['fuentes'][0]
        (self.reg / 'objetos' / (source + '.json')).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'alterado'):
            R.comparar(p)

    def test_registro_alterado_falla(self):
        p = self.crear()
        p.write_text(p.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'huella'):
            R.comparar(p)

    def test_huella_humana_obsoleta_falla(self):
        self.revision['original_huella'] = 'c' * 64
        self.write(self.chat / 'H0001.json', self.revision)
        with self.assertRaisesRegex(ValueError, 'original'):
            self.crear()

    def test_candidata_ausente_no_aprueba(self):
        p = self.crear()
        r = R.comparar(p, self.root / 'ausente')
        self.assertEqual(len(r['faltantes']), 1)
        self.assertFalse(r['sin_regresiones_observadas'])

    def test_candidata_otra_foto_o_modo_no_aprueba(self):
        p = self.crear()
        for campo, valor in [('modo', 'bajo'), ('foto', 'H9999')]:
            nueva = copy.deepcopy(self.original)
            nueva[campo] = valor
            self.write(self.a / 'H0001-alto.json', nueva)
            self.assertFalse(R.comparar(p, self.a)['sin_regresiones_observadas'])

    def test_otra_imagen_o_contexto_no_aprueba(self):
        p = self.crear()
        for change in ('imagen', 'contexto'):
            nueva = copy.deepcopy(self.original)
            if change == 'imagen':
                nueva['entrada_api']['sha256'] = 'c' * 64
            else:
                nueva['contexto'] = 'pista nueva'
            self.write(self.a / 'H0001-alto.json', nueva)
            self.assertFalse(R.comparar(p, self.a)['sin_regresiones_observadas'])

    def test_exportacion_contradictoria_no_se_resuelve_sola(self):
        p = self.crear([self.export(categorias={'retiro_poda': 'no'})])
        r = R.comparar(p)
        self.assertEqual(len(r['conflictos']), 1)
        self.assertFalse(r['sin_regresiones_observadas'])

    def test_exportacion_otro_conjunto_se_rechaza(self):
        f = self.export()
        r = R.leer(f)
        r['conjunto'] = 'otro'
        self.write(f, r)
        with self.assertRaisesRegex(ValueError, 'conjunto'):
            self.crear([f])

    def test_interior_no_etiqueta_categorias(self):
        (self.chat / 'H0001.json').unlink()
        p = self.crear([self.export(ambito='interior', decision='rechazar', exclusion='interior',
                                    categorias={'retiro_poda': 'sin_revisar'})])
        r = R.comparar(p)
        self.assertEqual([f['campo'] for f in r['filas']], ['interior_rechazado'])
        self.assertEqual(r['conteos']['error_persistente'], 1)

    def test_interior_no_admite_confirmaciones(self):
        with self.assertRaisesRegex(ValueError, 'Exclusión'):
            self.crear([self.export(ambito='interior', decision='rechazar', exclusion='interior')])

    def test_reservada_excluida_por_defecto(self):
        self.write(self.base / 'manifest-privado.json', {'fotos': [dict(self.foto, particion='evaluacion_reservada')]})
        p = self.crear()
        self.assertEqual(R.comparar(p)['filas'], [])
        self.assertEqual(len(R.comparar(p, particion='evaluacion_reservada')['filas']), 1)

    def test_observacion_sin_categoria_conserva_original(self):
        self.revision.pop('correcciones')
        self.revision['materiales'] = {'retiro_poda': 'si'}
        self.write(self.chat / 'H0001.json', self.revision)
        p = self.crear()
        self.assertEqual(len(R.cargar(p)['casos']), 1)
        self.assertEqual(R.comparar(p)['filas'], [])

    def test_duda_posterior_no_reutiliza_confirmacion_silenciosamente(self):
        otra = dict(self.revision, correcciones={'retiro_poda': 'duda'})
        self.write(self.chat / 'H0001-segunda.json', otra)
        self.assertTrue(R.comparar(self.crear())['conflictos'])

    def test_solo_errores_no_comprueba_preservacion(self):
        self.revision['correcciones']['retiro_poda'] = 'no'
        self.write(self.chat / 'H0001.json', self.revision)
        p = self.crear()
        with patch('sys.argv', ['regresiones.py', 'comparar', '--registro', str(p),
                               '--informe', str(self.root / 'informe')]):
            self.assertEqual(R.main(), 1)
        r = R.leer(self.root / 'informe/comparacion.json')
        self.assertFalse(r['proteccion_de_aciertos_comprobada'])

    def test_exportacion_borrador_no_agrega_etiquetas(self):
        (self.chat / 'H0001.json').unlink()
        r = R.comparar(self.crear([self.export(estado='borrador')]))
        self.assertEqual(r['filas'], [])

    def test_sin_verificacion_no_cuenta_como_negativo(self):
        p = self.crear()
        nueva = copy.deepcopy(self.original)
        nueva['resultado']['analisis_estado'] = 'sin_verificacion'
        self.write(self.a / 'H0001-alto.json', nueva)
        r = R.comparar(p, self.a)
        self.assertTrue(r['faltantes'])
        self.assertEqual(r['filas'], [])

    def candidata(self, resultado):
        nueva = copy.deepcopy(self.original)
        nueva['resultado'].update(resultado)
        nueva['resultado']['modo_version'] = 'candidata'
        destino = self.root / 'candidata'
        destino.mkdir(exist_ok=True)
        self.write(destino / 'H0001-alto.json', nueva)
        return destino

    def test_estados_invalidos_son_cobertura_faltante(self):
        p = self.crear()
        for estado in (None, 'error', 'en_cola', ''):
            with self.subTest(estado=estado):
                r = R.comparar(p, self.candidata({'analisis_estado': estado}))
                self.assertTrue(r['faltantes'])
                self.assertFalse(r['proteccion_de_aciertos_comprobada'])

    def test_parcial_no_inventa_negativos(self):
        self.original['resultado'].update(problemas=[], hay_reclamo=False)
        self.write(self.a / 'H0001-alto.json', self.original)
        self.revision.update(original_huella=R.huella((self.a / 'H0001-alto.json').read_bytes()),
                             correcciones={'retiro_poda': 'no'})
        self.write(self.chat / 'H0001.json', self.revision)
        r = R.comparar(self.crear(), self.candidata({'analisis_estado': 'parcial'}))
        self.assertEqual(r['filas'], [])
        self.assertEqual(r['faltantes'][0]['campo'], 'categorias.retiro_poda')
        self.assertFalse(r['proteccion_de_aciertos_comprobada'])

    def test_parcial_con_hallazgo_explicito_se_puntua(self):
        r = R.comparar(self.crear(), self.candidata({'analisis_estado': 'parcial'}))
        self.assertEqual(r['conteos']['acierto_conservado'], 1)
        self.assertTrue(r['proteccion_de_aciertos_comprobada'])

    def test_ausencia_en_referencia_parcial_no_es_acierto_previo(self):
        self.original['resultado'].update(analisis_estado='parcial', problemas=[], hay_reclamo=False)
        self.write(self.a / 'H0001-alto.json', self.original)
        self.revision.update(original_huella=R.huella((self.a / 'H0001-alto.json').read_bytes()),
                             correcciones={'retiro_poda': 'no'})
        self.write(self.chat / 'H0001.json', self.revision)
        r = R.comparar(self.crear(), self.candidata({'analisis_estado': 'completo'}))
        self.assertEqual(r['aciertos_previos_evaluados'], 0)
        self.assertTrue(r['faltantes'])

    def test_interior_exige_motivo_y_salida_vacia(self):
        vacia = dict(analisis_estado='completo', hay_reclamo=False, problemas=[],
                     posibles=[], elementos_detectados=[])
        self.assertIsNone(R.observado(vacia, 'interior_rechazado'))
        vacia['evaluacion_foto'] = {'ambito': 'publica', 'rechazada': False}
        self.assertFalse(R.observado(vacia, 'interior_rechazado'))
        vacia['evaluacion_foto'] = {'ambito': None, 'rechazada': True, 'estado': 'rechazada_calidad'}
        self.assertFalse(R.observado(vacia, 'interior_rechazado'))
        vacia['evaluacion_foto'] = {'rechazada': True}
        self.assertIsNone(R.observado(vacia, 'interior_rechazado'))
        vacia['evaluacion_foto'] = {'ambito': 'interior', 'rechazada': True}
        self.assertTrue(R.observado(vacia, 'interior_rechazado'))
        vacia['elementos_detectados'] = [{'key': 'contenedor_secos'}]
        self.assertFalse(R.observado(vacia, 'interior_rechazado'))

    def test_referencia_no_aprueba_candidata(self):
        r = R.comparar(self.crear())
        self.assertTrue(r['solo_referencia'])
        self.assertEqual(r['conteos']['acierto_conservado'], 1)
        self.assertFalse(r['proteccion_de_aciertos_comprobada'])

    def test_archivo_identico_y_version_igual_no_aprueban(self):
        p = self.crear()
        r = R.comparar(p, self.a)
        self.assertEqual(r['identicas'], ['H0001'])
        self.assertEqual(r['version_sin_cambio'], ['H0001'])
        self.assertFalse(r['proteccion_de_aciertos_comprobada'])
        nueva = copy.deepcopy(self.original)
        nueva['resultado']['descripcion'] = 'Otro texto, misma versión'
        self.write(self.a / 'H0001-alto.json', nueva)
        r = R.comparar(p, self.a)
        self.assertEqual(r['identicas'], [])
        self.assertEqual(r['version_sin_cambio'], ['H0001'])
        self.assertFalse(r['proteccion_de_aciertos_comprobada'])

    def test_nueva_version_con_acierto_real_aprueba_comparacion(self):
        r = R.comparar(self.crear(), self.candidata({}))
        self.assertTrue(r['proteccion_de_aciertos_comprobada'])
        self.assertFalse(r['aprobacion_de_despliegue'])
        self.assertEqual(r['versiones_referencia'], ['estable'])

    def test_categoria_desconocida_se_rechaza_en_ambas_fuentes(self):
        self.revision['correcciones'] = {'categoria_inexistente': 'no'}
        self.write(self.chat / 'H0001.json', self.revision)
        with self.assertRaisesRegex(ValueError, 'Categoría desconocida'):
            self.crear()
        (self.chat / 'H0001.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Categoría desconocida'):
            self.crear([self.export(categorias={'categoria_inexistente': 'no'})])

    def test_registro_previo_con_clave_desconocida_no_aprueba(self):
        self.revision['correcciones'] = {'categoria_inexistente': 'no'}
        self.write(self.chat / 'H0001.json', self.revision)
        with patch.object(R, 'CATEGORIAS', R.CATEGORIAS | {'categoria_inexistente'}):
            p = self.crear()
        with self.assertRaisesRegex(ValueError, 'Categoría desconocida en el registro'):
            R.comparar(p, self.candidata({}))


if __name__ == '__main__':
    unittest.main()
