#!/usr/bin/env python3
"""Sirve una bandeja privada en loopback y guarda su progreso en disco.

    python3 tools/servidor_revision.py <carpeta_revision> [puerto]
"""
import argparse
import json
import socket
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_ESTADO = 5_000_000


class Handler(SimpleHTTPRequestHandler):
    def _json(self, codigo, payload):
        cuerpo = json.dumps(payload).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _origen_local(self):
        # El Host también se valida: evita publicar datos por DNS rebinding.
        try:
            host = urlsplit("http://" + self.headers.get("Host", ""))
            if (host.hostname not in {"127.0.0.1", "localhost"}
                    or host.port != self.server.server_port or host.username
                    or host.password or host.path or host.query or host.fragment):
                return False
            origen = self.headers.get("Origin")
            if origen:
                parsed = urlsplit(origen)
                if (parsed.scheme != "http" or parsed.netloc != host.netloc
                        or parsed.path or parsed.query or parsed.fragment):
                    return False
            return self.headers.get("Sec-Fetch-Site") != "cross-site"
        except ValueError:
            return False

    def _ruta_permitida(self):
        raiz = Path(self.directory).resolve()
        try:
            destino = Path(self.translate_path(self.path)).resolve()
            relativo = destino.relative_to(raiz)
            return not any(parte.startswith(".") for parte in relativo.parts)
        except (ValueError, OSError, RuntimeError):
            return False

    def list_directory(self, path):
        self.send_error(403, "No se permite listar esta carpeta")
        return None

    def do_HEAD(self):
        if not self._origen_local() or not self._ruta_permitida():
            self.send_error(403, "Ruta u origen no permitido")
            return
        super().do_HEAD()

    def do_GET(self):
        if not self._origen_local() or not self._ruta_permitida():
            return self._json(403, {"error": "ruta u origen no permitido"})
        if urlsplit(self.path).path.rstrip("/") == "/estado":
            try:
                with self.server.estado_lock:
                    datos = (json.loads(self.server.estado_file.read_text())
                             if self.server.estado_file.exists() else {})
            except (OSError, ValueError):
                return self._json(500, {"error": "no pude leer el estado"})
            return self._json(200, datos)
        super().do_GET()

    def do_POST(self):
        if not self._origen_local():
            return self._json(403, {"error": "origen no permitido"})
        if urlsplit(self.path).path.rstrip("/") != "/estado":
            return self._json(404, {"error": "no existe"})
        # /estado nunca sigue un enlace hacia otro archivo del equipo.
        if self.server.estado_file.is_symlink():
            return self._json(403, {"error": "ruta de estado no permitida"})
        if self.headers.get("Transfer-Encoding"):
            return self._json(400, {"error": "transferencia no permitida"})
        longitud = self.headers.get_all("Content-Length", [])
        if len(longitud) != 1:
            return self._json(411, {"error": "falta una longitud única"})
        try:
            n = int(longitud[0])
            if n < 0:
                return self._json(400, {"error": "longitud inválida"})
            if n > MAX_ESTADO:
                return self._json(413, {"error": "demasiado grande"})
            self.connection.settimeout(5)
            cuerpo = self.rfile.read(n)
            if len(cuerpo) != n:
                return self._json(400, {"error": "cuerpo incompleto"})
            datos = json.loads(cuerpo)
            if not isinstance(datos, dict) or any(not isinstance(v, dict) for v in datos.values()):
                return self._json(400, {"error": "estado inválido"})
        except (ValueError, UnicodeError, socket.timeout):
            return self._json(400, {"error": "json inválido"})
        try:
            with self.server.estado_lock:
                # Temporal exclusivo: no sigue un enlace .tmp preexistente.
                import tempfile
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                                 dir=self.directory, delete=False) as tmp:
                    temporal = Path(tmp.name)
                    try:
                        tmp.write(json.dumps(datos, ensure_ascii=False))
                        tmp.flush()
                        temporal.replace(self.server.estado_file)
                    finally:
                        temporal.unlink(missing_ok=True)
        except OSError:
            return self._json(500, {"error": "no pude guardar el estado"})
        return self._json(200, {"ok": True, "fotos": len(datos)})

    def log_message(self, *args):
        pass


def crear_servidor(carpeta, puerto=8777):
    raiz = Path(carpeta).resolve(strict=True)
    if not raiz.is_dir():
        raise ValueError("La revisión debe ser una carpeta")
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), partial(Handler, directory=str(raiz)))
    servidor.estado_file = raiz / "estado_revision.json"
    servidor.estado_lock = threading.Lock()
    return servidor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("carpeta")
    parser.add_argument("puerto", type=int, nargs="?", default=8777)
    args = parser.parse_args()
    with crear_servidor(args.carpeta, args.puerto) as servidor:
        print(f"revisión: http://127.0.0.1:{servidor.server_port}/")
        servidor.serve_forever()


if __name__ == "__main__":
    main()
