#!/usr/bin/env python3
"""Sirve una carpeta de revisión y guarda el progreso EN EL DISCO.

La herramienta de revisión guardaba solo en localStorage del navegador. Eso
se cae en el teléfono (Safari en modo privado tira excepción al escribir, y
el sistema puede limpiar el storage por su cuenta) y además no se comparte
entre la compu y el celular. Acá el estado va a un JSON al lado de las fotos:
se puede revisar desde cualquier dispositivo y el progreso queda del lado de
la máquina, que es donde después se puntúa.

    python3 servidor_revision.py <carpeta_revision> [puerto]
"""
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REV = Path(sys.argv[1]).resolve()
PUERTO = int(sys.argv[2]) if len(sys.argv) > 2 else 8777
ESTADO = REV / "estado_revision.json"
_lock = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def _json(self, codigo, payload):
        cuerpo = json.dumps(payload).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def end_headers(self):
        # Sin esto el navegador se queda con la copia vieja de index.html. Ya
        # pasó: el mismo puerto sirvió antes una versión con los nombres de
        # archivo rotos y el navegador la siguió mostrando, sin fotos.
        if not self.path.startswith("/fotos/"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self.path.rstrip("/") == "/estado":
            with _lock:
                datos = json.loads(ESTADO.read_text()) if ESTADO.exists() else {}
            return self._json(200, datos)
        return super().do_GET()

    def do_POST(self):
        if self.path.rstrip("/") != "/estado":
            return self._json(404, {"error": "no existe"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 5_000_000:
                return self._json(413, {"error": "demasiado grande"})
            datos = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "json inválido"})
        with _lock:
            # escritura atómica: si el celular corta a mitad de la subida, el
            # archivo bueno sigue estando
            tmp = ESTADO.with_suffix(".tmp")
            tmp.write_text(json.dumps(datos, ensure_ascii=False))
            tmp.replace(ESTADO)
        return self._json(200, {"ok": True, "fotos": len(datos)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"revisión: http://127.0.0.1:{PUERTO}/  ·  estado en {ESTADO}")
    ThreadingHTTPServer(("127.0.0.1", PUERTO),
                        partial(Handler, directory=str(REV))).serve_forever()
