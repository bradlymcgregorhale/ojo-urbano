#!/usr/bin/env python3
"""Piso de ruido de `problemas`, para los criterios de aceptación de #7.

#7 midió que el árbitro cambiaba de opinión en el 17,5% de las fotos ante la
MISMA entrada. Aquel número salió de replayar evidencia congelada: los
veredictos de los verificadores están guardados, así que lo único que puede
variar entre dos corridas es el árbitro.

Acá se replica ese mismo experimento sobre la misma evidencia, con dos brazos:

  arbitro_confirma=1  el comportamiento VIEJO (el árbitro promueve a
                      confirmado lo que vio una sola fuente). Es el CONTROL:
                      tiene que reproducir el ruido original.
  arbitro_confirma=0  el default de hoy (lo de una sola fuente sale como
                      POSIBLE y el árbitro no lo promueve).

Los dos brazos corren sobre las mismas filas y en la misma sesión, así que la
diferencia no puede venir de la muestra ni del momento del día.

Se reporta por separado la cohorte dirigida y la aleatoria, que es el segundo
criterio de aceptación de #7.

    estabilidad.py [n_filas]

Sale JSON por stdout y un detalle por fila en estabilidad.jsonl.
"""
import json
import os
import sys
from collections import Counter
from math import sqrt
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATOS = REPO / "eval" / "datos"
sys.path.insert(0, str(REPO))

_env = REPO / ".env"
for linea in (_env.read_text().splitlines() if _env.exists() else []):
    linea = linea.strip()
    if linea and not linea.startswith("#") and "=" in linea:
        k, _, v = linea.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import verificador as V  # noqa: E402

CATS = json.loads((REPO / "categorias.json").read_text())
V._imagen_data_url = lambda img: ""     # la foto no viaja: es un replay


def wilson(exitos, n, z=1.96):
    """IC95 de una proporción. Con 0/100 el intervalo normal daría [0,0]."""
    if not n:
        return (0.0, 0.0)
    p = exitos / n
    d = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / d
    margen = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(100 * max(0.0, centro - margen), 1),
            round(100 * min(1.0, centro + margen), 1))


def una_corrida(fila):
    """Una pasada del pipeline con los veredictos congelados de esta fila."""
    congelados = {v["modelo"]: v for v in fila["veredictos"]}
    V.VERIFICADORES = list(congelados)
    V._verificar_uno = lambda m, du, cats, ctx="": congelados[m]
    r = V.verificar(None, CATS, fila["local"], "")
    return {
        # `problemas` de la API = confirmadas sin las claves de PRESENCIA
        "problemas": sorted(c["key"] for c in r["confirmadas"]
                            if c["key"] not in V.PRESENCIA),
        "en_duda": sorted(r.get("en_duda") or []),
        "posibles": sorted(c["key"] for c in (r.get("posibles") or [])),
    }


def main():
    filas = [json.loads(l) for l in (DATOS / "evidencia_congelada.jsonl").open()]
    if len(sys.argv) > 1:
        filas = filas[:int(sys.argv[1])]

    V.CONSENSO_VLM_SOLO = "confirma"
    salida = (Path(__file__).parent / "estabilidad.jsonl").open("w")
    res = {}

    for brazo, confirma in (("arbitro_confirma=1 (viejo)", True),
                            ("arbitro_confirma=0 (default de hoy)", False)):
        V.ARBITRO_CONFIRMA = confirma
        por_cohorte = Counter()
        distintos = Counter()
        churn = Counter()
        for fila in filas:
            a = una_corrida(fila)
            b = una_corrida(fila)
            coh = fila["cohorte"]
            por_cohorte[coh] += 1
            if a["problemas"] != b["problemas"]:
                distintos[coh] += 1
            for campo in ("en_duda", "posibles"):
                if a[campo] != b[campo]:
                    churn[campo] += 1
            salida.write(json.dumps({
                "brazo": brazo, "ident": fila["ident"], "cohorte": coh,
                "a": a, "b": b,
                "estable": a["problemas"] == b["problemas"]}, ensure_ascii=False) + "\n")
            salida.flush()

        n = sum(por_cohorte.values())
        d = sum(distintos.values())
        res[brazo] = {
            "n": n, "distintos": d, "pct": round(100 * d / n, 1) if n else None,
            "ic95": wilson(d, n),
            "por_cohorte": {
                c: {"n": por_cohorte[c], "distintos": distintos[c],
                    "pct": round(100 * distintos[c] / por_cohorte[c], 1),
                    "ic95": wilson(distintos[c], por_cohorte[c])}
                for c in sorted(por_cohorte)},
            # Lo que SIGUE variando. `problemas` es el criterio de #7, pero
            # decir solo eso escondería que el árbitro sigue siendo inestable:
            # su inestabilidad ahora se ve en estos campos, no en el veredicto.
            "churn_otros_campos": {k: f"{churn[k]}/{n}" for k in ("en_duda", "posibles")},
        }
        print(json.dumps({brazo: res[brazo]}, ensure_ascii=False, indent=2),
              file=sys.stderr)

    salida.close()
    print(json.dumps({
        "criterio_1": "replay x2, <5% de diferencia en problemas",
        "arbitro": V.ARBITRO, "temperatura": V.TEMPERATURA,
        "resultados": res}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
