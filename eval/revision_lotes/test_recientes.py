"""Verifica la preparación privada sin red ni inferencias."""
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from PIL import Image
from preparar_recientes import preparar


class RecientesTests(unittest.TestCase):
    def test_excluye_duplicados_fuera_de_carpeta_y_conjunto_anterior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos = root / 'photos'
            photos.mkdir()
            for name, color in [('uno.jpg', 'red'), ('dos.jpg', 'blue'), ('tres.jpg', 'green')]:
                Image.new('RGB', (80, 60), color).save(photos / name)
            (photos / 'copia.jpg').write_bytes((photos / 'uno.jpg').read_bytes())
            Image.new('RGB', (80, 60), 'black').save(root / 'fuera.jpg')
            (photos / 'escape.jpg').symlink_to(root / 'fuera.jpg')
            dbfile = root / 'bot.db'
            db = sqlite3.connect(dbfile)
            db.execute('CREATE TABLE message_history(photo_path TEXT, created_at INTEGER)')
            now = int(time.time() * 1000)
            db.executemany('INSERT INTO message_history VALUES (?,?)', [(str(photos / name), now-i) for i, name in enumerate(['uno.jpg', 'copia.jpg', 'dos.jpg', 'tres.jpg', 'escape.jpg'])])
            db.commit()
            db.close()
            excluded = root / 'anterior.json'
            excluded.write_text(json.dumps({'fotos': [{'sha256': hashlib.sha256((photos / 'dos.jpg').read_bytes()).hexdigest()}]}))
            target = root / 'nueva'
            result = preparar(dbfile, photos, target, excluir=excluded)
            self.assertEqual(result['fotos'], 2)
            public = json.loads((target / 'entrega/manifest.json').read_text())
            private = json.loads((target / 'manifest-privado.json').read_text())
            self.assertEqual([x['foto'] for x in public['fotos']], ['R0001', 'R0002'])
            self.assertTrue(all(x['particion'] == 'desarrollo' for x in private['fotos']))
            self.assertNotIn(str(root), json.dumps(public))
            self.assertTrue(all('origen' not in x for x in public['fotos']))
            self.assertFalse(list((target / 'analisis').glob('*-alto.json')))
            self.assertRaises(ValueError, preparar, dbfile, photos, target)
            for row in public['fotos']:
                raw = (target / 'entrega' / row['entrada_api']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row['sha256_api'])


if __name__ == '__main__':
    unittest.main()
