# OBS Scene Packer for macOS

An OBS Python script extension, not a compiled native plugin. It gathers local files and folders referenced in exported collection source settings, filters, groups, and transitions. It writes a new collection with updated absolute paths, or optionally updates matching sources in your loaded original collection.

## Install and use

1. Keep `scene_packer.py` in a permanent folder.
2. In OBS, open **Tools → Scripts → Python Settings** and configure a Python installation compatible with your OBS build and architecture. OBS's scripting documentation describes this requirement: https://docs.obsproject.com/scripting . This project uses Python 3.8 or newer and only standard-library packages. The Python ABI version supported by your OBS build still matters.
3. In **Tools → Scripts**, click **+** and select `scene_packer.py`.
4. Export your collection with **Scene Collection → Export** immediately before packing.
5. Select that JSON in the script. Enter the full path to a **new folder**, such as `/Users/yourname/Documents/Show Package`. The folder must not exist yet.
6. Click **Gather assets and create portable collection**. Completion or errors appear in the OBS log, accessible from **Help → Log Files → View Current Log**. Large copies run synchronously and may temporarily freeze OBS; pack when you are not broadcasting.
7. To update your original collection, leave it loaded and click **Replace paths in current original collection**. Keep the exported original JSON as your recovery copy. Live apply requires the original collection name to match and updates source settings; collections with file references inside filters must use the new-collection import workflow instead. Use a fresh export and avoid editing sources between export and apply.

## Transfer to another Mac

Copy the **entire package folder**, including `assets`, `manifest.json`, and `collection.json`. On the destination Mac, load this script, choose the moved folder under **Moved package folder**, and click **Relink package to this computer**. Then import its `collection.json` through **Scene Collection → Import** and select the imported collection. Rebase whenever the package moves again. Absolute paths mean importing without rebasing after a move will point at the old location.

Shared files go into `assets/Shared`; files used by one scene go into a folder named for that scene. Nested scenes and groups are traversed, with cycles guarded. Unattached sources and transitions use a Collection folder. Unique scene and asset suffixes prevent equal names from overwriting one another. OBS shared sources retain one path, so files shared between scenes are deliberately stored once.

## Standalone commands

```sh
python3 scene_packer.py pack '/path/to/export.json' '/path/to/new-package'
python3 scene_packer.py rebase '/path/to/moved-package'
```

## Scope and limits

Covers image files, local media, slideshow directories, VLC playlist file entries, text-file paths, local browser pages, stingers, and third-party settings that contain recognizable local paths. Detection scans strings in source settings and filters rather than claiming complete knowledge of every plugin. Relative paths resolve against the exported JSON folder when they exist. Missing absolute paths stop packing before copying. Missing relative paths and opaque or encoded plugin settings cannot be reliably detected.

A local HTML file is copied, but HTML/CSS dependencies referenced *inside* it are not discovered. Store browser overlays in a referenced directory or separately copy their full dependency tree. Remote URLs, cloud resources, installed fonts, devices, capture permissions, plugins, and OBS profiles are not transferred. Install matching plugins and fonts on the other Mac. The manifest lists gathered assets; review it against your collection before relying on the package.

Copying referenced folders includes their contents and dereferences symlinks. Do not select broad folders containing unrelated private files. Package JSON preserves unrelated collection fields, which can include plugin credentials or URLs; review before sharing outside your own computers.

Rebase validates that manifest paths stay within the package. Packing refuses an existing destination and writes through a staging folder, cleaning up failed copies. Original assets and the exported JSON are preserved. Live OBS apply is not transactional if OBS itself fails during an update; reimport your saved original export to recover.

## Validation

Run `python3 -m unittest discover -s . -v` in this folder. Five fixture-based tests cover shared and nested sources, group references, transitions, equal filenames, relocation, preservation, missing assets, folder copying, file URIs, filter paths, and unsafe destination placement. OBS UI integration has not been exercised in a running OBS instance; this is a working first implementation pending testing against your actual collection and OBS version.
