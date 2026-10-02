import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest
from unittest.mock import patch
from scene_packer import pack, install_plugins, binary_architectures

class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.plugins = self.root / 'source-plugins'; self.plugins.mkdir()
        self.collection = self.root / 'collection.json'; self.collection.write_text('{"name":"Demo","sources":[]}')
        self.package = self.root / 'package'; self.destination = self.root / 'installed'
    def tearDown(self): self.temp.cleanup()
    def bundle(self, name='Example', cpu=0x0100000C, identifier=None):
        root = self.plugins / (name + '.plugin'); executable = root / 'Contents/MacOS' / name
        executable.parent.mkdir(parents=True)
        executable.write_bytes(struct.pack('<IIII',0xFEEDFACF,cpu,0,0))
        executable.chmod(0o755)
        with (root / 'Contents/Info.plist').open('wb') as file:
            plistlib.dump({'CFBundleExecutable':name, 'CFBundleIdentifier':identifier or 'org.example.'+name, 'CFBundleShortVersionString':'1.0'}, file)
        resources=root / 'Contents/Resources'; resources.mkdir(); (resources / 'image.png').write_bytes(b'pixels')
        (resources / 'alias.png').symlink_to('image.png')
        return root
    def gather(self):
        pack(self.collection, self.package, font_dirs=[], plugin_dirs=[self.plugins], include_plugins=True)
    def install(self):
        with patch('platform.system', return_value='Darwin'), patch('platform.machine', return_value='arm64'):
            return install_plugins(self.package, self.destination)
    def test_copy_install_and_repeat(self):
        self.bundle(); self.gather()
        result = self.install(); self.assertIn('Installed 1', result)
        installed = self.destination / 'Example.plugin'
        self.assertTrue((installed / 'Contents/Resources/alias.png').is_symlink())
        self.assertEqual((installed / 'Contents/MacOS/Example').stat().st_mode & 0o777, 0o755)
        self.assertIn('already present: Example.plugin', self.install())
    def test_different_existing_bundle_is_not_overwritten(self):
        self.bundle(); self.gather(); self.install()
        installed = self.destination / 'Example.plugin/Contents/Resources/image.png'
        installed.write_bytes(b'keep me')
        with self.assertRaisesRegex(ValueError, 'Installed plugin differs'): self.install()
        self.assertEqual(installed.read_bytes(), b'keep me')
    def test_architecture_mismatch_writes_nothing(self):
        self.bundle(cpu=0x01000007); self.gather()
        with self.assertRaisesRegex(ValueError, 'does not support'): self.install()
        self.assertFalse(self.destination.exists())
    def test_modified_package_refused(self):
        self.bundle(); self.gather()
        (self.package / 'plugins/Example.plugin/Contents/Resources/image.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed or is incomplete'): self.install()
        self.assertFalse(self.destination.exists())
    def test_external_symlink_rejected(self):
        bundle = self.bundle(); outside = self.root / 'outside'; outside.write_text('secret')
        (bundle / 'Contents/Resources/external').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'external symlink'): self.gather()
        self.assertFalse(self.package.exists())
    def test_duplicate_identifiers_refused_before_copy(self):
        self.bundle('First', identifier='org.example.same'); self.bundle('Second', identifier='org.example.same'); self.gather()
        with self.assertRaisesRegex(ValueError, 'identifier already installed'): self.install()
        self.assertFalse(self.destination.exists())
    def test_failure_rolls_back_new_installations(self):
        self.bundle('First'); self.bundle('Second'); self.gather()
        import os
        rename = os.rename
        def fail_second(source, target):
            if Path(target).name == 'Second.plugin': raise OSError('fixture failure')
            return rename(source, target)
        with patch('os.rename', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'fixture failure'): self.install()
        self.assertEqual(list(self.destination.iterdir()), [])
    def test_universal_binary_architectures(self):
        file = self.root / 'fat'
        file.write_bytes(struct.pack('>II',0xCAFEBABE,2)+struct.pack('>IIIII',0x01000007,0,0,0,0)+struct.pack('>IIIII',0x0100000C,0,0,0,0))
        self.assertEqual(binary_architectures(file), ['arm64','x86_64'])

if __name__ == '__main__': unittest.main()
