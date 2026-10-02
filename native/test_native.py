"""Run the existing behavior fixtures against the compiled C++ engine."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_scene_packer
import test_dependencies
import test_plugins

binary = Path(os.environ['PACKER_CLI']).resolve()
def run(*args):
    result = subprocess.run([str(binary), *map(str, args)], text=True, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()
def pack(collection, destination, include_fonts=False, font_dirs=None, plugin_dirs=None, include_plugins=False):
    args = ['pack', collection, destination]
    if include_fonts: args += ['--fonts']
    if include_plugins: args += ['--plugins']
    for folder in (font_dirs if font_dirs is not None else []): args += ['--font-dir', folder]
    for folder in (plugin_dirs if plugin_dirs is not None else []): args += ['--plugin-dir', folder]
    return Path(run(*args))
def rebase(package): return Path(run('rebase', package))
def install_plugins(package, destination=None):
    return run('install-plugins', package, *([destination] if destination is not None else []))
for module in (test_scene_packer, test_dependencies, test_plugins):
    module.pack = pack
    module.rebase = rebase
    module.install_plugins = install_plugins
# Python mocks cannot inject a rename failure into the native subprocess.
test_plugins.PluginTests.test_failure_rolls_back_new_installations = unittest.skip('Python os.rename mock does not affect the C++ process; native failure rollback is not fault-injected here')(test_plugins.PluginTests.test_failure_rolls_back_new_installations)
# Header inspection through Python was already tested; native architecture parsing is
# exercised by the native gather/install fixtures rather than this Python helper.
test_plugins.PluginTests.test_universal_binary_architectures = unittest.skip('Python-only helper; use native universal fixture')(test_plugins.PluginTests.test_universal_binary_architectures)

import json
import struct
import tempfile
from scene_packer import pack as python_pack
from scene_packer import rebase as python_rebase
class NativeCompatibilityTests(unittest.TestCase):
    def test_output_under_symlinked_asset_parent_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); media=root/'media'; media.mkdir(); alias=root/'alias'; alias.symlink_to(media,target_is_directory=True)
            collection=root/'input.json'; collection.write_text(json.dumps({'sources':[{'name':'Slides','settings':{'file':str(media)}}]}))
            with self.assertRaisesRegex(ValueError,'inside an asset directory'): pack(collection,alias/'package')
            self.assertFalse((media/'package').exists())
    def test_nested_asset_link_to_output_parent_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); media=root/'media'; media.mkdir(); (media/'parent').symlink_to(root,target_is_directory=True)
            collection=root/'input.json'; collection.write_text(json.dumps({'sources':[{'name':'Slides','settings':{'file':str(media)}}]}))
            with self.assertRaisesRegex(ValueError,'inside its source directory'): pack(collection,root/'package')
            self.assertFalse((root/'package').exists())
    def test_plugin_package_inside_source_bundle_refused(self):
        fixture=test_plugins.PluginTests(); fixture.setUp()
        try:
            bundle=fixture.bundle()
            with self.assertRaisesRegex(ValueError,'inside its source directory'):
                pack(fixture.collection,bundle/'package',plugin_dirs=[fixture.plugins],include_plugins=True)
            self.assertFalse((bundle/'package').exists())
        finally: fixture.tearDown()
    def test_root_asset_folder_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); collection=root/'input.json'
            collection.write_text(json.dumps({'sources':[{'name':'Root','settings':{'file':'/'}}]}))
            with self.assertRaisesRegex(ValueError,'inside an asset directory'): pack(collection,root/'package')
            self.assertFalse((root/'package').exists())
    def test_broken_plugin_link_refused(self):
        fixture=test_plugins.PluginTests(); fixture.setUp()
        try:
            bundle=fixture.bundle()
            (bundle/'Contents/Resources/broken').symlink_to('missing.png')
            with self.assertRaisesRegex(ValueError,'broken or external symlink'):
                pack(fixture.collection,fixture.package,plugin_dirs=[fixture.plugins],include_plugins=True)
            self.assertFalse(fixture.package.exists())
        finally: fixture.tearDown()
    def test_media_folder_symlinks_gather_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); media=root/'media'; media.mkdir()
            original=root/'original.png'; original.write_bytes(b'pixels')
            (media/'linked.png').symlink_to(original)
            collection=root/'input.json'; collection.write_text(json.dumps({'sources':[{'name':'Slides','settings':{'file':str(media)}}]}))
            output=pack(collection,root/'package')
            copied=Path(json.loads(output.read_text())['sources'][0]['settings']['file'])/'linked.png'
            self.assertFalse(copied.is_symlink())
            original.unlink()
            self.assertEqual(copied.read_bytes(),b'pixels')
    def test_media_folder_symlink_cycle_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); media=root/'media'; media.mkdir(); (media/'cycle').symlink_to(media)
            collection=root/'input.json'; collection.write_text(json.dumps({'sources':[{'name':'Slides','settings':{'file':str(media)}}]}))
            with self.assertRaisesRegex(ValueError,'symlink cycle'): pack(collection,root/'package')
            self.assertFalse((root/'package').exists())
    def test_python_package_native_plugin_restore(self):
        fixture = test_plugins.PluginTests()
        fixture.setUp()
        try:
            fixture.bundle()
            python_pack(fixture.collection, fixture.package, font_dirs=[], plugin_dirs=[fixture.plugins], include_plugins=True)
            self.assertIn('Installed 1', install_plugins(fixture.package, fixture.destination))
        finally: fixture.tearDown()
    def test_native_universal_bundle(self):
        fixture = test_plugins.PluginTests()
        fixture.setUp()
        try:
            root = fixture.bundle()
            binary_file = root / 'Contents/MacOS/Example'
            binary_file.write_bytes(struct.pack('>II',0xCAFEBABE,2)+struct.pack('>IIIII',0x01000007,0,0,0,0)+struct.pack('>IIIII',0x0100000C,0,0,0,0))
            pack(fixture.collection, fixture.package, font_dirs=[], plugin_dirs=[fixture.plugins], include_plugins=True)
            report = json.loads((fixture.package/'requirements.json').read_text())
            self.assertEqual(report['installed_user_plugins'][0]['architectures'], ['arm64','x86_64'])
            self.assertIn('Installed 1', install_plugins(fixture.package, fixture.destination))
        finally: fixture.tearDown()
    def test_inline_script_not_scanned_as_html_and_recovery_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html = root/'index.html'
            html.write_text("<script>const example = `<img src='not-a-real-file.png'>`;</script><!-- <img src='also-missing.png'> -->")
            collection=root/'input.json'
            original={'name':'Inline','sources':[{'name':'Web','id':'browser_source','settings':{'local_file':str(html)}}]}
            collection.write_text(json.dumps(original))
            pack(collection,root/'package')
            self.assertEqual(json.loads((root/'package/original-collection.json').read_text()),original)
            manifest=json.loads((root/'package/manifest.json').read_text())
            self.assertTrue(any('Inline JavaScript' in text for text in manifest['browser_dependencies'][0]['warnings']))
            import shutil
            shutil.move(root/'package',root/'moved')
            path=python_rebase(root/'moved')
            self.assertTrue(Path(json.loads(path.read_text())['sources'][0]['settings']['local_file']).exists())

if __name__ == '__main__':
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in (test_scene_packer, test_dependencies, test_plugins))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(NativeCompatibilityTests))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
