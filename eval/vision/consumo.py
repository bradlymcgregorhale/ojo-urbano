"""Concilia los cargos por foto a partir de registros de uso de Ojo Urbano."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
import os
from pathlib import Path

from eval.vision.centavo import TOPE, errores


def leer_uso(rutas):
    for ruta in rutas:
        with ruta.open(encoding="utf-8") as archivo:
            for linea in archivo:
                if linea.lstrip().startswith("{"):
                    evento = json.loads(linea, parse_float=Decimal)
                    if evento.get("evento") == "openrouter_uso":
                        yield evento


def _importe(valor):
    if type(valor) in (int, Decimal, float):
        monto = Decimal(str(valor))
        if monto.is_finite() and monto >= 0:
            return monto
    return None


def _identificador(valor):
    return isinstance(valor, str) and bool(valor.strip())


def _validar(casos):
    if not isinstance(casos, list) or not casos:
        raise ValueError("El manifiesto requiere una lista de casos")
    fichas = {}
    pedidos = {}
    for caso in casos:
        if not all(_identificador(caso[k]) for k in ("id", "entrada", "modo", "modo_version")):
            raise ValueError("Cada caso requiere ID, entrada, modo y versión")
        ficha = (caso["id"], caso["entrada"])
        if ficha in fichas or not isinstance(caso["pedidos"], dict) or not caso["pedidos"]:
            raise ValueError("Caso repetido o sin pedidos vinculados")
        fichas[ficha] = caso
        for pedido, intentos in caso["pedidos"].items():
            if not _identificador(pedido) or pedido in pedidos:
                raise ValueError("Cada pedido debe pertenecer a un solo caso")
            if intentos is not None and (type(intentos) is not int or intentos < 1):
                raise ValueError("El total esperado de intentos debe ser positivo o null")
            pedidos[pedido] = intentos
    return fichas, pedidos


def _cargos(eventos, pedidos):
    intentos = defaultdict(set)
    cargos = defaultdict(dict)
    pendientes = set()
    vinculados = {}
    for evento in eventos:
        pedido = evento.get("pedido")
        if pedido not in pedidos:
            continue
        generacion = evento.get("id")
        etapa, numero = evento.get("etapa"), evento.get("intento")
        monto = _importe(evento.get("cost"))
        if not _identificador(generacion):
            pendientes.add(pedido)
            continue
        if vinculados.setdefault(generacion, pedido) != pedido:
            raise ValueError("Un ID de generación figura en dos pedidos")
        if _identificador(etapa) and type(numero) is int and numero > 0:
            intentos[pedido].add((generacion, etapa, numero))
        else:
            pendientes.add(pedido)
        if monto is None:
            pendientes.add(pedido)
            continue
        previo = cargos[pedido].setdefault(generacion, monto)
        if previo != monto:
            raise ValueError("Un ID de generación tiene importes distintos")
    return intentos, cargos, pendientes


def _diferencias(caso):
    referencia = caso.get("esperado", {})
    etiquetas = caso.get("etiquetas")
    if not isinstance(referencia, dict) or any(
        not _identificador(k) or (v is not None and type(v) is not bool)
        for k, v in referencia.items()
    ):
        raise ValueError("La referencia debe tener etiquetas true, false o null")
    if etiquetas is not None and (not isinstance(etiquetas, list)
                                  or any(not _identificador(k) for k in etiquetas)):
        raise ValueError("Las etiquetas de la respuesta deben ser una lista o null")
    if etiquetas is None or all(v is None for v in referencia.values()):
        return None
    return sorted(errores(set(etiquetas), referencia))


def conciliar(casos, eventos):
    fichas, pedidos = _validar(casos)
    intentos, cargos, pendientes = _cargos(eventos, pedidos)
    detalle = []
    resumen = Counter(dict.fromkeys((
        "casos", "casos_conciliados", "casos_con_consumo_pendiente", "casos_sobre_el_tope",
        "casos_sin_puntuar", "casos_con_diferencias", "diferencias_de_etiqueta",
    ), 0))
    maximo = None
    for caso in fichas.values():
        vinculados = caso["pedidos"]
        registros = []
        costo = Decimal(0)
        for pedido, esperados in vinculados.items():
            observados = len(intentos[pedido])
            subtotal = sum(cargos[pedido].values(), Decimal(0))
            registros.append({
                "pedido": pedido, "intentos_esperados": esperados, "intentos_identificados": observados,
                "generaciones_con_importe": len(cargos[pedido]), "costo_conocido_usd": str(subtotal),
                "conciliado": observados == esperados and pedido not in pendientes,
            })
            costo += subtotal
        conciliado = all(r["conciliado"] for r in registros)
        sobre_tope = costo > TOPE
        diferencias = _diferencias(caso)
        detalle.append({k: caso[k] for k in ("id", "entrada", "modo", "modo_version")} | {
            "costo_conocido_usd": str(costo), "conciliado": conciliado,
            "pedidos": registros, "supera_tope": sobre_tope,
            "diferencias": diferencias,
        })
        resumen["casos"] += 1
        resumen["casos_conciliados"] += conciliado
        resumen["casos_con_consumo_pendiente"] += not conciliado
        resumen["casos_sobre_el_tope"] += sobre_tope
        resumen["casos_sin_puntuar"] += diferencias is None
        resumen["casos_con_diferencias"] += bool(diferencias)
        resumen["diferencias_de_etiqueta"] += len(diferencias or [])
        if conciliado:
            maximo = costo if maximo is None else max(costo, maximo)
    return dict(resumen) | {"maximo_conciliado_usd": str(maximo) if maximo is not None else None}, detalle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casos", required=True, type=Path, help="Manifiesto privado de la ejecución")
    parser.add_argument("--uso", required=True, action="append", type=Path, help="Log de uso (repetible)")
    parser.add_argument("--detalle", required=True, type=Path, help="Archivo privado nuevo para el detalle")
    args = parser.parse_args()
    try:
        casos = json.loads(args.casos.read_text(encoding="utf-8"))["casos"]
        resumen, detalle = conciliar(casos, leer_uso(args.uso))
        with open(args.detalle, "x", encoding="utf-8",
                  opener=lambda ruta, flags: os.open(ruta, flags, 0o600)) as archivo:
            json.dump(detalle, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(2, f"Conciliación incompleta: {error}\n")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    return int(any(resumen[k] for k in (
        "casos_con_consumo_pendiente", "casos_sobre_el_tope", "casos_sin_puntuar", "casos_con_diferencias",
    )))


if __name__ == "__main__":
    raise SystemExit(main())
