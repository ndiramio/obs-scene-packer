# OBS Scene Packer

## Native macOS plugin alpha

The native C++ plugin runs under **Tools → Scene Packer** and does not require Python. The first alpha targets Apple Silicon Macs, macOS 13 or newer, and OBS 32.x. It includes scene folders, shared assets, collection relinking, static browser dependencies, optional fonts, plugin-bundle transfer, and a recovery snapshot.

[Download the native alpha](https://github.com/ndiramio/obs-scene-packer/releases/tag/v0.2.0-alpha.1) · [Native installation and build instructions](native/README.md)

The alpha installer is unsigned and not notarized. Native transfer fixtures and an isolated Qt/OBS-loader host are tested; full validation inside the running OBS application remains pending because UI automation was unavailable during development. Intel support is not included in this first binary. The Python implementation below remains available during validation.

## Legacy Python script

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

Copy the **entire package folder**, including `assets`, `manifest.json`, `requirements.json`, `collection.json`, and `fonts` and `plugins` when present. On the destination Mac, load this script, choose the moved folder under **Moved package folder**, and click **Relink package to this computer**. If plugin bundles were included, click **Install bundled plugins on this Mac**, restart OBS, and reopen Scripts. Then import its `collection.json` through **Scene Collection → Import** and select the imported collection. Rebase whenever the package moves again. Absolute paths mean importing without rebasing after a move will point at the old location.

Shared files go into `assets/Shared`; files used by one scene go into a folder named for that scene. Nested scenes and groups are traversed, with cycles guarded. Unattached sources and transitions use a Collection folder. Unique scene and asset suffixes prevent equal names from overwriting one another. OBS shared sources retain one path, so files shared between scenes are deliberately stored once.

## Standalone commands

```sh
python3 scene_packer.py pack '/path/to/export.json' '/path/to/new-package'
# Optional installed font gathering, with an explicit font search folder:
python3 scene_packer.py pack '/path/to/export.json' '/path/to/new-package' --include-font-files --font-dir '/path/to/fonts'
# Optional plugin bundle gathering, using the standard macOS plugin folders:
python3 scene_packer.py pack '/path/to/export.json' '/path/to/new-package' --include-plugins
# On the destination Mac, install copied bundles, then restart OBS:
python3 scene_packer.py install-plugins '/path/to/moved-package'
python3 scene_packer.py rebase '/path/to/moved-package'
```

## Scope and limits

Covers image files, local media, slideshow directories, VLC playlist file entries, text-file paths, local browser pages, stingers, and third-party settings that contain recognizable local paths. Detection scans strings in source settings and filters rather than claiming complete knowledge of every plugin. Relative paths resolve against the exported JSON folder when they exist. Missing absolute paths stop packing before copying. Missing relative paths and opaque or encoded plugin settings cannot be reliably detected.

Local HTML files referenced directly by sources now gather static dependencies from `src`, `href`, `poster`, `data`, `srcset`, inline styles, CSS `url()` and quoted `@import`. Dependencies are copied recursively and links become relative, including images, stylesheets, scripts, and web-font files. Query strings and fragments are retained; cycles and repeated files are deduplicated within each overlay. UTF-8 HTML/CSS is required. A missing local dependency or an HTML `<base href>` stops packing with an error, cleaning up the staged package. Directory sources are copied as entire trees without rewriting embedded URLs.

Remote resources stay online and are listed in `manifest.json` warnings. JavaScript files are copied, but module imports, `fetch()`, workers, dynamically generated URLs, iframe `srcdoc`, and server-side routes are not analyzed; review script dependencies manually. URLs inside SVG, JSON, and other formats are not recursively analyzed. No web code is executed and no remote resources are downloaded. `srcset` containing data URLs is preserved and flagged for manual review. This is static dependency gathering, not a guarantee that every browser overlay works offline.

`requirements.json` lists font family/style requests from OBS font settings, matching installed font files, every source/filter type ID, and user-installed macOS plugin bundle names, identifiers, and versions. All IDs are listed, including OBS built-ins. Collection JSON does not establish which bundle provides each ID or disclose frontend-only plugin requirements, so review the bundle list manually. Plugin locations follow the [OBS plugins guide](https://obsproject.com/kb/plugins-guide).

Enable **Copy matched non-system font files** in the script to include discovered `.ttf`, `.otf`, `.ttc`, and `.otc` files under `fonts`. Matching uses internal font family/full/PostScript names rather than filenames and copies all matching family files. Style coverage is not guaranteed. The default scans `~/Library/Fonts`, `/Library/Fonts`, and `/System/Library/Fonts`; system fonts are inventoried but never copied. Font files unavailable in these locations, including some cloud-managed fonts, are reported as not found. Install copied fonts with Font Book on the destination Mac; OBS's font family names remain unchanged. Only copy fonts you are permitted to redistribute. Local web fonts referenced through CSS are gathered as browser dependencies independently of this option.

Enable **Copy installed third-party plugin bundles** to gather complete `.plugin` bundles from `~/Library/Application Support/obs-studio/plugins` and `/Library/Application Support/obs-studio/plugins` into `plugins/`. This copies all discovered bundles in these locations, not just plugins used by the collection. OBS built-in modules inside OBS.app, legacy installations, VST/AU plugins, external runtimes, licenses, and plugin preferences stored outside bundles are not gathered. The CLI accepts repeated `--plugin-dir` options to choose explicit search folders instead of the defaults. Duplicate bundle names stop gathering; select a single folder to resolve them.

On the destination Mac, choose the package in Scripts and click **Install bundled plugins on this Mac**, then restart OBS. Alternatively use the `install-plugins` CLI while OBS is closed. Bundles are placed in the standard user plugin folder. Installation preserves internal symlinks and executable permissions, checks bundle hashes and main-executable Mach-O architecture against the running Python/OBS process, and preflights all bundles before copying. Identical installed bundles are skipped; different existing bundles and conflicting identifiers are refused. Failed installs remove new bundles added by that operation. No installed bundle is overwritten. The CLI offers `--destination` for a custom plugin folder.

The architecture check does not prove compatibility with the destination OBS/macOS version or every bundled library. Universal plugins can support both Intel and Apple Silicon; single-architecture bundles need a matching OBS process. Matching OBS versions are strongly recommended. NDI, drivers, companion apps, external libraries, activation services, and other prerequisites can still require publisher installers. macOS security controls may also require the publisher's installer; this tool does not remove quarantine attributes or change security settings. Only install bundles from your own trusted package. The manifest hash detects changes, not publisher authenticity. Plugin bundle copying does not execute plugins; OBS loads them on restart. Before sharing a package publicly, ensure redistribution is permitted and remove plugins or licenses you do not intend to share. Remote URLs, cloud resources, devices, capture permissions, and OBS profiles still require destination setup. The manifest lists gathered assets; review it against your collection before relying on the package.

Copying referenced folders includes their contents and dereferences symlinks. Do not select broad folders containing unrelated private files. Package JSON preserves unrelated collection fields, which can include plugin credentials or URLs; review before sharing outside your own computers.

Rebase validates that manifest paths stay within the package. Packing refuses an existing destination and writes through a staging folder, cleaning up failed copies. Original assets and the exported JSON are preserved. Live OBS apply is not transactional if OBS itself fails during an update; reimport your saved original export to recover.

## Validation

Run `python3 -m unittest discover -s . -v` in this folder. Eighteen fixture-based tests cover shared and nested sources, group references, transitions, equal filenames, relocation, preservation, missing assets, folder copying, file URIs, filter paths, and unsafe destination placement, recursive CSS and web-font gathering, browser relocation, dependency cleanup, internal font-name matching, opt-in font copying, plugin/filter inventory, bundle copying, universal Mach-O headers, architecture refusal, conflict prevention, tamper checks, symlink validation, repeat installation, and installation rollback. OBS UI integration has not been exercised in a running OBS instance; this is a working first implementation pending testing against your actual collection and OBS version.
