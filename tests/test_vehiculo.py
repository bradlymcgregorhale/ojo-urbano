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

    def test_sin_lectores(self):
        with mock.patch.object(V, "modelos_activos", return_value=[]):
            with self.assertRaises(ValueError):
                Ve.escena([Image.new("RGB", (8, 8))])


if __name__ == "__main__":
    unittest.main()
