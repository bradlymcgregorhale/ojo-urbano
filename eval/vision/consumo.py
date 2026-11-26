"""Concilia los cargos por foto con el registro de uso y los recibos de OpenRouter."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
import os
from pathlib import Path

from eval.vision.centavo import TOPE, errores

_RANGO_BOLSAS = {"bolsas": 15, "bolsas_minimo": None, "bolsas_fuentes": 2}
_CAMPO_COMPLETO = "bolsas_recuento_completo"


def leer_uso(rutas):
    for ruta in rutas:
        with ruta.open(encoding="utf-8") as archivo:
            for linea in archivo:
                if linea.lstrip().startswith("{"):
                    evento = json.loads(linea, parse_float=Decimal)
                    if evento.get("evento") == "openrouter_uso":
                        yield evento


def leer_recibos(rutas):
    for ruta in rutas:
        with ruta.open(encoding="utf-8") as archivo:
            contenido = json.load(archivo, parse_float=Decimal)
        yield from contenido if isinstance(contenido, list) else (contenido,)


def _importe(valor):
    if type(valor) in (int, Decimal, float):
        monto = Decimal(str(valor))
        if monto.is_finite() and monto >= 0:
            return monto
    return None


def _identificador(valor):
    return isinstance(valor, str) and bool(valor.strip())


def medidas_bolsas(resultado):
    escombros = next((p for p in resultado["problemas"] if p["key"] == "retiro_escombros"), None)
    if escombros is None:
        return {}
    revision = resultado.get("verificacion_escombros") or {}
    medidas = {k: revision[k] for k in ("bolsas_minimo", _CAMPO_COMPLETO, "bolsas_fuentes") if k in revision}
    if "bolsas" in escombros:
        medidas["bolsas"] = escombros["bolsas"]
    return medidas


def _desde_respuestas(caso):
    if "respuestas" not in caso:
        return caso | {"recuento_observado": None}
    respuestas = caso["respuestas"]
    if any(k in caso for k in ("pedidos", "etiquetas")) or not isinstance(respuestas, list) or not respuestas:
        raise ValueError("Las respuestas archivadas reemplazan los pedidos y las etiquetas manuales")
    pedidos, etiquetas, sin_traza, recuento = {}, None, 0, None
    for respuesta in respuestas:
        if respuesta is None:
            sin_traza += 1
            continue
        if not isinstance(respuesta, dict):
            raise ValueError("Cada respuesta archivada debe ser un objeto o null")
        resultado = respuesta.get("resultado", respuesta)
        if not isinstance(resultado, dict):
            raise ValueError("El resultado archivado debe ser un objeto")
        if "detail" in resultado and not {"modo", "consumo_api"} & resultado.keys():
            sin_traza += 1
            continue
        if any(resultado.get(k) != caso[k] for k in ("modo", "modo_version")):
            raise ValueError("La respuesta corresponde a otro modo o versión")
        consumo = resultado.get("consumo_api")
        if not consumo:
            sin_traza += 1
        else:
            pedido, intentos = consumo["pedido"], consumo["intentos"]
            if not _identificador(pedido):
                raise ValueError("La respuesta archivada tiene un ID de pedido inválido")
            anterior = pedidos.get(pedido)
            if intentos is not None and anterior is not None and intentos != anterior:
                raise ValueError("El mismo pedido tiene dos totales de intentos")
            pedidos[pedido] = anterior if intentos is None else intentos
        if ("problemas" in resultado and resultado.get("verificacion_activa") is not False
                and resultado.get("analisis_estado") != "sin_verificacion"):
            etiquetas = [p["key"] for campo in ("problemas", "elementos_detectados")
                         for p in resultado.get(campo, [])]
            if "recuento_esperado" in caso:
                recuento = medidas_bolsas(resultado)
    return caso | {"pedidos": pedidos, "etiquetas": etiquetas,
                   "respuestas_sin_traza": sin_traza, "recuento_observado": recuento}


def _validar(casos):
    if not isinstance(casos, list) or not casos:
        raise ValueError("El manifiesto requiere una lista de casos")
    fichas = {}
    pedidos = {}
    for entrada in casos:
        caso = _desde_respuestas(entrada)
        if not all(_identificador(caso[k]) for k in ("id", "entrada", "modo", "modo_version")):
            raise ValueError("Cada caso requiere ID, entrada, modo y versión")
        ficha = (caso["id"], caso["entrada"])
        if (ficha in fichas or not isinstance(caso["pedidos"], dict)
                or not (caso["pedidos"] or caso.get("respuestas_sin_traza"))):
            raise ValueError("Caso repetido o sin pedidos vinculados")
        fichas[ficha] = caso
        for pedido, intentos in caso["pedidos"].items():
            if not _identificador(pedido) or pedido in pedidos:
                raise ValueError("Cada pedido debe pertenecer a un solo caso")
            if intentos is not None and (type(intentos) is not int or intentos < 0):
                raise ValueError("El total esperado de intentos debe ser no negativo o null")
            pedidos[pedido] = intentos
    return fichas, pedidos


def _importes_recibos(recibos):
    importes = {}
    for recibo in recibos:
        datos = recibo.get("data") if isinstance(recibo, dict) else None
        if not isinstance(datos, dict):
            raise ValueError("El recibo debe contener los datos de una generación")
        generacion = datos.get("id")
        monto = _importe(datos.get("total_cost"))
        if not _identificador(generacion) or monto is None:
            raise ValueError("El recibo requiere ID de generación e importe válido")
        if importes.setdefault(generacion, monto) != monto:
            raise ValueError("Un ID de generación tiene recibos con importes distintos")
    return importes


def _cargos(eventos, pedidos, recibos):
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
        recibo = recibos.get(generacion)
        if recibo is not None:
            if monto is not None and monto != recibo:
                raise ValueError("El importe del recibo difiere del registro de uso")
            monto = recibo
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


def _intervalo_recuento(campo, esperado):
    desde, hasta = esperado if isinstance(esperado, list) and len(esperado) == 2 else (esperado, esperado)
    limite = _RANGO_BOLSAS[campo]
    if type(desde) is not int or desde < 0:
        raise ValueError("El recuento requiere un mínimo entero no negativo")
    if hasta is None:
        hasta = limite
    if hasta is not None and (type(hasta) is not int or hasta < desde or (limite is not None and hasta > limite)):
        raise ValueError("El recuento tiene un máximo inválido")
    return desde, hasta


def diferencias_recuento(esperado, medidas):
    campos = _RANGO_BOLSAS.keys() | {_CAMPO_COMPLETO}
    if not isinstance(esperado, dict) or not esperado or esperado.keys() - campos:
        raise ValueError("El recuento esperado debe indicar campos públicos de bolsas")
    diferencias = []
    for campo, valor in esperado.items():
        observado = (medidas or {}).get(campo)
        if campo == _CAMPO_COMPLETO:
            if type(valor) is not bool:
                raise ValueError("El recuento completo esperado debe ser true o false")
            coincide = observado is valor
        elif campo == "bolsas" and valor is None:
            coincide = observado is None
        else:
            desde, hasta = _intervalo_recuento(campo, valor)
            coincide = type(observado) is int and desde <= observado and (hasta is None or observado <= hasta)
        if medidas is not None and (campo not in medidas or not coincide):
            diferencias.append(campo)
    return None if medidas is None else sorted(diferencias)


def conciliar(casos, eventos, recibos=()):
    fichas, pedidos = _validar(casos)
    importes_recibos = _importes_recibos(recibos)
    intentos, cargos, pendientes = _cargos(eventos, pedidos, importes_recibos)
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
                "generaciones_con_recibo": len(cargos[pedido].keys() & importes_recibos.keys()),
                "conciliado": observados == esperados and pedido not in pendientes,
            })
            costo += subtotal
        sin_traza = caso.get("respuestas_sin_traza", 0)
        conciliado = not sin_traza and all(r["conciliado"] for r in registros)
        sobre_tope = costo > TOPE
        diferencias = _diferencias(caso)
        detalle.append({k: caso[k] for k in ("id", "entrada", "modo", "modo_version")} | {
            "costo_conocido_usd": str(costo), "conciliado": conciliado,
            "pedidos": registros, "supera_tope": sobre_tope,
            "diferencias": diferencias, "respuestas_sin_traza": sin_traza,
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
        if "recuento_esperado" in caso:
            diferencias_bolsas = diferencias_recuento(caso["recuento_esperado"], caso["recuento_observado"])
            detalle[-1].update(recuento_esperado=caso["recuento_esperado"],
                               recuento_observado=caso["recuento_observado"],
                               diferencias_recuento=diferencias_bolsas)
            resumen["casos_con_recuento_revisado"] += 1
            resumen["casos_sin_puntuar_recuento"] += diferencias_bolsas is None
            resumen["casos_con_diferencias_recuento"] += bool(diferencias_bolsas)
    return dict(resumen) | {"maximo_conciliado_usd": str(maximo) if maximo is not None else None}, detalle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casos", required=True, type=Path, help="Manifiesto privado de la ejecución")
    parser.add_argument("--uso", required=True, action="append", type=Path, help="Log de uso (repetible)")
    parser.add_argument("--recibos", action="append", type=Path, default=[],
                        help="Respuesta JSON de /generation o lista de respuestas (repetible)")
    parser.add_argument("--detalle", required=True, type=Path, help="Archivo privado nuevo para el detalle")
    args = parser.parse_args()
    try:
        casos = json.loads(args.casos.read_text(encoding="utf-8"))["casos"]
        resumen, detalle = conciliar(casos, leer_uso(args.uso), leer_recibos(args.recibos))
        with open(args.detalle, "x", encoding="utf-8",
                  opener=lambda ruta, flags: os.open(ruta, flags, 0o600)) as archivo:
            json.dump(detalle, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(2, f"Conciliación incompleta: {error}\n")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    return int(any(resumen.get(k) for k in (
        "casos_con_consumo_pendiente", "casos_sobre_el_tope", "casos_sin_puntuar", "casos_con_diferencias",
        "casos_sin_puntuar_recuento", "casos_con_diferencias_recuento",
    )))


if __name__ == "__main__":
    raise SystemExit(main())
