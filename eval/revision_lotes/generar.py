"""Informe de aprobación y corrección de sugerencias por foto (#42)."""
import argparse,hashlib,json
from pathlib import Path

def generar(base):
    base=Path(base);out=base/'entrega';manifest=json.loads((out/'manifest.json').read_text());photos=manifest['fotos'];state_path=base/'analisis/estado.json';state=json.loads(state_path.read_text()) if state_path.exists() else {'estado':'preparado','resultados':[],'gasto_observado_usd':0}
    identity=[{k:p[k] for k in ('foto','sha256','sha256_api')} for p in photos];dataset=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    records={}
    for p in photos:
        f=base/'analisis'/f"{p['foto']}-alto.json"
        if f.exists():
            raw=f.read_bytes();r=json.loads(raw);assert r['foto']==p['foto'] and r['modo']=='alto'
            records[p['foto']]={'respuesta':r['resultado'],'huella':hashlib.sha256(raw).hexdigest(),'fecha':r['fecha']}
    categories=json.loads((Path(__file__).resolve().parents[2]/'categorias.json').read_text())
    data={'version':2,'conjunto':dataset,'fotos':photos,'resultados':records,'categorias':categories,'proceso':{k:state.get(k) for k in ('estado','motivo_pausa','gasto_observado_usd','reserva_incierta_usd','reserva_acotada_usd')},'cantidad':len(photos)}
    bridge=base/'analisis/reanalisis-config.json'
    if bridge.exists():
        cfg=json.loads(bridge.read_text())
        data['reanalisis']={'modo_version':cfg['modo_version'],'url':f"http://127.0.0.1:{cfg['puerto']}/{cfg['token']}"}
    template=(Path(__file__).parent/'pagina.html').read_text();css=(Path(__file__).parent/'pagina.css').read_text();js=(Path(__file__).parent/'pagina.js').read_text()
    page=template.replace('/* ESTILOS */',css).replace('/* APLICACION */',js).replace('DATOS_JSON',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
    out.mkdir(exist_ok=True);temp=out/'revision.html.tmp';temp.write_text(page);temp.replace(out/'revision.html')
    target_file=base/'analisis/destino.json'
    if target_file.exists():
        target=Path(json.loads(target_file.read_text())['carpeta']);temp=target/'revision.html.tmp';temp.write_text(page);temp.replace(target/'revision.html')
    return {'fotos':len(photos),'resultados':len(records),'conjunto':dataset}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('base');args=parser.parse_args();print(json.dumps(generar(args.base),ensure_ascii=False))
