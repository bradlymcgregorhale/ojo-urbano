"""La recuperación no acepta fotos distintas ni habilita gasto."""
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tools.servidor_consolidada import crear_servidor


class RecuperacionTests(unittest.TestCase):
    def test_huella_origen_y_bloqueo_de_analisis(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            foto = raiz / 'foto.jpg'
            foto.write_bytes(b'foto sintetica')
            huella = hashlib.sha256(foto.read_bytes()).hexdigest()
            (raiz / 'datos.json').write_text(json.dumps({'conjunto': 'prueba', 'fotos': [
                {'id': 'V0001', 'sha256': huella, 'archivo': 'foto.jpg'}]}))
            revision = {'conjunto': 'prueba', 'revisiones': {'V0001': {'sha256': huella}},
                        'propuestas': {'V0001': {'foto': 'V0001', 'resultado': {'sin_gasto': True}}}}
            (raiz / 'revision_original.json').write_text(json.dumps(revision))
            server = crear_servidor(raiz, 0)
            hilo = threading.Thread(target=server.serve_forever, daemon=True)
            hilo.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urllib.request.urlopen(base + '/api/estado') as r:
                    estado = json.load(r)
                self.assertFalse(estado['autorizado'])
                intento = estado['intentos'][0]['id']
                with urllib.request.urlopen(base + '/api/intento/' + intento) as r:
                    self.assertEqual(json.load(r), revision['propuestas']['V0001'])
                for request in [urllib.request.Request(base + '/api/analizar', data=b'{}'),
                                urllib.request.Request(base + '/foto.jpg', headers={'Origin': 'https://otro.example'})]:
                    with self.assertRaises(urllib.error.HTTPError) as e:
                        urllib.request.urlopen(request)
                    self.assertEqual(e.exception.code, 403)
                    e.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                hilo.join()
            foto.write_bytes(b'foto cambiada')
            with self.assertRaises(ValueError):
                crear_servidor(raiz, 0)
            foto.unlink()
            with self.assertRaises(FileNotFoundError):
                crear_servidor(raiz, 0)
