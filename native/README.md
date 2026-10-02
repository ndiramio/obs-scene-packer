# Native OBS Scene Packer for macOS

The native alpha is a C++/Qt OBS Tools extension. Users do not need Python, Python Settings, or Tools → Scripts. The Python version remains in this repository for comparison and recovery while the native build is validated.

## Install

Quit OBS. Install `obs-scene-packer.plugin` into `~/Library/Application Support/obs-studio/plugins`, or use the macOS installer package to install it under `/Library/Application Support/obs-studio/plugins`. Restart OBS and open **Tools → Scene Packer**. Do not install both copies of the native bundle or keep two versions in separate plugin locations. The Python script may be removed from Tools → Scripts after switching.

This first binary targets Apple Silicon on macOS 13 or newer with OBS 32.x. Developer ID signing and Apple notarization are not configured; the installer is an unsigned alpha, not a notarized public release. Mac security checks on downloaded builds may require a locally compiled build or a future signed release. No security settings or quarantine attributes are changed by the build or the plugin.

## Use

Choose **Use current collection** or select an exported collection JSON. Choose a new package folder that does not exist yet, optionally include fonts and third-party plugin bundles, and click **Gather assets and create portable collection**. A progress window keeps OBS responsive while files copy. Gathering is blocked while streaming, recording, replay buffer, or virtual camera are active.

**Also replace paths in the loaded original collection** updates matching source settings after gathering. UUID and original-path checks catch source edits made since the selected collection was saved. The UI retains the new package path even if live replacement is refused. Filters with file references require importing the portable collection instead. Each native package includes `original-collection.json`, a recovery snapshot with the original paths; import that file to recover the original collection.

On the new Mac, choose the complete package under **Moved package**, click **Install bundled plugins** if plugins were included, restart OBS, and click **Relink collection**. Import the resulting `collection.json` through **Scene Collection → Import**. Install gathered fonts through Font Book. OBS plugin bundles are not loaded by Scene Packer itself; OBS loads them on its next launch.

The native engine retains the Python package structure and can relink earlier packages. It traverses nested scenes and groups, keeps shared assets in Shared, prevents filename collisions, discovers static local HTML/CSS dependencies and web fonts, inventories installed font families and plugin versions, copies optional non-system fonts and third-party bundles, and restores plugins without overwriting differing installations. Review `manifest.json` and `requirements.json` before transfer. The root README explains browser, font, and plugin limits, including JavaScript runtime loads and external plugin runtimes.

## Build

Requires macOS command-line developer tools, CMake 3.28+, and OBS installed at `/Applications/OBS.app`. Run:

```sh
./build-macos.sh
```

The script fetches hash-verified Qt SDK and OBS headers pinned by OBS's official plugin template. Development uses Python only for fixture tests, not for building with this script or running the plugin. Qt is linked to the runtime supplied by OBS; a second Qt runtime is not bundled. `OBS_APP`, `CMAKE`, `PACKER_DEPS_DIR`, `PACKER_BUILD_DIR`, and `PACKER_ARCH` can override defaults. The local build currently links the OBS libraries of the installed app, so cross-compiling Intel requires a matching Intel OBS app and SDK slice. Intel builds require separate testing before release.

## Tests

```sh
PACKER_CLI="$PWD/build/packer-cli" python3 test_native.py
```

The existing fixture suite is adapted to call the compiled C++ executable. Python-only binary-header helper checks and Python rename fault injection are skipped, because those mocks cannot exercise a native subprocess. Native transfer, browser, font, and plugin-copy/install fixtures run against actual compiled code. macOS sandbox restrictions can prevent Qt from detecting ARM CPU capabilities; run build/test commands in a normal Terminal. Do not interpret a passing packing fixture as proof that an arbitrary third-party plugin will load in another OBS version.

OBS header and Qt SDK versions, alpha status, and runtime validation are intentionally documented separately from installer availability. The build uses OBS's public C API and official headers; a small generated `obsconfig.h` replaces the full OBS source build, allowing Apple's Command Line Tools without the full Xcode application.

## Alpha validation record

The native executable passes 26 fixture tests (28 considered, with two Python-only checks skipped). An isolated Qt host exercises module initialization, window creation, gather, package relocation, and empty-plugin restoration. The actual bundle is also checked through `obs_open_module` against the installed OBS libraries. This is not a full live OBS application test: the native app automation service timed out, so the Tools menu and transfer workflow in the running OBS app still require manual verification. The legacy Python suite passes all 18 tests.

The UI test is optional at build time (`-DPACKER_BUILD_UI_TEST=ON`). Run it with `QT_QPA_PLATFORM=offscreen`, the downloaded SDK's `plugins` folder as `QT_PLUGIN_PATH`, an output PNG path, and the compiled bundle executable path. It uses temporary fixture files and does not edit user scenes or install test bundles into OBS's plugin folder.

Use `./package-macos.sh /path/to/output` after building to create the unsigned `.pkg`, a manual-install ZIP, and SHA-256 checksums. The `.pkg` places the bundle in the system plugin folder and requires administrator approval; the ZIP can be installed into the user plugin folder without an administrator password. The alpha plugin carries an ad-hoc signature, not a Developer ID signature.
