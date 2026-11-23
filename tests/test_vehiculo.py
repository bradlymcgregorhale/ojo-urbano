"""Veredicto de escena de vehículo (#216): consenso campo por campo, sin red."""
import json
import unittest
from unittest import mock

from PIL import Image

import comparador
import vehiculo as Ve
import verificador as V


def _respuesta(**campos):
    base = {"vehiculos_protagonistas": 1, "tipo": "moto", "color": "negro", "mismo_vehiculo": "si",
            "infraccion": "vereda", "abandono": {"senales": ["cadena_candado"], "nivel": "bajo"},
            "personas_visibles": False, "conductor_adentro": False, "motivo": "moto sobre la vereda"}
    base.update(campos)
    return json.dumps(base)


class NormalizarTests(unittest.TestCase):
    def test_valores_fuera_de_catalogo_quedan_en_none(self):
        n = Ve._normalizar(json.loads(_respuesta(tipo="bicicleta", infraccion="volando",
                                                 abandono={"senales": ["cadena_candado", "rara"], "nivel": "x"},
                                                 vehiculos_protagonistas="muchos")), dos_fotos=True)
        self.assertIsNone(n["tipo"])
        self.assertIsNone(n["infraccion"])
        self.assertIsNone(n["abandono_nivel"])
        self.assertEqual(n["abandono_senales"], ["cadena_candado"])
        self.assertIsNone(n["vehiculos_protagonistas"])
        self.assertEqual(n["mismo_vehiculo"], "si")

    def test_mismo_vehiculo_no_aplica_con_una_foto(self):
        n = Ve._normalizar(json.loads(_respuesta()), dos_fotos=False)
        self.assertIsNone(n["mismo_vehiculo"])

    def test_identificacion_ausente_o_ilegible(self):
        for valor in (None, 150, "No identificado", "¿RX 150?", "N/A"):
            with self.subTest(valor=valor):
                n = Ve._normalizar(json.loads(_respuesta(marca=valor, modelo=valor)), dos_fotos=False)
                self.assertIsNone(n["marca"])
                self.assertIsNone(n["modelo"])


class ConsensoTests(unittest.TestCase):
    def test_dos_de_tres_publican_y_el_resto_queda_en_duda(self):
        lecturas = [Ve._normalizar(json.loads(_respuesta()), True),
                    Ve._normalizar(json.loads(_respuesta(infraccion="ochava", abandono={"senales": ["oxido", "cadena_candado"], "nivel": "bajo"})), True),
                    Ve._normalizar(json.loads(_respuesta(tipo="auto", personas_visibles=True)), True)]
        escena, en_duda = Ve._consenso(lecturas)
        self.assertEqual(escena["tipo"], "moto")
        self.assertEqual(escena["infraccion"], "vereda")
        self.assertEqual(escena["abandono_senales"], ["cadena_candado"])
        self.assertEqual(escena["abandono_nivel"], "bajo")
        self.assertFalse(escena["personas_visibles"])
        self.assertEqual(en_duda["abandono_senales"], ["oxido"])
        self.assertNotIn("tipo", en_duda)

    def test_un_solo_lector_no_publica_nada(self):
        escena, en_duda = Ve._consenso([Ve._normalizar(json.loads(_respuesta()), True)])
        self.assertIsNone(escena["tipo"])
        self.assertEqual(en_duda["tipo"], ["moto"])
        self.assertEqual(escena["abandono_senales"], [])


class EscenaTests(unittest.TestCase):
    def _escena(self, respuestas, fotos=2, misma="si"):
        llamadas = []

        def fake(modelo, mensajes, etapa="", **k):
            llamadas.append((modelo, etapa))
            r = respuestas[modelo]
            if r == "falla":
                raise ValueError("caído")
            return r
        imgs = [Image.new("RGB", (64, 64), "white") for _ in range(fotos)]
        with mock.patch.object(V, "_llamar", side_effect=fake), \
             mock.patch.object(comparador, "comparar", return_value={"misma": misma}), \
             mock.patch.object(V, "modelos_activos", return_value=list(respuestas)):
            return Ve.escena(imgs, "moto sobre la vereda"), llamadas

    def test_tres_lectores_consenso_y_misma_escena(self):
        escena, llamadas = self._escena({"a/1": _respuesta(), "b/2": _respuesta(), "c/3": _respuesta(tipo="auto")})
        self.assertEqual(escena["tipo"], "moto")
        self.assertEqual(escena["abandono"], {"senales": ["cadena_candado"], "nivel": "bajo"})
        self.assertEqual(escena["misma_escena"], "si")
        self.assertEqual(escena["fuentes"], 3)
        self.assertEqual(len(escena["revisiones"]), 3)
        self.assertEqual({e for _, e in llamadas}, {"escena_vehiculo"})

    def test_un_lector_caido_no_cancela_a_los_demas(self):
        escena, _ = self._escena({"a/1": _respuesta(), "b/2": "falla", "c/3": _respuesta()})
        self.assertEqual(escena["fuentes"], 2)
        self.assertEqual(escena["tipo"], "moto")
        self.assertFalse(escena["revisiones"][1]["ok"])

    def test_una_foto_no_compara(self):
        escena, _ = self._escena({"a/1": _respuesta(), "b/2": _respuesta()}, fotos=1)
        self.assertIsNone(escena["misma_escena"])
        self.assertIsNone(escena["mismo_vehiculo"])

    def test_marca_y_modelo_coinciden_aunque_varie_el_formato(self):
        escena, llamadas = self._escena({
            "a/1": _respuesta(marca="Zanella", modelo="RX 150"),
            "b/2": _respuesta(marca="zanella", modelo="RX-150"),
            "c/3": _respuesta(marca="ZANELLA", modelo=None),
        }, fotos=1)
        self.assertEqual((escena["marca"], escena["modelo"], escena["color"]), ("Zanella", "RX 150", "negro"))
        self.assertEqual(len(llamadas), 3)

    def test_marca_legible_con_modelo_en_duda(self):
        escena, _ = self._escena({
            "a/1": _respuesta(marca="Zanella", modelo="RX 150"),
            "b/2": _respuesta(marca="Zanella", modelo=None),
            "c/3": _respuesta(marca=None, modelo="RX 125"),
        }, fotos=1)
        self.assertEqual(escena["marca"], "Zanella")
        self.assertIsNone(escena["modelo"])
        self.assertEqual(escena["en_duda"]["modelo"], ["RX 125", "RX 150"])

    def test_las_tildes_no_dividen_el_voto_de_la_marca(self):
        escena, _ = self._escena({
            "a/1": _respuesta(marca="Citroën"),
            "b/2": _respuesta(marca="CITROEN"),
        }, fotos=1)
        self.assertEqual(escena["marca"], "Citroën")

    def test_dos_lecturas_empatadas_no_publican_modelo(self):
        escena, _ = self._escena({
            lector: _respuesta(marca="Zanella", modelo=modelo)
            for lector, modelo in zip(("a/1", "b/2", "c/3", "d/4"), ("RX 150", "RX 150", "RX 125", "RX 125"))
        })
        self.assertEqual(escena["marca"], "Zanella")
        self.assertIsNone(escena["modelo"])

    def test_identidad_requiere_un_mismo_vehiculo_protagonista(self):
        for datos in ({"vehiculos_protagonistas": 2}, {"mismo_vehiculo": "no"}, {"mismo_vehiculo": "duda"}):
            with self.subTest(datos=datos):
                respuesta = _respuesta(marca="Zanella", modelo="RX 150", **datos)
                escena, _ = self._escena({"a/1": respuesta, "b/2": respuesta})
                self.assertIsNone(escena["marca"])
                self.assertIsNone(escena["modelo"])
                self.assertEqual(escena["color"], "negro")

    def test_lector_que_ve_otro_vehiculo_no_aporta_a_su_identidad(self):
        escena, _ = self._escena({
            "a/1": _respuesta(marca="Zanella", modelo="RX 150"),
            "b/2": _respuesta(marca="Zanella"),
            "c/3": _respuesta(mismo_vehiculo="no", marca="Zanella", modelo="RX 150"),
        })
        self.assertEqual(escena["mismo_vehiculo"], "si")
        self.assertEqual(escena["marca"], "Zanella")
        self.assertIsNone(escena["modelo"])

    def test_sin_lectores(self):
        with mock.patch.object(V, "modelos_activos", return_value=[]):
            with self.assertRaises(ValueError):
                Ve.escena([Image.new("RGB", (8, 8))])


if __name__ == "__main__":
    unittest.main()
