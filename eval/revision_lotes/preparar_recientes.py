"""Prepara una bandeja privada de fotos recientes, sin inferencias (#139)."""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from PIL import Image, ImageOps


def preparar(db_path, fotos_dir, destino, limite=50, dias=14, excluir=None):
    if not 1 <= limite <= 500 or dias < 1:
        raise ValueError('Usá entre 1 y 500 fotos y al menos un día.')
    db_path, fotos_dir, destino = map(lambda p: Path(p).resolve(), (db_path, fotos_dir, destino))
    if destino.exists():
        raise ValueError('Elegí una carpeta nueva para conservar las bandejas anteriores.')
    excluded = set()
    if excluir:
        for row in json.loads(Path(excluir).read_text())['fotos']:
            excluded.update(v for k, v in row.items() if k in ('sha256', 'sha256_api'))
    since = int((datetime.now(timezone.utc) - timedelta(days=dias)).timestamp() * 1000)
    db = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
    try:
        records = db.execute('SELECT photo_path, MAX(created_at) AS fecha FROM message_history WHERE photo_path IS NOT NULL AND created_at >= ? GROUP BY photo_path ORDER BY fecha DESC', (since,)).fetchall()
    finally:
        db.close()
    accepted, seen = [], set(excluded)
    for filename, received in records:
        source = Path(filename)
        if not source.is_absolute():
            source = db_path.parent / source
        source = source.resolve()
        if not source.is_relative_to(fotos_dir) or not source.is_file():
            continue
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            continue
        try:
            with Image.open(source) as image:
                normalized = ImageOps.exif_transpose(image).convert('RGB')
                normalized.thumbnail((2048, 2048))
                import io
                output = io.BytesIO()
                normalized.save(output, 'JPEG', quality=92)
                api_raw = output.getvalue()
        except (OSError, ValueError):
            continue
        api_digest = hashlib.sha256(api_raw).hexdigest()
        if api_digest in seen:
            continue
        seen.update((digest, api_digest))
        accepted.append((source, raw, digest, api_raw, api_digest, received))
        if len(accepted) >= limite:
            break
    if not accepted:
        raise ValueError('No hay fotos recientes nuevas con archivos disponibles.')
    delivery = destino / 'entrega'
    analysis = destino / 'analisis'
    (delivery / 'fotos').mkdir(parents=True, mode=0o700)
    (delivery / 'entradas-api').mkdir(mode=0o700)
    analysis.mkdir(mode=0o700)
    photos, private, inputs = [], [], {}
    for n, (source, raw, digest, api_raw, api_digest, received) in enumerate(accepted, 1):
        identifier = f'R{n:04d}'
        original_rel = f'fotos/{identifier}{source.suffix.lower()}'
        api_rel = f'entradas-api/{identifier}.jpg'
        (delivery / original_rel).write_bytes(raw)
        (delivery / api_rel).write_bytes(api_raw)
        date = datetime.fromtimestamp(received / 1000, timezone.utc).isoformat()
        row = dict(foto=identifier, archivo=original_rel, entrada_api=api_rel, sha256=digest, sha256_api=api_digest, fecha=date)
        photos.append(row)
        private.append(dict(row, origen=str(source), particion='desarrollo'))
        inputs[identifier] = dict(archivo=api_rel, sha256=api_digest, original_sha256=digest)
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        path.chmod(0o600)
    write(delivery / 'manifest.json', dict(cantidad=len(photos), fotos=photos, tipo='revision_manual_reciente'))
    write(destino / 'manifest-privado.json', dict(fotos=private, exclusiones=bool(excluir), dias=dias))
    write(analysis / 'entradas-api.json', inputs)
    write(analysis / 'estado.json', dict(estado='manual', resultados=[], gasto_observado_usd=0))
    return dict(fotos=len(photos), destino=str(destino), sin_inferencias=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--fotos', required=True)
    parser.add_argument('--destino', required=True)
    parser.add_argument('--limite', type=int, default=50)
    parser.add_argument('--dias', type=int, default=14)
    parser.add_argument('--excluir-manifest')
    args = parser.parse_args()
    print(json.dumps(preparar(args.db, args.fotos, args.destino, args.limite, args.dias, args.excluir_manifest), ensure_ascii=False))
