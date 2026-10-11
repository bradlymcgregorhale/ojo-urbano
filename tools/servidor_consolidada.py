#!/usr/bin/env python3
"""Sirve una ronda recuperada y sus resultados guardados, sin ejecutar análisis pagos."""
import argparse
import hashlib
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
try:
    from .servidor_revision import Handler
except ImportError:
    from servidor_revision import Handler


class Consolidada(Handler):
    def do_GET(self):
        if not self._origen_local() or not self._ruta_permitida():
            return self._json(403, {"error": "ruta u origen no permitido"})
        ruta = urlsplit(self.path).path
        if ruta == "/api/estado":
            return self._json(200, self.server.estado)
        if ruta.startswith("/api/intento/"):
            resultado = self.server.resultados.get(ruta.removeprefix("/api/intento/"))
            return self._json(200, resultado) if resultado else self._json(404, {"error": "resultado inexistente"})
        if ruta.startswith("/api/") or ruta == "/estado":
            return self._json(404, {"error": "no existe"})
        super().do_GET()

    def do_POST(self):
        if not self._origen_local():
            return self._json(403, {"error": "origen no permitido"})
        return self._json(403, {"error": "Esta recuperación sólo muestra resultados guardados. No ejecuta análisis pagos."})


def crear_servidor(carpeta, puerto=8772):
    raiz = Path(carpeta).resolve(strict=True)
    datos = json.loads((raiz / "datos.json").read_text())
    revision = json.loads((raiz / "revision_original.json").read_text())
    if revision["conjunto"] != datos["conjunto"]:
        raise ValueError("La revisión corresponde a otra ronda")
    ids = set()
    for foto in datos["fotos"]:
        archivo = (raiz / foto["archivo"]).resolve()
        archivo.relative_to(raiz)
        disponible = foto.get("foto_recuperada", True)
        if disponible is False and archivo.exists():
            raise ValueError("Una foto existente no puede marcarse ausente")
        if foto["id"] in ids or (disponible is not False and hashlib.sha256(archivo.read_bytes()).hexdigest() != foto["sha256"]):
            raise ValueError("Foto duplicada o huella distinta")
        ids.add(foto["id"])
        if revision["revisiones"][foto["id"]]["sha256"] != foto["sha256"]:
            raise ValueError("La revisión no corresponde a la foto")
    if ids != set(revision["revisiones"]):
        raise ValueError("Faltan fotos de la revisión")
    resultados = {}
    intentos = []
    for foto, propuesta in revision.get("propuestas", {}).items():
        if foto not in ids or propuesta.get("foto") != foto or not isinstance(propuesta.get("resultado"), dict):
            raise ValueError("Propuesta no corresponde a la ronda")
        intento = "recuperado-" + foto
        resultados[intento] = propuesta
        intentos.append({"id": intento, "foto": foto, "estado": "listo"})
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), partial(Consolidada, directory=str(raiz)))
    servidor.resultados = resultados
    servidor.estado = {"intentos": intentos, "gasto_usd": datos.get("gasto_usd", 0), "tope_usd": datos.get("tope_usd", 0),
                       "reserva_incierta_usd": 0, "reserva_acotada_usd": 0,
                       "autorizado": False, "activo": None,
                       "bloqueo": "Ronda recuperada: resultados disponibles, nuevos análisis deshabilitados."}
    return servidor


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("carpeta")
    parser.add_argument("puerto", type=int, nargs="?", default=8772)
    args = parser.parse_args()
    with crear_servidor(args.carpeta, args.puerto) as servidor:
        print(f"http://127.0.0.1:{servidor.server_port}/revision.html", flush=True)
        servidor.serve_forever()
