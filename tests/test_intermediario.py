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
    @unittest.skipUnless(shutil.which('php'), 'La prueba necesita PHP')
    def test_origenes_confiables_y_limites_cidr(self):
        original = (Path(__file__).resolve().parents[1] / 'deploy/ojourbano.php').read_text()
        funciones = original.split("$backend =", 1)[0]
        casos = [
            ('173.245.48.0', '203.0.113.8', '203.0.113.8'),
            ('173.245.63.255', '2001:db8::8', '2001:db8::8'),
            ('173.245.47.255', '203.0.113.8', '173.245.47.255'),
            ('173.245.64.0', '203.0.113.8', '173.245.64.0'),
            ('104.23.255.255', '203.0.113.8', '203.0.113.8'),
            ('104.28.0.0', '203.0.113.8', '104.28.0.0'),
            ('2606:4700::1', '203.0.113.8', '203.0.113.8'),
            ('2606:4701::1', '203.0.113.8', '2606:4701::1'),
            ('2a06:98c7:ffff::1', '203.0.113.8', '203.0.113.8'),
            ('2a06:98c8::1', '203.0.113.8', '2a06:98c8::1'),
            ('127.0.0.1', '203.0.113.8', '127.0.0.1'),
            ('173.245.48.1', '203.0.113.8, 1.2.3.4', '173.245.48.1'),
            ('173.245.48.1', '', '173.245.48.1'),
            ('invalida', '203.0.113.8', '127.0.0.1'),
        ]
        codigo = funciones + "\n$cases = json_decode('" + json.dumps(casos) + "', true);\n"
        codigo += "foreach ($cases as $case) { echo ojo_proxy_client_ip($case[0], $case[1]) . PHP_EOL; }"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'ip.php'
            script.write_text(codigo)
            salida = subprocess.check_output(['php', str(script)], text=True).splitlines()
        self.assertEqual(salida, [c[2] for c in casos])

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
                self.wfile.write(json.dumps({'campos': [(k, v.decode()) for k, v in campos], 'ip': self.headers.get('X-Forwarded-For')}).encode())

            def log_message(self, *args):
                pass

        backend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        hilo = threading.Thread(target=backend.serve_forever, daemon=True)
        hilo.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                proxy = Path(tmp) / 'index.php'
                original = (Path(__file__).resolve().parents[1] / 'deploy/ojourbano.php').read_text()
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
                            for ruta in ('clasificar', 'trabajos', 'comparar', '%63lasificar', 'trabajos%2F', 'otra'):
                                for campos in ([('modo', 'bajo')], [('modo', 'medio')], [],
                                               [('modo', 'bajo'), ('modo', 'alto')],
                                               [('modo[]', 'bajo')], [('modo[clave]', 'medio')],
                                               [('modo', 'alto', 'modo.txt')],
                                               [('modo', 'bajo'), ('modo[]', 'alto')],
                                               [('modo[]', 'alto'), ('modo', 'bajo')],
                                               [('modo', 'bajo'), ('modo', 'alto', 'modo.txt')]):
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
                                                                 data=cuerpo, headers={'Content-Type': 'multipart/form-data; boundary=separador', 'CF-Connecting-IP': '203.0.113.55', 'X-Forwarded-For': '198.51.100.11'})
                                    try:
                                        with urllib.request.urlopen(req, timeout=5) as response:
                                            estado, data = response.status, json.load(response)
                                    except urllib.error.HTTPError as error:
                                        with error:
                                            estado, data = error.code, json.load(error)
                                    invalido = bool(campos and ('[' in campos[-1][0] or any(len(c) == 3 for c in campos)))
                                    if lectura_php and invalido and ruta != 'otra':
                                        self.assertEqual(estado, 422, (ruta, campos))
                                        self.assertEqual(len(recibidos), antes)
                                        self.assertIn('único valor', data['detail'])
                                    else:
                                        self.assertEqual(estado, 200, (ruta, campos))
                                        self.assertEqual(len(recibidos), antes + 1)
                                        self.assertEqual(data['ip'], '127.0.0.1')
                                        recibo = data['campos']
                                        self.assertIn(['file', 'foto_de_prueba'], recibo)
                                        if not lectura_php:
                                            for campo in campos:
                                                self.assertIn(list(campo[:2]), recibo)
                                        elif campos and not invalido:
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
