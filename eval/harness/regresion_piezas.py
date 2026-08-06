#!/usr/bin/env python3
"""¿La regla de "piezas de contenedor" se comió el retiro_muebles legítimo?

La rúbrica ahora manda las piezas desprendidas de un contenedor (cabezal,
tapa) a reparacion_contenedor en vez de retiro_muebles. Una regla así puede
pasarse de largo y suprimir voluminosos de verdad, que es justo lo que pasó
la vez anterior que se endureció la rúbrica (rompió retiro_poda).

Se corren SOLO las fotos cuyo gold incluye alguna clave de contenedor/cesto o
retiro_muebles, con los MISMOS dos modelos con los que se capturó
evidencia_rubrica_v3.jsonl, para que la única diferencia sea la rúbrica.

    regresion_piezas.py

Compara, por modelo y por foto, si la clave la reportó v3 y si la reporta hoy.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATOS = REPO / "eval" / "datos"
FOTOS = REPO / "eval" / "fotos_cache"
sys.path.insert(0, str(REPO))

_env = REPO / ".env"
for linea in (_env.read_text().splitlines() if _env.exists() else []):
    linea = linea.strip()
    if linea and not linea.startswith("#") and "=" in linea:
        k, _, v = linea.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import verificador as V  # noqa: E402

CATS = json.loads((REPO / "categorias.json").read_text())
MODELOS = ["openai/gpt-5-mini", "google/gemini-3.5-flash-lite"]
INTERES = {"retiro_muebles", "reparacion_contenedor",
           "reposicion_contenedor", "reparacion_cesto"}


def main():
    sel = json.load(open("/tmp/regresion_sel.json"))
    v3 = {f["ident"]: f for f in
          (json.loads(l) for l in (DATOS / "evidencia_rubrica_v3.jsonl").open())}

    V.VERIFICADORES = MODELOS
    salida = (Path(__file__).parent / "regresion_piezas.jsonl").open("w")
    antes = defaultdict(Counter)
    ahora = defaultdict(Counter)
    detalle = []

    for i, fila in enumerate(sel, 1):
        ruta = FOTOS / (fila["ident"].replace("/", "-") + ".jpg")
        if not ruta.exists():
            continue
        data_url = V._imagen_data_url_de_bytes(ruta.read_bytes()) \
            if hasattr(V, "_imagen_data_url_de_bytes") else None
        if data_url is None:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(ruta.read_bytes())).convert("RGB")
            data_url = V._imagen_data_url(img)

        gold = set(fila["gold"])
        nuevo = {}
        for m in MODELOS:
            try:
                r = V._verificar_uno(m, data_url, CATS, "")
                nuevo[m] = {c["key"] for c in (r.get("categorias") or [])}
            except Exception as e:                       # noqa: BLE001
                print(f"  [{i}] {m} falló: {e}", file=sys.stderr)
                nuevo[m] = set()

        viejo = {}
        for v in (v3.get(fila["ident"], {}).get("veredictos") or []):
            viejo[v["modelo"]] = {c["key"] for c in (v.get("categorias") or [])}

        for m in MODELOS:
            for k in INTERES:
                if k in gold:
                    antes[m][k] += int(k in viejo.get(m, set()))
                    ahora[m][k] += int(k in nuevo.get(m, set()))

        detalle.append({"ident": fila["ident"], "gold": sorted(gold),
                        "v3": {m: sorted(viejo.get(m, [])) for m in MODELOS},
                        "hoy": {m: sorted(nuevo.get(m, [])) for m in MODELOS}})
        salida.write(json.dumps(detalle[-1], ensure_ascii=False) + "\n")
        salida.flush()
        print(f"  [{i}/{len(sel)}] {fila['ident']} gold={sorted(gold)}",
              file=sys.stderr)

    salida.close()
    total_gold = Counter()
    for fila in sel:
        for k in fila["gold"]:
            total_gold[k] += 1

    print(json.dumps({
        "n_fotos": len(detalle),
        "recall_por_modelo": {
            m: {k: {"gold": total_gold[k], "v3": antes[m][k], "hoy": ahora[m][k]}
                for k in sorted(INTERES) if total_gold[k]}
            for m in MODELOS},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
