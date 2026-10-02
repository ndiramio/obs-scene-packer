import json
import plistlib
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from urllib.parse import unquote, urlsplit
from scene_packer import pack, rebase, font_names

class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
    def tearDown(self): self.temp.cleanup()
    def collection(self, html):
        path = self.root / 'collection.json'
        path.write_text(json.dumps({'name':'Web','sources':[{'name':'Overlay','id':'browser_source','settings':{'local_file':str(html)}}]}))
        return path
    def test_recursive_web_dependencies_and_relocation(self):
        web = self.root / 'web'; web.mkdir()
        (web / 'logo 1.png').write_bytes(b'logo')
        (web / 'font.woff2').write_bytes(b'font')
        (web / 'app.js').write_text('fetch("dynamic.json")')
        (web / 'more.css').write_text('@import "main.css"; @font-face{src:url(font.woff2)}')
        (web / 'main.css').write_text('@import "more.css"; body{background:url("logo%201.png?x=1#pic")}')
        html = web / 'index.html'
        html.write_text('<!DOCTYPE html><link href="main.css"><img src="logo%201.png" srcset="logo%201.png 1x, logo%201.png 2x"><script src="app.js"></script><style>.x{background:url(logo%201.png)}</style><div style="background:url(logo%201.png)"></div><img src="https://example.com/live.png">')
        package = self.root / 'package'; pack(self.collection(html), package, font_dirs=[], plugin_dirs=[])
        manifest = json.loads((package / 'manifest.json').read_text())
        bundle = manifest['browser_dependencies'][0]
        self.assertEqual(len(bundle['files']), 6)
        self.assertTrue(any('JavaScript' in w for w in bundle['warnings']))
        self.assertTrue(any('Remote resource' in w for w in bundle['warnings']))
        moved = self.root / 'moved'; shutil.move(package, moved); data = json.loads(rebase(moved).read_text())
        entry = Path(data['sources'][0]['settings']['local_file'])
        rewritten = entry.read_text()
        self.assertNotIn(str(web), rewritten)
        for asset in bundle['files']: self.assertTrue((moved / asset['relative']).exists())
        import re
        stylesheet = re.search('href="([^"]+)"', rewritten).group(1)
        css_file = entry.parent / unquote(urlsplit(stylesheet).path)
        self.assertTrue(css_file.is_file())
        for url in re.findall(r'url\("([^"]+)"\)', css_file.read_text()):
            self.assertTrue((css_file.parent / unquote(urlsplit(url).path)).is_file())
    def test_missing_web_dependency_cleans_staging(self):
        html = self.root / 'index.html'; html.write_text('<img src="missing.png">')
        out = self.root / 'package'
        with self.assertRaisesRegex(ValueError, 'Missing local browser'): pack(self.collection(html), out, font_dirs=[], plugin_dirs=[])
        self.assertFalse(out.exists()); self.assertEqual(list(self.root.glob('.scene-packer-*')), [])
    def test_base_href_rejected(self):
        html = self.root / 'index.html'; html.write_text('<base href="https://example.com/">')
        with self.assertRaisesRegex(ValueError, 'base href'): pack(self.collection(html), self.root / 'package', font_dirs=[], plugin_dirs=[])
    def test_font_inventory_and_opt_in_copy(self):
        fonts = self.root / 'fonts'; fonts.mkdir()
        name = 'Fixture Sans'.encode('utf-16-be')
        table = struct.pack('>HHH',0,1,18) + struct.pack('>HHHHHH',3,1,0x409,1,len(name),0) + name
        raw = struct.pack('>IHHHH',0x10000,1,16,0,0) + struct.pack('>4sIII',b'name',0,28,len(table)) + table
        font = fonts / 'UnrelatedFilename.ttf'; font.write_bytes(raw)
        self.assertIn('fixture sans', font_names(font))
        collection = self.root / 'collection.json'
        collection.write_text(json.dumps({'name':'Text','sources':[{'id':'text_ft2_source','name':'Title','settings':{'font':{'face':'Fixture Sans','style':'Bold'}}}]}))
        pack(collection, self.root / 'report-only', font_dirs=[fonts], plugin_dirs=[])
        report = json.loads((self.root / 'report-only/requirements.json').read_text())
        self.assertNotIn('relative', report['fonts'][0]['matches'][0])
        pack(collection, self.root / 'copied', True, [fonts], [])
        report = json.loads((self.root / 'copied/requirements.json').read_text())
        self.assertEqual((self.root / 'copied' / report['fonts'][0]['matches'][0]['relative']).read_bytes(), raw)
    def test_plugin_versions_and_filter_ids(self):
        plugins = self.root / 'plugins'; contents = plugins / 'Example.plugin/Contents'; contents.mkdir(parents=True)
        with (contents / 'Info.plist').open('wb') as f: plistlib.dump({'CFBundleIdentifier':'org.example.plugin','CFBundleShortVersionString':'1.2.3'},f)
        collection = self.root / 'collection.json'
        collection.write_text(json.dumps({'sources':[{'id':'example-source','name':'Source','settings':{},'filters':[{'id':'example-filter','name':'Filter','settings':{}}]}]}))
        pack(collection, self.root / 'package', font_dirs=[], plugin_dirs=[plugins])
        report = json.loads((self.root / 'package/requirements.json').read_text())
        self.assertEqual(report['installed_user_plugins'][0]['version'], '1.2.3')
        self.assertEqual({r['id'] for r in report['required_source_and_filter_types']}, {'example-source','example-filter'})
        self.assertFalse((self.root / 'package/plugins').exists())

if __name__ == '__main__': unittest.main()
