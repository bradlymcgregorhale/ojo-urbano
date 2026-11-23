import copy
import unittest

import contexto_ocupacion


PEDIDO = "uso indebido del espacio público"
MUEBLES = {"problemas": [{"key": "retiro_muebles", "fuentes": 3}],
           "categorias_contexto": [{"codigo": "118001", "respaldo_visual": "compatible", "fuentes": 1}],
           "foto_valida": True}


class ContextoOcupacionTest(unittest.TestCase):
    def test_foto_compatible_con_el_pedido_aunque_la_lectura_visual_diga_retiro(self):
        resultado = copy.deepcopy(MUEBLES)
        contexto_ocupacion.aplicar(resultado, PEDIDO)
        self.assertEqual(resultado["reclamo_foto"]["estado"], "compatible")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], ["ocupacion_comercial"])
        self.assertTrue(resultado["solicitudes_sugeridas"]["requiere_confirmacion"])
        self.assertEqual(resultado["problemas"], MUEBLES["problemas"])

    def test_muebles_mas_pedido_generico_alcanzan_para_proponer_ocupacion(self):
        resultado = {"problemas": MUEBLES["problemas"]}
        contexto_ocupacion.aplicar(resultado, "ocupacion de espacio publico")
        self.assertEqual(resultado["reclamo_foto"]["estado"], "compatible")

    def test_contenedor_lleno_con_el_mismo_texto_pide_otra_foto(self):
        resultado = {"problemas": [{"key": "contenedor_desbordado"}],
                     "categorias_contexto": MUEBLES["categorias_contexto"]}
        contexto_ocupacion.aplicar(resultado, "ocupacion de espacio pubilco")
        self.assertEqual(resultado["reclamo_foto"]["estado"], "no_corresponde")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], [])
        self.assertTrue(resultado["conclusion"]["requiere_revision"])

    def test_sin_pedido_y_pedidos_distintos_conservan_su_contrato(self):
        for texto in ("", "retiro de muebles", "no es ocupación del espacio público",
                      "ocupación del espacio público moto sin dominio",
                      "ocupación del espacio público de un local gastronómico"):
            with self.subTest(texto=texto):
                resultado = copy.deepcopy(MUEBLES)
                contexto_ocupacion.aplicar(resultado, texto)
                self.assertEqual(resultado, MUEBLES)

    def test_falta_de_evidencia_se_distingue_de_foto_distinta(self):
        resultado = {}
        contexto_ocupacion.aplicar(resultado, PEDIDO)
        self.assertEqual(resultado["reclamo_foto"]["estado"], "sin_evidencia")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], [])
        self.assertTrue(resultado["hay_reclamo"])


if __name__ == "__main__":
    unittest.main()
