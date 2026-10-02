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


def pack(collection, destination):
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
    manifest = {'version': 1, 'collection': 'collection.json', 'assets': [], 'references': []}
    copied = {}
    try:
        for source, trail, value, path, scene_owners in references:
            if path not in copied:
                all_owners = set().union(*(r[4] for r in references if r[3] == path))
                folder = 'Shared' if len(all_owners) > 1 else slug(next(iter(all_owners)))
                relative = Path('assets') / folder / (hashlib.sha256(str(path).encode()).hexdigest()[:12] + '-' + path.name)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.copytree(path, target)
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
        write_json(staging / 'manifest.json', manifest)
        os.rename(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination / 'collection.json'


def rebase(package):
    root = Path(package).expanduser().resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    data = json.loads((root / 'collection.json').read_text())
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


def report(action):
    try:
        result = action()
        obs.script_log(obs.LOG_INFO, 'Scene Packer: ' + str(result))
    except Exception as exc:
        obs.script_log(obs.LOG_ERROR, 'Scene Packer: ' + str(exc))
    return False


def pack_clicked(props, prop):
    return report(lambda: pack(_config['collection'], _config['destination']))


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
    obs.obs_properties_add_button(props, 'pack', 'Gather assets and create portable collection', pack_clicked)
    obs.obs_properties_add_button(props, 'apply', 'Replace paths in current original collection', apply_clicked)
    obs.obs_properties_add_path(props, 'package', 'Moved package folder', obs.OBS_PATH_DIRECTORY, None, None)
    obs.obs_properties_add_button(props, 'rebase', 'Relink package to this computer', rebase_clicked)
    return props


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('pack'); p.add_argument('collection'); p.add_argument('destination')
    p = sub.add_parser('rebase'); p.add_argument('package')
    args = parser.parse_args()
    print(pack(args.collection, args.destination) if args.command == 'pack' else rebase(args.package))
