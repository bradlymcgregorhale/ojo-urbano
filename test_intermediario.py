"""Comprueba el intermediario PHP real, con y sin reconstrucción de multipart."""
import email.parser
import email.policy
import http.server
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request


class IntermediarioTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('php'), 'La prueba del intermediario necesita PHP con cURL')
    def test_modos_en_cuerpo_crudo_y_reconstruido(self):
        recibidos = []

        class Backend(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + body)
                campos = [(p.get_param('name', header='content-disposition'), p.get_payload(decode=True))
                          for p in msg.iter_parts()]
                recibidos.append(campos)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'campos': [(k, v.decode()) for k, v in campos]}).encode())

            def log_message(self, *args):
                pass

        backend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        hilo = threading.Thread(target=backend.serve_forever, daemon=True)
        hilo.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                proxy = Path(tmp) / 'index.php'
                original = (Path(__file__).parent / 'deploy/ojourbano.php').read_text()
                self.assertEqual(original.count('http://127.0.0.1:8091'), 1)
                proxy.write_text(original.replace('http://127.0.0.1:8091',
                                                  'http://127.0.0.1:' + str(backend.server_port)))
                for lectura_php in (0, 1):
                    with self.subTest(lectura_php=lectura_php):
                        with socket.socket() as sock:
                            sock.bind(('127.0.0.1', 0))
                            puerto = sock.getsockname()[1]
                        proc = subprocess.Popen(['php', '-d', f'enable_post_data_reading={lectura_php}',
                                                 '-S', f'127.0.0.1:{puerto}', str(proxy)],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        try:
                            for _ in range(100):
                                try:
                                    with socket.create_connection(('127.0.0.1', puerto), timeout=.1):
                                        break
                                except OSError:
                                    if proc.poll() is not None:
                                        self.fail('El servidor PHP no inició')
                                    time.sleep(.02)
                            for ruta in ('clasificar', 'trabajos'):
                                for campos in ([('modo', 'bajo')], [('modo', 'medio')], [],
                                               [('modo', 'bajo'), ('modo', 'alto')],
                                               [('modo[]', 'bajo')], [('modo[clave]', 'medio')],
                                               [('modo', 'alto', 'modo.txt')]):
                                    partes = [('file', 'foto_de_prueba', 'foto.jpg')] + campos
                                    cuerpo = b''
                                    for parte in partes:
                                        cabecera = f'--separador\r\nContent-Disposition: form-data; name="{parte[0]}"'
                                        if len(parte) == 3:
                                            cabecera += f'; filename="{parte[2]}"'
                                        cuerpo += (cabecera + '\r\n\r\n' + parte[1] + '\r\n').encode()
                                    cuerpo += b'--separador--\r\n'
                                    antes = len(recibidos)
                                    req = urllib.request.Request(f'http://127.0.0.1:{puerto}/ojourbano/{ruta}/',
                                                                 data=cuerpo, headers={'Content-Type': 'multipart/form-data; boundary=separador'})
                                    try:
                                        with urllib.request.urlopen(req, timeout=5) as response:
                                            estado, data = response.status, json.load(response)
                                    except urllib.error.HTTPError as error:
                                        with error:
                                            estado, data = error.code, json.load(error)
                                    invalido = bool(campos and ('[' in campos[0][0] or len(campos[0]) == 3))
                                    if lectura_php and invalido:
                                        self.assertEqual(estado, 422, (ruta, campos))
                                        self.assertEqual(len(recibidos), antes)
                                        self.assertIn('único valor', data['detail'])
                                    else:
                                        self.assertEqual(estado, 200, (ruta, campos))
                                        self.assertEqual(len(recibidos), antes + 1)
                                        recibo = data['campos']
                                        self.assertIn(['file', 'foto_de_prueba'], recibo)
                                        if not lectura_php:
                                            for campo in campos:
                                                self.assertIn(list(campo[:2]), recibo)
                                        elif campos:
                                            self.assertEqual([v for k, v in recibo if k == 'modo'], [campos[-1][1]])
                        finally:
                            proc.terminate()
                            proc.wait(timeout=5)
        finally:
            backend.shutdown()
            backend.server_close()
            hilo.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
