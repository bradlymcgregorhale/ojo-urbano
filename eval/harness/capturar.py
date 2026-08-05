#!/usr/bin/env python3
"""Fase 1 del eval: captura de evidencia CONGELADA, una sola vez por foto.

Guarda, por foto: la prediccion del modelo local y los veredictos crudos de
los dos verificadores. El cambio que se evalua (CONSENSO_VLM_SOLO) vive
AGUAS ABAJO de esto, en la logica de consenso, asi que congelando esta capa
el diff entre los dos brazos queda 100% atribuible al cambio y no al ruido
de los modelos. El arbitro NO se llama aca: su prompt cambia entre brazos,
que es justamente el cambio, y se corre en la fase de replay.
"""
import os
import sys
from pathlib import Path

# Rutas relativas al repo: el harness corre desde cualquier clon.
REPO = Path(__file__).resolve().parents[2]
DATOS = REPO / "eval" / "datos"
sys.path.insert(0, str(REPO))
import json
import random
import sqlite3
import time
import urllib.request

AQUI = Path(__file__).resolve().parent
OJO = REPO
# Base privada de solicitudes scrapeadas (no se versiona). Solo hace falta
# para RE-MUESTREAR fotos nuevas. Para auditar los numeros publicados NO se
# necesita: la evidencia congelada ya esta en eval/datos/ y se analiza con
# eval/analizar.py. Las URLs de las fotos no se versionan: este script las
# resuelve de la base al vuelo y baja al cache local, que esta gitignoreado.
DB = os.environ.get("SOLICITUDES_DB", "")
DB = f"file:{DB}?mode=ro" if DB else ""
FOTOS = REPO / "eval" / "fotos_cache"   # único lugar, y está gitignoreado
FOTOS.mkdir(exist_ok=True)
SALIDA = DATOS / "evidencia_congelada.jsonl"

# 8 de las 10 categorias solo-VLM tienen concepto propio en la ciudad.
# trapitos: no existe (es X-only + 911 por diseno). residuos_establecimiento:
# sin mapeo limpio. Las dos quedan SIN PROBAR y se reportan como tal.
OBJETIVO = {
    "vehiculo_mal_estacionado": "Vehículo mal estacionado",
    "columna_poste_cable": "Columna/poste/cable en mal estado, abandonado o cortado",
    "reposicion_contenedor": "Reposición de contenedor",
    "lavado_contenedor": "Lavado de contenedor",
    "lavado_cesto": "Lavado de cesto papelero",
    "puesto_diarios": "Irregularidades en puesto de diarios",
    "puesto_flores": "Irregularidades en puesto de flores",
    "mayor_iluminacion": "Mayor iluminación en calle / plaza",
}
N_OBJETIVO = int(os.environ.get("N_OBJETIVO", "15"))
N_AZAR = int(os.environ.get("N_AZAR", "100"))

random.seed(20260804)  # muestreo reproducible


def filas(con, concepto, n, saltar=()):
    q = ("SELECT identificador, imagenes_json, concepto FROM solicitudes "
         "WHERE concepto = ? AND imagenes_json LIKE '%adjuntosSUACI%' "
         "ORDER BY CAST(substr(identificador,1,8) AS INTEGER) DESC LIMIT ?")
    out = []
    for ident, imgs, concepto_r in con.execute(q, (concepto, n * 4)):
        if ident in saltar:
            continue
        try:
            url = next(i["contenido"] for i in json.loads(imgs)
                       if str(i.get("contenido", "")).lower().endswith(
                           (".jpg", ".jpeg", ".png")))
        except (StopIteration, ValueError, TypeError):
            continue
        out.append((ident, url, concepto_r))
        if len(out) >= n:
            break
    return out


def muestra_azar(con, n, saltar):
    """Muestra ponderada por produccion: refleja la mezcla real de reportes."""
    tot = con.execute("SELECT COUNT(*) FROM solicitudes WHERE imagenes_json "
                      "LIKE '%adjuntosSUACI%'").fetchone()[0]
    vistos, out = set(saltar), []
    intentos = 0
    while len(out) < n and intentos < n * 40:
        intentos += 1
        off = random.randrange(tot)
        fila = con.execute(
            "SELECT identificador, imagenes_json, concepto FROM solicitudes "
            "WHERE imagenes_json LIKE '%adjuntosSUACI%' LIMIT 1 OFFSET ?",
            (off,)).fetchone()
        if not fila or fila[0] in vistos:
            continue
        try:
            url = next(i["contenido"] for i in json.loads(fila[1])
                       if str(i.get("contenido", "")).lower().endswith(
                           (".jpg", ".jpeg", ".png")))
        except (StopIteration, ValueError, TypeError):
            continue
        vistos.add(fila[0])
        out.append((fila[0], url, fila[2]))
    return out


def bajar(ident, url):
    seguro = ident.replace("/", "-")  # los identificadores vienen "00860541/26"
    dest = FOTOS / f"{seguro}.jpg"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        urllib.request.urlretrieve(url, dest)
        return dest if dest.stat().st_size > 0 else None
    except Exception as e:
        print(f"       fallo la descarga: {type(e).__name__} {str(e)[:80]}")
        return None


def main():
    if not DB:
        sys.exit("Pone SOLICITUDES_DB=/ruta/solicitudes.sqlite para re-muestrear.\n"
                 "Para auditar lo publicado no hace falta: corre eval/analizar.py.")
    con = sqlite3.connect(DB, uri=True)
    hechos = set()
    if SALIDA.exists():
        for linea in SALIDA.read_text().splitlines():
            try:
                hechos.add(json.loads(linea)["ident"])
            except Exception:
                pass
    print(f"ya capturadas: {len(hechos)}")

    trabajo = []
    for clave, concepto in OBJETIVO.items():
        f = filas(con, concepto, N_OBJETIVO, hechos)
        trabajo += [(i, u, c, "objetivo", clave) for i, u, c in f]
        print(f"  objetivo {clave:26s} {len(f):3d} fotos")
    az = muestra_azar(con, N_AZAR, hechos | {t[0] for t in trabajo})
    trabajo += [(i, u, c, "azar", None) for i, u, c in az]
    print(f"  azar {'':30s} {len(az):3d} fotos")
    print(f"total a capturar: {len(trabajo)}")

    import servidor
    import verificador
    from PIL import Image
    CATS = servidor.CATEGORIAS
    if not verificador.disponible():
        sys.exit("!! falta OPENROUTER_API_KEY: no se puede capturar la evidencia VLM")

    t0 = time.time()
    with SALIDA.open("a") as sal:
        for n, (ident, url, concepto, cohorte, clave) in enumerate(trabajo, 1):
            ruta = bajar(ident, url)
            if not ruta:
                print(f"  [{n}/{len(trabajo)}] {ident} SIN FOTO")
                continue
            try:
                img = Image.open(ruta).convert("RGB")
            except Exception as e:
                print(f"  [{n}/{len(trabajo)}] {ident} ilegible: {e}")
                continue
            local = servidor.clasificar_local(img)
            data_url = verificador._imagen_data_url(img)
            import concurrent.futures as cf
            with cf.ThreadPoolExecutor(2) as pool:
                veredictos = list(pool.map(
                    lambda m: verificador._verificar_uno(m, data_url, CATS, ""),
                    verificador.VERIFICADORES))
            sal.write(json.dumps({
                "ident": ident, "concepto": concepto, "cohorte": cohorte,
                "clave_esperada": clave,
                "local": local, "veredictos": veredictos,
            }, ensure_ascii=False) + "\n")
            sal.flush()
            ok = sum(1 for v in veredictos if v.get("ok"))
            transcurrido = time.time() - t0
            print(f"  [{n}/{len(trabajo)}] {ident} {cohorte[:3]} vlm_ok={ok}/2 "
                  f"({transcurrido/n:.1f}s/foto, faltan "
                  f"{(len(trabajo)-n)*transcurrido/n/60:.0f} min)")
    print(f"listo en {(time.time()-t0)/60:.1f} min -> {SALIDA}")


if __name__ == "__main__":
    main()
