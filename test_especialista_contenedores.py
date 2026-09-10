"""Contrato del inventario, fallos y publicacion con las otras incidencias."""
import copy
import io
import json
import sys
import unittest
from unittest.mock import patch
from PIL import Image
import especialista_contenedores as E
import verificador as V


def respuesta(tipos=None, uncertain=False):
    return {'model': E.MODELO, 'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'types': tipos or [], 'uncertain': uncertain, 'evidence': 'Visible'})}}],
        'usage': {'cost': .004}}


class Inventario(unittest.TestCase):
    def test_inventarios_y_incertidumbre(self):
        for tipos in ([], [E.TIPOS[0]], [E.TIPOS[0], E.TIPOS[2]]):
            self.assertEqual(E.interpretar(respuesta(tipos))['tipos'], tipos)
        self.assertEqual(E.interpretar(respuesta(uncertain=True)), E.revision())

    def test_invalidos(self):
        for data in (None, {}, {'model': 'wrong'}, respuesta(['wrong']), respuesta([E.TIPOS[0]]*2)):
            self.assertEqual(E.interpretar(data), E.revision(fallo=True))
        for field,value in [('finish_reason','length')]:
            data=respuesta();data['choices'][0][field]=value
            self.assertTrue(E.interpretar(data)['fallo'])
        data=respuesta();data['choices'][0]['message']['content']='{"types":[],"types":[],"uncertain":false,"evidence":""}'
        self.assertTrue(E.interpretar(data)['fallo'])

    def test_otra_incidencia_y_foto_no_corresponde(self):
        original={'problemas':[{'key':'retiro_escombros','nombre':'Escombros'},{'key':'retiro_muebles','nombre':'Muebles'}],'foto_valida':False,
                  'elementos_detectados':[{'key':E.TIPOS[1]}],
                  'posibles':[{'key':E.TIPOS[1]},{'key':'retiro_muebles'}],
                  'en_duda':[E.TIPOS[1],'barrido'],
                  'descripcion':'Hay bolsas y un contenedor de húmedos, carga lateral junto a muebles.'}
        before=copy.deepcopy(original)
        out=E.aplicar(original,E.interpretar(respuesta([E.TIPOS[0]])),{k:{'nombre':k} for k in E.TIPOS})
        self.assertEqual(out['problemas'],before['problemas']);self.assertEqual(original,before)
        self.assertEqual([x['key'] for x in out['elementos_detectados']],[E.TIPOS[0]])
        self.assertEqual(out['en_duda'],['barrido']);self.assertEqual(out['posibles'],[{'key':'retiro_muebles'}])
        self.assertIn('Escombros',out['descripcion']);self.assertIn('Muebles',out['descripcion']);self.assertNotIn('lateral',out['descripcion'])
        out=E.aplicar(original,E.revision(),{k:{'nombre':k} for k in E.TIPOS})
        self.assertIsNone(out['contenedores']['tipos']);self.assertEqual(out['elementos_detectados'],[])

    def test_descripciones_contradictorias(self):
        categorias={k:{'nombre':k} for k in E.TIPOS}
        for texto in ['No se observa ningún contenedor en la imagen. Hay bolsas de basura en la vereda.',
                      'Hay un contenedor verde para residuos secos y escombros.']:
            base={'problemas':[{'key':'retiro_escombros','nombre':'Escombros'}], 'descripcion':texto}
            out=E.aplicar(base,E.interpretar(respuesta([E.TIPOS[2]])),categorias)
            self.assertNotIn('ningún contenedor',out['descripcion']);self.assertNotIn('residuos secos',out['descripcion'])
            self.assertIn('Escombros',out['descripcion']);self.assertIn(E.TIPOS[2],out['descripcion'])
            for result in [E.revision(),E.interpretar(respuesta())]:
                out=E.aplicar(base,result,categorias)
                self.assertNotIn('Hay un contenedor',out['descripcion'])
        base={'problemas':[{'key':'reparacion_contenedor','nombre':'Reparacion'}]}
        out=E.aplicar(base,E.interpretar(respuesta()),categorias)
        self.assertEqual(out['contenedores']['estado'],'revision');self.assertIsNone(out['contenedores']['tipos'])
        self.assertEqual(out['problemas'],base['problemas'])

    def test_http_un_intento_y_costo(self):
        b=io.BytesIO();Image.new('RGB',(20,20)).save(b,format='JPEG')
        payload={'model':E.MODELO,'messages':[],'reasoning':{'effort':'medium'},'max_tokens':3000}
        with patch.object(E,'solicitud',return_value=payload),patch.object(V,'_pedir_http',return_value=respuesta([E.TIPOS[2]])) as http:
            V.costo_reset();result=V.verificar_contenedores(b.getvalue());self.assertEqual(result['tipos'],[E.TIPOS[2]])
            self.assertEqual(V.costo_total(),.004);self.assertEqual(http.call_count,1)
            self.assertEqual(json.loads(http.call_args.args[0].data),payload)
        with patch.object(E,'solicitud',return_value=payload),patch.object(V,'_pedir_http',side_effect=TimeoutError) as http:
            self.assertTrue(V.verificar_contenedores(b.getvalue())['fallo']);self.assertEqual(http.call_count,1)

    @unittest.skipUnless('servidor' in sys.modules,'Ejecutar mediante pruebas.py para usar el servidor sin pesos')
    def test_procesar_publica_y_cache(self):
        import servidor as S
        b=io.BytesIO();Image.new('RGB',(20,20)).save(b,format='JPEG')
        local={'predichas':[],'probabilidades':[],'gravedad':{}}
        veri={'activa':True,'confirmadas':[{'key':'retiro_muebles','nombre':'Muebles','gravedad':2,'fuentes':['v1','v2']}],
              'en_duda':[],'categorias_contexto':[],'descripcion':'Hay muebles.', 'verificadores':[]}
        for result in [E.interpretar(respuesta([E.TIPOS[0],E.TIPOS[2]])),E.interpretar(respuesta()),E.revision(),E.revision(fallo=True)]:
            with patch.object(S,'CONTENEDORES_ESPECIALISTA',True),patch.object(V,'disponible',return_value=True),patch.object(S,'_hay_cuota',return_value=True),patch.object(S,'clasificar_local',return_value=local),patch.object(V,'verificar',return_value=copy.deepcopy(veri)),patch.object(V,'verificar_contenedores',return_value=result),patch.object(S,'_calidad_segura',return_value=None):
                internal=S.procesar(b.getvalue(),'','auto');public=S._publica(internal)
                self.assertEqual(public['contenedores']['tipos'],result['tipos'])
                self.assertEqual(public['problemas'][0]['key'],'retiro_muebles')
                self.assertEqual(public['descripcion'],'Hay muebles.')
                self.assertEqual(S._cacheable(internal),not result['fallo'])
        with patch.object(S,'CONTENEDORES_ESPECIALISTA',True),patch.object(V,'disponible',return_value=True),patch.object(S,'clasificar_local',return_value=local),patch.object(V,'verificar_contenedores') as request,patch.object(S,'_calidad_segura',return_value=None):
            internal=S.procesar(b.getvalue(),'','0');self.assertNotIn('contenedores',S._publica(internal));request.assert_not_called()
