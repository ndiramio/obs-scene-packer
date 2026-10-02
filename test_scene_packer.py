import json
from pathlib import Path
import shutil
import tempfile
import unittest
from scene_packer import pack, rebase

class PackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def fixture(self):
        a = self.root / 'a'; b = self.root / 'b'
        a.mkdir(); b.mkdir()
        (a / 'logo.png').write_bytes(b'first')
        (b / 'logo.png').write_bytes(b'second')
        data = {'name':'Demo', 'sources':[
            {'name':'Logo','uuid':'logo','id':'image_source','settings':{'file':str(a / 'logo.png')}},
            {'name':'Other','id':'image_source','settings':{'file':str(b / 'logo.png')}},
            {'name':'Nested','id':'scene','settings':{'items':[{'source_uuid':'logo'}]}},
            {'name':'Main','id':'scene','settings':{'items':[{'name':'Nested'},{'name':'Other'},{'name':'Group'}]}},
            {'name':'Second','id':'scene','settings':{'items':[{'name':'Logo'}]}},
            {'name':'Web','id':'browser_source','settings':{'url':'https://example.com'}}],
            'groups':[{'name':'Group','id':'group','settings':{'items':[{'name':'Logo'}]}}],
            'transitions':[{'name':'Wipe','settings':{'path':str(b / 'logo.png')}}],
            'custom':{'unchanged':True}}
        f = self.root / 'input.json'; f.write_text(json.dumps(data))
        return f, data
    def test_pack_and_relocate(self):
        f, original = self.fixture(); package = self.root / 'package'
        result = pack(f, package)
        data = json.loads(result.read_text()); manifest = json.loads((package / 'manifest.json').read_text())
        self.assertEqual(len(manifest['assets']), 2)
        self.assertTrue(any('/Shared/' in str(package / a['relative']) for a in manifest['assets']))
        self.assertEqual(json.loads(f.read_text()), original)
        self.assertEqual(data['custom'], original['custom'])
        self.assertEqual(data['sources'][-1]['settings']['url'], 'https://example.com')
        self.assertNotEqual(data['sources'][0]['settings']['file'], data['sources'][1]['settings']['file'])
        moved = self.root / 'moved'; shutil.move(str(package), moved)
        rewritten = json.loads(rebase(moved).read_text())
        for source in rewritten['sources'][:2]:
            path = Path(source['settings']['file'])
            self.assertTrue(path.exists()); self.assertIn(moved.resolve(), path.parents)
    def test_missing_does_not_create_package(self):
        f, data = self.fixture(); data['sources'][0]['settings']['file'] = str(self.root / 'missing.png')
        f.write_text(json.dumps(data)); out = self.root / 'package'
        with self.assertRaises(ValueError): pack(f, out)
        self.assertFalse(out.exists())
    def test_existing_destination_refused(self):
        f, _ = self.fixture()
        with self.assertRaises(ValueError): pack(f, self.root)
    def test_directories_uris_and_filters(self):
        assets = self.root / 'slides'; assets.mkdir(); (assets / '1.png').write_bytes(b'1')
        f = self.root / 'input.json'
        f.write_text(json.dumps({'name':'Slides','sources':[{'name':'Slide','settings':{'files':[{'value':str(assets)}]},'filters':[{'settings':{'file':(assets / '1.png').as_uri()}}]}]}))
        result = pack(f, self.root / 'packed'); data = json.loads(result.read_text())
        self.assertTrue(Path(data['sources'][0]['settings']['files'][0]['value']).is_dir())
        self.assertTrue(data['sources'][0]['filters'][0]['settings']['file'].startswith('file://'))
    def test_destination_inside_asset_rejected(self):
        f = self.root / 'input.json'
        f.write_text(json.dumps({'sources':[{'settings':{'file':str(self.root)}}]}))
        with self.assertRaises(ValueError): pack(f, self.root / 'packed')

if __name__ == '__main__': unittest.main()
