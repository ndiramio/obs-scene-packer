"""OBS Scene Packer: OBS script and standalone pack/rebase CLI. Python 3.8+."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import unquote, urlparse


def slug(value):
    text = re.sub(r'[^\w .-]', '_', value).strip(' .')[:80] or 'Unnamed'
    return text + '-' + hashlib.sha256(value.encode()).hexdigest()[:8]


def write_json(path, data):
    path = Path(path)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.packer-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def strings(node, trail=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from strings(v, trail + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from strings(v, trail + (i,))
    elif isinstance(node, str):
        yield trail, node


def put(node, trail, value):
    for key in trail[:-1]:
        node = node[key]
    node[trail[-1]] = value


def local_path(value, base, key):
    if value.startswith('file://'):
        parsed = urlparse(value)
        if parsed.netloc not in ('', 'localhost'):
            return None
        return Path(unquote(parsed.path))
    if '://' in value or '\n' in value or not value:
        return None
    if value.startswith(('/', '~/')):
        return Path(value).expanduser()
    if key in {'file', 'local_file', 'path', 'value', 'text_file', 'input'}:
        candidate = base / value
        if candidate.exists():
            return candidate
    return None


# Static browser dependencies are rewritten to relative URLs, so they survive moves.
def bundle_web(entry, target, staging):
    from html.parser import HTMLParser
    from html import unescape
    from urllib.parse import quote, urlsplit
    mapping = {entry.resolve(): target}
    records, warnings = [], set()
    dependency_dir = target.parent / (target.stem + '.dependencies')

    def transfer(url, origin, output):
        value = unescape(url.strip())
        parsed = urlsplit(value)
        if not value or value.startswith('#') or parsed.scheme not in ('', 'file') or parsed.netloc:
            if parsed.scheme in ('http', 'https') or parsed.netloc:
                warnings.add('Remote resource remains online: ' + value)
            return url
        dependency = Path(unquote(parsed.path))
        if not dependency.is_absolute():
            dependency = origin.parent / dependency
        dependency = dependency.resolve()
        if not dependency.is_file():
            raise ValueError('Missing local browser dependency: ' + str(dependency))
        if dependency not in mapping:
            mapping[dependency] = dependency_dir / (hashlib.sha256(str(dependency).encode()).hexdigest()[:12] + '-' + dependency.name)
            process(dependency, mapping[dependency])
        relative = quote(os.path.relpath(mapping[dependency], output.parent).replace(os.sep, '/'), safe='/')
        return relative + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')

    def css(text, origin, output):
        def replace_url(match):
            value = match.group(2).strip()
            return 'url("' + transfer(value, origin, output) + '")'
        text = re.sub(r'url\(\s*([\"\']?)(.*?)\1\s*\)', replace_url, text, flags=re.I)
        def replace_import(match):
            return '@import "' + transfer(match.group(2), origin, output) + '"'
        return re.sub(r'@import\s+([\"\'])(.*?)\1', replace_import, text, flags=re.I)

    class HTMLBundle(HTMLParser):
        def __init__(self, origin, output):
            super().__init__(convert_charrefs=False)
            self.origin, self.output, self.parts, self.style = origin, output, [], False
        def handle_starttag(self, tag, attrs):
            if tag == 'base' and dict(attrs).get('href'):
                raise ValueError('HTML base href is not supported; remove it or use relative URLs: ' + str(self.origin))
            raw = self.get_starttag_text()
            def attr(match):
                key, value = match.group(1), match.group(3) if match.group(2) else match.group(4)
                if key.lower() == 'style':
                    rewritten = css(unescape(value), self.origin, self.output)
                elif key.lower() == 'srcset':
                    if 'data:' in value:
                        warnings.add('Data URL srcset left unchanged: ' + str(self.origin))
                        return match.group(0)
                    candidates = []
                    for candidate in value.split(','):
                        bits = candidate.strip().split()
                        if bits:
                            candidates.append(transfer(bits[0], self.origin, self.output) + (' ' + ' '.join(bits[1:]) if len(bits) > 1 else ''))
                    rewritten = ', '.join(candidates)
                else:
                    rewritten = transfer(value, self.origin, self.output)
                from html import escape
                return key + '="' + escape(rewritten, quote=True) + '"'
            raw = re.sub(r'\b(srcset|src|href|poster|data|style)\s*=\s*(?:([\"\'])(.*?)\2|([^\s>]+))', attr, raw, flags=re.I|re.S)
            self.parts.append(raw)
            if tag == 'style': self.style = True
        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
        def handle_endtag(self, tag):
            self.parts.append('</' + tag + '>')
            if tag == 'style': self.style = False
        def handle_data(self, data):
            self.parts.append(css(data, self.origin, self.output) if self.style else data)
        def handle_entityref(self, name): self.parts.append('&' + name + ';')
        def handle_charref(self, name): self.parts.append('&#' + name + ';')
        def handle_comment(self, data): self.parts.append('<!--' + data + '-->')
        def handle_decl(self, decl): self.parts.append('<!' + decl + '>')
        def handle_pi(self, data): self.parts.append('<?' + data + '>')

    def process(origin, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        suffix = origin.suffix.lower()
        if suffix in ('.html', '.htm', '.css'):
            text = origin.read_text(encoding='utf-8-sig')
            if suffix == '.css':
                text = css(text, origin, output)
            else:
                parser = HTMLBundle(origin, output)
                parser.feed(text); parser.close(); text = ''.join(parser.parts)
            output.write_text(text, encoding='utf-8')
        else:
            shutil.copy2(origin, output)
            if suffix in ('.js', '.mjs'):
                warnings.add('JavaScript runtime loads and module imports need manual review: ' + str(origin))
        records.append({'original': str(origin), 'relative': output.relative_to(staging).as_posix()})
    process(entry.resolve(), target)
    return {'files': records, 'warnings': sorted(warnings)}


def font_names(path):
    """Read OpenType/TrueType name tables, including collection members."""
    import struct
    try:
        raw = path.read_bytes()
        if len(raw) > 32 * 1024 * 1024: return set()
        offsets = [0]
        if raw[:4] == b'ttcf':
            count = struct.unpack_from('>I', raw, 8)[0]
            if count > 256: return set()
            offsets = list(struct.unpack_from('>' + 'I' * count, raw, 12))
        names = set()
        for offset in offsets:
            count = struct.unpack_from('>H', raw, offset + 4)[0]
            if count > 256: continue
            for i in range(count):
                tag, _, start, length = struct.unpack_from('>4sIII', raw, offset + 12 + i * 16)
                if tag != b'name': continue
                _, entries, storage = struct.unpack_from('>HHH', raw, start)
                if entries > 4096: continue
                for j in range(entries):
                    platform, _, _, name_id, size, position = struct.unpack_from('>HHHHHH', raw, start + 6 + j * 12)
                    if name_id in (1, 4, 6, 16):
                        chunk = raw[start + storage + position:start + storage + position + size]
                        try: names.add(chunk.decode('utf-16-be' if platform in (0, 3) else 'mac_roman').casefold())
                        except UnicodeError: pass
        return names
    except (OSError, struct.error, ValueError):
        return set()


def dependency_report(data, staging, include_fonts=False, font_dirs=None, plugin_dirs=None):
    import plistlib
    required_fonts, types = {}, {}
    def inspect(node):
        if isinstance(node, dict):
            if isinstance(node.get('font'), dict) and node['font'].get('face'):
                face = node['font']['face']
                required_fonts.setdefault(face, set()).add(str(node['font'].get('style', 'Regular')))
            if node.get('id') and isinstance(node.get('settings'), dict):
                types.setdefault(str(node['id']), set()).add(str(node.get('name', 'Unnamed')))
            for value in node.values(): inspect(value)
        elif isinstance(node, list):
            for value in node: inspect(value)
    inspect(data)
    font_dirs = font_dirs if font_dirs is not None else [Path.home() / 'Library/Fonts', Path('/Library/Fonts'), Path('/System/Library/Fonts')]
    found = {face: [] for face in required_fonts}
    if required_fonts:
        for folder in font_dirs:
            folder = Path(folder).expanduser()
            if not folder.exists(): continue
            for file in sorted(folder.rglob('*')):
                if file.suffix.lower() not in ('.ttf', '.otf', '.ttc', '.otc') or not file.is_file(): continue
                names = font_names(file)
                for face in required_fonts:
                    if face.casefold() in names: found[face].append(file)
    fonts, copied_fonts = [], {}
    for face, styles in sorted(required_fonts.items()):
        matches = []
        for file in found[face]:
            item = {'original': str(file), 'system_font': str(file).startswith('/System/Library/')}
            if include_fonts and not item['system_font']:
                if file not in copied_fonts:
                    relative = Path('fonts') / (hashlib.sha256(str(file).encode()).hexdigest()[:12] + '-' + file.name)
                    (staging / relative).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, staging / relative)
                    copied_fonts[file] = relative.as_posix()
                item['relative'] = copied_fonts[file]
            matches.append(item)
        fonts.append({'family': face, 'styles': sorted(styles), 'matches': matches, 'status': 'found' if matches else 'not_found'})
    plugin_dirs = plugin_dirs if plugin_dirs is not None else [Path.home() / 'Library/Application Support/obs-studio/plugins', Path('/Library/Application Support/obs-studio/plugins')]
    plugins = []
    for folder in plugin_dirs:
        for bundle in sorted(Path(folder).expanduser().glob('*.plugin')):
            try:
                with (bundle / 'Contents/Info.plist').open('rb') as f: info = plistlib.load(f)
                plugins.append({'original': str(bundle.resolve()), 'bundle': bundle.name, 'identifier': info.get('CFBundleIdentifier'), 'version': info.get('CFBundleShortVersionString') or info.get('CFBundleVersion')})
            except (OSError, ValueError, plistlib.InvalidFileException):
                plugins.append({'original': str(bundle.resolve()), 'bundle': bundle.name, 'version': None})
    return {'fonts': fonts, 'required_source_and_filter_types': [{'id': key, 'sources': sorted(value)} for key, value in sorted(types.items())], 'installed_user_plugins': plugins,
            'instructions': ['Install copied fonts using Font Book before importing. Family names remain unchanged in OBS.', 'System fonts are inventoried but not copied. Font file copying is opt-in; only redistribute files your license permits.', 'Install matching macOS OBS plugins from their publishers. Bundle copying is opt-in; restoration requires a matching CPU architecture and an OBS restart. External runtimes may require the publisher installer.', 'Source/filter IDs include OBS built-ins; this report does not map IDs to plugin bundles or guarantee plugin completeness. Frontend-only plugins are not identifiable from collection JSON.']}


def binary_architectures(path):
    """Read Mach-O headers without executing the plugin."""
    import struct
    cpu_names = {0x01000007: 'x86_64', 0x0100000C: 'arm64'}
    try:
        with Path(path).open('rb') as file: raw = file.read(16384)
        magic = raw[:4]
        if magic in (b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe'):
            endian = '>' if magic[:2] == b'\xfe\xed' else '<'
            return [cpu_names.get(struct.unpack_from(endian + 'I', raw, 4)[0], 'unsupported')]
        if magic in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
            endian = '>' if magic[:2] == b'\xca\xfe' else '<'
            count = struct.unpack_from(endian + 'I', raw, 4)[0]
            if count > 128: return []
            stride = 32 if magic in (b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca') else 20
            return sorted({cpu_names.get(struct.unpack_from(endian + 'I', raw, 8 + index * stride)[0], 'unsupported') for index in range(count)})
    except (OSError, struct.error): pass
    return []


def validate_plugin_bundle(bundle):
    import plistlib
    bundle = Path(bundle).resolve()
    if not bundle.is_dir() or bundle.suffix != '.plugin':
        raise ValueError('Not a macOS plugin bundle: ' + str(bundle))
    for entry in bundle.rglob('*'):
        if entry.is_symlink():
            resolved = entry.resolve()
            if bundle not in resolved.parents or not resolved.exists():
                raise ValueError('Plugin has a broken or external symlink; use its installer: ' + str(entry))
    with (bundle / 'Contents/Info.plist').open('rb') as file:
        info = plistlib.load(file)
    executable = info.get('CFBundleExecutable', '')
    if not executable or Path(executable).name != executable or executable in ('.', '..'):
        raise ValueError('Invalid plugin executable: ' + str(bundle))
    binary = bundle / 'Contents/MacOS' / executable
    if not binary.is_file(): raise ValueError('Plugin executable missing: ' + str(binary))
    return binary_architectures(binary)


def bundle_digest(bundle):
    digest = hashlib.sha256()
    for entry in sorted(Path(bundle).rglob('*')):
        digest.update(entry.relative_to(bundle).as_posix().encode() + b'\0')
        if entry.is_symlink():
            digest.update(b'link\0' + os.readlink(entry).encode())
        elif entry.is_file():
            digest.update(b'file\0' + str(entry.stat().st_mode & 0o777).encode() + b'\0')
            with entry.open('rb') as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b''): digest.update(chunk)
        elif entry.is_dir(): digest.update(b'dir\0')
    return digest.hexdigest()


def gather_plugins(report, staging):
    names = set()
    for plugin in report['installed_user_plugins']:
        bundle = Path(plugin['original'])
        if bundle.name in names:
            raise ValueError('Duplicate plugin bundle name; select one --plugin-dir: ' + bundle.name)
        names.add(bundle.name)
        architectures = validate_plugin_bundle(bundle)
        if not architectures:
            raise ValueError('Cannot identify plugin architecture; use its installer: ' + bundle.name)
        relative = Path('plugins') / bundle.name
        (staging / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle, staging / relative, symlinks=True)
        plugin.update({'relative': relative.as_posix(), 'architectures': architectures, 'sha256': bundle_digest(staging / relative)})


def install_plugins(package, destination=None):
    """Install trusted package bundles without overwriting installed plugins."""
    import platform
    if platform.system() != 'Darwin':
        raise ValueError('Plugin restoration is macOS only.')
    architecture = platform.machine()
    root = Path(package).expanduser().resolve()
    report = json.loads((root / 'requirements.json').read_text())
    destination = Path(destination).expanduser() if destination is not None else Path.home() / 'Library/Application Support/obs-studio/plugins'
    destination = destination.resolve()
    pending, skipped, names, identifiers = [], [], set(), set()
    # Preflight everything before writing destination bundles.
    import plistlib
    for installed in destination.glob('*.plugin'):
        try:
            with (installed / 'Contents/Info.plist').open('rb') as file: info = plistlib.load(file)
            identifier = info.get('CFBundleIdentifier')
            if identifier: identifiers.add(identifier)
        except (OSError, ValueError, plistlib.InvalidFileException): pass
    for plugin in report.get('installed_user_plugins', []):
        if not plugin.get('relative'): continue
        source = (root / plugin['relative']).resolve()
        if root not in source.parents: raise ValueError('Plugin path escapes package.')
        architectures = validate_plugin_bundle(source)
        if architecture not in architectures:
            raise ValueError('Plugin ' + source.name + ' does not support this OBS/Python process architecture: ' + architecture)
        if bundle_digest(source) != plugin.get('sha256'):
            raise ValueError('Plugin bundle changed or is incomplete: ' + source.name)
        target = destination / source.name
        if source.name in names: raise ValueError('Duplicate packaged plugin: ' + source.name)
        names.add(source.name)
        if target.exists():
            if target.is_dir() and bundle_digest(target) == plugin['sha256']:
                skipped.append(source.name); continue
            raise ValueError('Installed plugin differs; nothing replaced: ' + str(target))
        identifier = plugin.get('identifier')
        if identifier and identifier in identifiers:
            raise ValueError('Plugin identifier already installed under another name: ' + identifier)
        if identifier: identifiers.add(identifier)
        if source == destination or source in destination.parents or destination in source.parents:
            raise ValueError('Plugin installation folder must be outside packaged bundles.')
        pending.append((source, target))
    if not pending:
        return 'No new bundled plugins to install; already present: ' + ', '.join(skipped)
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.scene-packer-install-', dir=str(destination)))
    installed = []
    try:
        for source, target in pending:
            shutil.copytree(source, staging / source.name, symlinks=True)
        for source, target in pending:
            if target.exists(): raise ValueError('Plugin appeared during installation: ' + str(target))
            os.rename(staging / source.name, target)
            installed.append(target)
    except Exception:
        for target in installed: shutil.rmtree(target)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return 'Installed ' + str(len(installed)) + ' plugin bundles. Restart OBS before importing the collection.'


def pack(collection, destination, include_fonts=False, font_dirs=None, plugin_dirs=None, include_plugins=False):
    collection = Path(collection).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError('Choose a new, nonexistent package folder.')
    data = json.loads(collection.read_text(encoding='utf-8'))
    sources = data.get('sources', []) + data.get('groups', [])
    by_name = {s.get('name'): s for s in sources}
    by_uuid = {s.get('uuid'): s for s in sources if s.get('uuid')}
    owners = {}
    scenes = [s for s in sources if s.get('id') == 'scene']
    def visit(source, owner, seen):
        identity = source.get('uuid') or source.get('name')
        if identity in seen:
            return
        seen = seen | {identity}
        owners.setdefault(id(source), set()).add(owner)
        for item in source.get('settings', {}).get('items', []):
            child = by_uuid.get(item.get('source_uuid')) or by_name.get(item.get('name'))
            if child:
                visit(child, owner, seen)
    for scene in scenes:
        visit(scene, scene.get('name', 'Scene'), set())
    references = []
    for source in sources + data.get('transitions', []):
        for trail, value in strings(source):
            if not trail or trail[0] not in ('settings', 'filters'):
                continue
            path = local_path(value, collection.parent, trail[-1])
            if path:
                references.append((source, trail, value, path.resolve(), owners.get(id(source), {'Collection'})))
    missing = sorted({str(r[3]) for r in references if not r[3].exists()})
    if missing:
        raise ValueError('Missing assets; nothing copied:\n' + '\n'.join(missing))
    for _, _, _, path, _ in references:
        if path.is_dir() and (destination == path or path in destination.parents):
            raise ValueError('Package must not be inside an asset directory.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.scene-packer-', dir=str(destination.parent)))
    manifest = {'version': 2, 'collection': 'collection.json', 'assets': [], 'references': [], 'browser_dependencies': []}
    copied = {}
    try:
        requirements = dependency_report(data, staging, include_fonts, font_dirs, plugin_dirs)
        if include_plugins:
            gather_plugins(requirements, staging)
        for source, trail, value, path, scene_owners in references:
            if path not in copied:
                all_owners = set().union(*(r[4] for r in references if r[3] == path))
                folder = 'Shared' if len(all_owners) > 1 else slug(next(iter(all_owners)))
                relative = Path('assets') / folder / (hashlib.sha256(str(path).encode()).hexdigest()[:12] + '-' + path.name)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.copytree(path, target)
                elif path.suffix.lower() in ('.html', '.htm'):
                    manifest['browser_dependencies'].append(bundle_web(path, target, staging))
                else:
                    shutil.copy2(path, target)
                copied[path] = relative.as_posix()
                manifest['assets'].append({'original': str(path), 'relative': relative.as_posix(), 'scenes': sorted(all_owners)})
            new = str(destination / copied[path])
            put(source, trail, Path(new).as_uri() if value.startswith('file://') else new)
            # Locate this source in the full collection, preserving all unrelated fields.
            for section in ('sources', 'groups', 'transitions'):
                for index, entry in enumerate(data.get(section, [])):
                    if entry is source:
                        manifest['references'].append({'trail': [section, index] + list(trail), 'relative': copied[path], 'uri': value.startswith('file://')})
        data['name'] = data.get('name', collection.stem) + ' Portable'
        write_json(staging / 'collection.json', data)
        manifest['font_files'] = sorted({match['relative'] for font in requirements['fonts'] for match in font['matches'] if 'relative' in match})
        write_json(staging / 'manifest.json', manifest)
        write_json(staging / 'requirements.json', requirements)
        os.rename(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination / 'collection.json'


def rebase(package):
    root = Path(package).expanduser().resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    data = json.loads((root / 'collection.json').read_text())
    extra_files = list(manifest.get('font_files', []))
    for browser in manifest.get('browser_dependencies', []):
        extra_files.extend(file['relative'] for file in browser['files'])
    for relative in extra_files:
        dependency = (root / relative).resolve()
        if root not in dependency.parents or not dependency.is_file():
            raise ValueError('Missing or unsafe package dependency: ' + str(dependency))
    for ref in manifest['references']:
        path = (root / ref['relative']).resolve()
        if root not in path.parents or not path.exists():
            raise ValueError('Missing or unsafe package asset: ' + str(path))
        put(data, ref['trail'], path.as_uri() if ref['uri'] else str(path))
    write_json(root / 'collection.json', data)
    return root / 'collection.json'


try:
    import obspython as obs
except ImportError:
    obs = None

_config = {}

def script_description():
    return 'Scene Packer: select a freshly exported OBS collection JSON and a NEW package folder. Pack creates scene folders and collection.json. After moving the package, select its folder and Rebase before importing. Apply updates matching sources in the currently loaded original collection. See README for limits.'


def script_update(settings):
    for key in ('collection', 'destination', 'package'):
        _config[key] = obs.obs_data_get_string(settings, key)
    _config['include_fonts'] = obs.obs_data_get_bool(settings, 'include_fonts')
    _config['include_plugins'] = obs.obs_data_get_bool(settings, 'include_plugins')


def report(action):
    try:
        result = action()
        obs.script_log(obs.LOG_INFO, 'Scene Packer: ' + str(result))
    except Exception as exc:
        obs.script_log(obs.LOG_ERROR, 'Scene Packer: ' + str(exc))
    return False


def pack_clicked(props, prop):
    return report(lambda: pack(_config['collection'], _config['destination'], _config.get('include_fonts', False), include_plugins=_config.get('include_plugins', False)))


def install_plugins_clicked(props, prop):
    def install():
        if obs.obs_frontend_streaming_active() or obs.obs_frontend_recording_active():
            raise ValueError('Stop streaming and recording before installing plugins.')
        return install_plugins(_config['package'])
    return report(install)


def rebase_clicked(props, prop):
    return report(lambda: rebase(_config['package']))


def apply_clicked(props, prop):
    def apply():
        if obs.obs_frontend_streaming_active() or obs.obs_frontend_recording_active():
            raise ValueError('Stop streaming and recording before replacing paths.')
        original = json.loads(Path(_config['collection']).read_text())
        if obs.obs_frontend_get_current_scene_collection() != original.get('name'):
            raise ValueError('Load the original collection before applying paths.')
        root = Path(_config['destination']).resolve()
        updated = json.loads(rebase(root).read_text())
        manifest = json.loads((root / 'manifest.json').read_text())
        changes = {}
        for ref in manifest['references']:
            section, index, *trail = ref['trail']
            if trail[0] != 'settings':
                raise ValueError('Filter assets require importing collection.json; live apply cancelled.')
            src = updated[section][index]
            changes.setdefault(src['name'], []).append((trail[1:], ref))
        pending = []
        try:
            for name, refs in changes.items():
                source = obs.obs_get_source_by_name(name)
                if source is None:
                    raise ValueError('Source missing: ' + name)
                pending.append((source, refs))
            for source, refs in pending:
                settings = obs.obs_source_get_settings(source)
                try:
                    settings_json = json.loads(obs.obs_data_get_json(settings))
                    for trail, ref in refs:
                        target = root / ref['relative']
                        put(settings_json, trail, target.as_uri() if ref['uri'] else str(target))
                    replacement = obs.obs_data_create_from_json(json.dumps(settings_json))
                    try:
                        obs.obs_source_update(source, replacement)
                    finally:
                        obs.obs_data_release(replacement)
                finally:
                    obs.obs_data_release(settings)
            obs.obs_frontend_save()
        finally:
            for source, _ in pending:
                obs.obs_source_release(source)
        return 'Current collection paths updated; original exported JSON remains your backup.'
    return report(apply)


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_path(props, 'collection', 'Exported collection JSON', obs.OBS_PATH_FILE, 'JSON (*.json)', None)
    obs.obs_properties_add_text(props, 'destination', 'New package folder (full path)', obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_bool(props, 'include_fonts', 'Copy matched non-system font files (requires redistribution rights)')
    obs.obs_properties_add_bool(props, 'include_plugins', 'Copy installed third-party plugin bundles')
    obs.obs_properties_add_button(props, 'pack', 'Gather assets and create portable collection', pack_clicked)
    obs.obs_properties_add_button(props, 'apply', 'Replace paths in current original collection', apply_clicked)
    obs.obs_properties_add_path(props, 'package', 'Moved package folder', obs.OBS_PATH_DIRECTORY, None, None)
    obs.obs_properties_add_button(props, 'install_plugins', 'Install bundled plugins on this Mac (restart OBS afterward)', install_plugins_clicked)
    obs.obs_properties_add_button(props, 'rebase', 'Relink package to this computer', rebase_clicked)
    return props


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('pack'); p.add_argument('collection'); p.add_argument('destination'); p.add_argument('--include-font-files', action='store_true'); p.add_argument('--font-dir', action='append'); p.add_argument('--include-plugins', action='store_true'); p.add_argument('--plugin-dir', action='append')
    p = sub.add_parser('rebase'); p.add_argument('package')
    p = sub.add_parser('install-plugins'); p.add_argument('package'); p.add_argument('--destination')
    args = parser.parse_args()
    if args.command == 'pack':
        print(pack(args.collection, args.destination, args.include_font_files, args.font_dir, args.plugin_dir, args.include_plugins))
    elif args.command == 'install-plugins':
        print(install_plugins(args.package, args.destination))
    else:
        print(rebase(args.package))
