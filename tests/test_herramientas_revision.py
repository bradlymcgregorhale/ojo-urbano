"""Regresiones de la bandeja privada con archivos y HTTP sintéticos."""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tools.servidor_revision import crear_servidor

RAIZ = Path(__file__).resolve().parents[1]


class ServidorRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name) / 'bandeja'
        self.raiz.mkdir()
        (self.raiz / 'index.html').write_text('<h1>Revisión sintética</h1>')
        self.estado = self.raiz / 'estado_revision.json'
        self.estado.write_text('{"S1":{"rev":false}}')
        self.server = crear_servidor(self.raiz, 0)
        self.hilo = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.hilo.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.hilo.join()
        self.tmp.cleanup()

    def pedir(self, path='/', data=None, headers=None, method=None):
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as response:
            with response:
                return response.code, response.read()

    def test_enlace_externo_no_se_lee_por_get_ni_head(self):
        fuera = Path(self.tmp.name) / 'fuera.txt'
        fuera.write_text('marcador externo')
        (self.raiz / 'enlace').symlink_to(fuera)
        for method in ('GET', 'HEAD'):
            code, body = self.pedir('/enlace', method=method)
            self.assertEqual(code, 403)
            self.assertNotIn(b'marcador externo', body)

    def test_ocultos_y_listados_no_se_publican(self):
        (self.raiz / '.env').write_text('marcador privado')
        (self.raiz / 'sin_indice').mkdir()
        for path in ('/.env', '/sin_indice/', '/%2eenv'):
            self.assertEqual(self.pedir(path)[0], 403)
        self.assertEqual(self.pedir('/')[0], 200)

    def test_host_y_origen_externos_se_rechazan(self):
        for headers in ({'Host': f'atacante.example:{self.server.server_port}'},
                        {'Origin': 'https://atacante.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.pedir('/', headers=headers)[0], 403)
            self.assertEqual(self.pedir('/estado', b'{}', headers=headers)[0], 403)
        self.assertEqual(json.loads(self.estado.read_text()), {'S1': {'rev': False}})

    def test_estado_invalido_no_reemplaza_progreso(self):
        anterior = self.estado.read_bytes()
        for data in (b'[]', b'null', b'3', b'{"S1":null}', b'{invalido'):
            self.assertEqual(self.pedir('/estado', data)[0], 400)
            self.assertEqual(self.estado.read_bytes(), anterior)
        for n, expected in (('-1', 400), ('5000001', 413)):
            with socket.create_connection(('127.0.0.1', self.server.server_port)) as sock:
                req = (f'POST /estado HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
                       f'Content-Length: {n}\r\nConnection: close\r\n\r\n')
                sock.sendall(req.encode())
                self.assertIn(f' {expected} '.encode(), sock.recv(4096).split(b'\r\n')[0])
            self.assertEqual(self.estado.read_bytes(), anterior)

    def test_estado_valido_y_enlace_no_se_sobrescribe(self):
        payload = {'S1': {'rev': True, 'cats': {'recoleccion': True}}}
        self.assertEqual(self.pedir('/estado', json.dumps(payload).encode())[0], 200)
        self.assertEqual(json.loads(self.pedir('/estado')[1]), payload)
        destino = Path(self.tmp.name) / 'otro.json'
        destino.write_text('no reemplazar')
        self.estado.unlink()
        self.estado.symlink_to(destino)
        self.assertEqual(self.pedir('/estado', b'{}')[0], 403)
        self.assertEqual(destino.read_text(), 'no reemplazar')


class GeneradorRevisionTests(unittest.TestCase):
    def generar(self, folder, ident='S1', archivo='foto.jpg', desc='texto'):
        (folder / 'categorias_modelo.json').write_text('["recoleccion"]')
        (folder / 'predicciones.json').write_text(json.dumps([{
            'id': ident, 'archivo': archivo, 'pred': ['recoleccion'],
            'gravedad': 3, 'en_cache': True, 'descripcion': desc}]))
        return subprocess.run([sys.executable, str(RAIZ / 'tools/generar_tool.py'), str(folder)],
                              cwd=folder, capture_output=True, text=True)

    def test_json_no_puede_cerrar_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            texto = '</script><script>window.marcador=1</script>'
            self.assertEqual(self.generar(folder, desc=texto).returncode, 0)
            html = (folder / 'index.html').read_text()
            self.assertNotIn(texto, html)
            self.assertIn('\\u003c/script\\u003e', html)
            self.assertIn('esc(p.desc)', html)
            self.assertNotIn("onclick=\"quitar(PHOTOS", html)

    def test_ids_y_rutas_invalidos_se_rechazan(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for ident in ('<img>', '__proto__', 'constructor', '" onclick="x'):
                self.assertNotEqual(self.generar(folder, ident=ident).returncode, 0)
            for archivo in ('../fuera.jpg', '/tmp/foto.jpg', '.env', 'carpeta/../foto.jpg'):
                self.assertNotEqual(self.generar(folder, archivo=archivo).returncode, 0)
            self.assertFalse((folder / 'index.html').exists())


if __name__ == '__main__':
    unittest.main()
