"""Reproduce las últimas reglas del ensayo de visión con lecturas archivadas."""

import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from unicodedata import normalize, combining


TOPE = Decimal("0.01")
VOLUMINOSOS = "retiro_muebles"
VEGETAL = re.compile(r"\b(?:hojas?|ramas?|frondas?|tallos?|pencas?|pastos?|vegetal|palmera)\b")
PISO_PUBLICO = re.compile(r"\b(?:vereda|calzada|cordon|paso peatonal|via publica|calle|senda|cruce)\b")
BALCON = re.compile(r"\b(?:pretil|baranda) (?:del|de un) balcon\b")
INTERIOR = re.compile(r"\b(?:interior|dentro de|balcon|terraza|ambiente|recinto|habitacion)\b")
SUPERFICIE = re.compile(r"\b(?:vereda|calzada|pavimento|suelo|asfalto|adoquin\w*|baldosas?)\b")
ESQUINA = re.compile(r"\b(?:esquina|angulo|encuentro|cruce)\b")
CANTO = re.compile(r"\b(?:lateral|borde|tabla|tablero|panel|canto)\b")
RIGIDAS = {"marco_cajon_o_mueble", "otro_rigido"}


def texto(valor):
    return "".join(c for c in normalize("NFD", valor.casefold()) if not combining(c))


def revisar_poda(descripcion):
    """La mención de un árbol por sí sola no abre la revisión de las bolsas."""
    return bool(VEGETAL.search(texto(descripcion)))


def piso_privado(descripcion, alcance):
    if PISO_PUBLICO.search(texto(descripcion)):
        return False
    if BALCON.search(texto(descripcion)):
        return True
    if alcance is None or alcance["ubicacion"] != "privada":
        return False
    prueba = texto(alcance["evidencia_acopio"])
    return bool(INTERIOR.search(prueba) and not PISO_PUBLICO.search(prueba))


def comparte_cuerpo(caja, detecciones):
    area = (caja[2] - caja[0]) * (caja[3] - caja[1])
    if area <= 0:
        raise ValueError("La región localizada no tiene área")
    for figura in detecciones:
        if "dumpster" not in figura["texto"] or figura["confianza"] < 0.28:
            continue
        x0, y0, x1, y1 = figura["caja"]
        ancho = max(0, min(caja[2], x1) - max(caja[0], x0))
        alto = max(0, min(caja[3], y1) - max(caja[1], y0))
        if ancho * alto >= 0.9 * area and caja[3] <= y1 - 0.05 * (y1 - y0):
            return True
    return False


def confirma_pieza_rigida(localizada, lectura):
    caja, clase = localizada["objetos"][0]
    return bool(
        "drawer" in clase
        and lectura["pieza"] in RIGIDAS
        and lectura["canto"] == "dos_paredes_rigidas"
        and ESQUINA.search(texto(lectura["evidencia_canto"]))
        and lectura["apoyo"] == "pavimento"
        and SUPERFICIE.search(texto(lectura["evidencia_apoyo"]))
        and not comparte_cuerpo(caja, localizada["detecciones"])
    )


def corrobora_pieza_rigida(lectura):
    return bool(
        lectura["pieza"] in RIGIDAS
        and lectura["canto"] in {"dos_paredes_rigidas", "tabla_gruesa_o_melamina"}
        and CANTO.search(texto(lectura["evidencia_canto"]))
        and lectura["apoyo"] == "pavimento"
        and SUPERFICIE.search(texto(lectura["evidencia_apoyo"]))
    )


def errores(etiquetas, referencia):
    return {clave for clave, esperado in referencia.items() if esperado is not None and (clave in etiquetas) != esperado}


def monto(valor):
    precio = Decimal(valor)
    if not precio.is_finite() or not 0 <= precio <= TOPE:
        raise ValueError("Importe simulado fuera del tope por foto")
    return precio


def reproducir(banco, base, vereda, poda, indice, lecturas, localizaciones, controles):
    if banco.keys() != base.keys() or not (vereda.keys() | poda.keys() | indice.keys() | controles) <= banco.keys():
        raise ValueError("El banco y sus lecturas tienen fotos distintas")
    puntuacion = Counter()
    maximo = Decimal(0)
    ahorro = Decimal(0)
    for clave, foto in banco.items():
        descripcion = foto["lectura"]["evidencia"]
        anterior = base[clave]
        iniciales = set(anterior["etiquetas"])
        etiquetas = iniciales.copy()
        costo_inicial = monto(anterior["presupuesto_usd"])
        costo = costo_inicial
        referencia = foto["esperado"]
        if set(anterior["errores"]) != errores(iniciales, referencia):
            raise ValueError("Las etiquetas revisadas no coinciden con la base")

        if clave in vereda:
            consulta = vereda[clave]
            if not piso_privado(descripcion, consulta["alcance"]):
                raise ValueError("La omisión de vereda no tiene evidencia de ámbito privado")
            costo -= monto(consulta["costo_omitido"])
            puntuacion["omitidas_vereda"] += 1

        if clave in poda and not revisar_poda(descripcion):
            consulta = poda[clave]
            if consulta["agrega"] or consulta["quita"]:
                raise ValueError("La consulta de poda modificó una categoría")
            costo -= monto(consulta["reserva_usd"])
            puntuacion["omitidas_poda"] += 1

        if clave in indice and indice[clave]["en_ruta"]:
            principal, verificacion = lecturas[clave]
            if VOLUMINOSOS not in etiquetas and confirma_pieza_rigida(localizaciones[clave], principal):
                if not corrobora_pieza_rigida(verificacion):
                    raise ValueError("Falta corroboración independiente de la pieza")
                etiquetas.add(VOLUMINOSOS)
                puntuacion["piezas_rigidas_recuperadas"] += 1

        costo = monto(costo)
        if clave in controles and etiquetas != iniciales:
            raise ValueError("Se modificaron las categorías de un control")
        diferencias = errores(etiquetas, referencia)
        puntuacion["fotos"] += 1
        puntuacion["etiquetas_revisadas"] += sum(x is not None for x in referencia.values())
        puntuacion["fotos_exactas"] += not diferencias
        puntuacion["diferencias"] += len(diferencias)
        puntuacion["diferencias_nuevas"] += len(diferencias - set(anterior["errores"]))
        ahorro += costo_inicial - costo
        maximo = max(costo, maximo)

    return dict(puntuacion) | {
        "controles": len(controles),
        "ahorro_simulado_usd": str(ahorro),
        "costo_maximo_simulado_usd": str(maximo),
    }


def verificar_recorte(raiz, metadatos, original):
    nombre = Path(metadatos["foto"])
    if nombre.name != str(nombre):
        raise ValueError("El nombre del recorte incluye una ruta")
    originales = {original["origen_sha256"], original["entrada_sha256"]}
    if metadatos["origen_sha256"] not in originales:
        raise ValueError("El recorte corresponde a otra foto")
    if sha256((raiz / nombre).read_bytes()).hexdigest() != metadatos["entrada_sha256"]:
        raise ValueError("La huella del recorte cambió")


def reproducir_archivo(raiz, lector, corroborador):
    def abrir(ruta):
        return json.loads((raiz / ruta).read_text(encoding="utf-8"))

    banco = {r["id"]: r for r in abrir("replay-conjunto/banco-834/comparacion.private.json")["fotos"]}
    base = abrir("recoleccion-apoyo-y-uso/reproduccion-final.private.json")["fotos"]
    vereda = abrir("rescate-lecturas-guardadas/alcance-pavimento.private.json")["omitidas"]
    registro_poda = abrir("poda-en-la-boca/protocolo.private.json")
    poda = {
        r["id"]: r | {"reserva_usd": registro_poda["reserva_usd"]}
        for r in abrir("poda-en-la-boca/reproduccion.private.json")["lecturas"]
    }
    recortes_poda = {r["id"]: r for r in registro_poda["fotos"]}
    if poda.keys() != recortes_poda.keys():
        raise ValueError("Faltan lecturas archivadas de poda")
    for i, recorte in recortes_poda.items():
        verificar_recorte(raiz / "poda-en-la-boca/fotos", recorte, banco[i])
    indice = abrir("sustitucion-por-evidencia/uniones/protocolo.private.json")["fotos"]
    respuestas = abrir("sustitucion-por-evidencia/uniones/resultado.private.json")["modelos"]
    if lector == corroborador:
        raise ValueError("La corroboración requiere una lectura independiente")
    for rol in (lector, corroborador):
        if respuestas[rol].keys() != indice.keys():
            raise ValueError("Faltan lecturas de estructura")
    for i, recorte in indice.items():
        verificar_recorte(raiz / "sustitucion-por-evidencia/uniones/fotos", recorte, banco[i])
    lecturas = {i: (respuestas[lector][i]["a"], respuestas[corroborador][i]["a"]) for i in indice}
    ubicaciones = abrir("voluminosos-por-sitio/deteccion.private.json")["fotos"]
    controles = set(abrir("margen-poda/reproduccion.private.json")["controles"])
    if len(banco) != 834 or len(controles) != 6 or not controles <= banco.keys():
        raise ValueError("El archivo no corresponde al banco y los controles de este ensayo")
    return reproducir(banco, base, vereda, poda, indice, lecturas, ubicaciones, controles)


def main():
    argumentos = argparse.ArgumentParser(description=__doc__)
    argumentos.add_argument("--evidencia", required=True, type=Path)
    argumentos.add_argument("--lector", required=True)
    argumentos.add_argument("--corroborador", required=True)
    args = argumentos.parse_args()
    try:
        resumen = reproducir_archivo(args.evidencia, args.lector, args.corroborador)
    except (OSError, KeyError, TypeError, ValueError, ArithmeticError) as error:
        argumentos.exit(2, f"Reproducción incompleta: {error}\n")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
