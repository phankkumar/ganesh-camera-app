[app]
title = GaneshCameraApp
package.name = ganeshcameraapp
package.domain = org.example
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,xml
version = 0.1
# Pinned to Python 3.11 - newer Python (3.14, whatever the default recipe
# resolves to) has a known incompatibility with Android/NDK cross-compilation
# (remote_debugging module doesn't build - see kivy/python-for-android#3274).
# Both python3 AND hostpython3 must be pinned together - they're separate
# recipes and Buildozer requires them to match exactly, or the build fails
# with "python3 should have same version as hostpython3".
requirements = python3==3.11.9,hostpython3==3.11.9,kivy,pyjnius

orientation = portrait
fullscreen = 0

# --- Permissions requested at runtime ---
android.permissions = CAMERA,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

# --- API / NDK targets ---
android.api = 33
android.minapi = 23
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a

# --- Required hardware features (install-time gating) ---
# This is what makes the app UNINSTALLABLE on devices lacking the
# listed hardware. Buildozer/python-for-android passes these through
# to the generated AndroidManifest.xml as <uses-feature required="true">.
# If your buildozer version doesn't expose this key, see README.md for
# the manual AndroidManifest.xml override method.
android.uses_feature = android.hardware.camera:required, android.hardware.camera.any:required, android.hardware.camera.front:false

# Required for CI/automation (GitHub Actions has no interactive terminal
# to type "y" at the SDK license prompt).
android.accept_sdk_license = True

# FileProvider is required for the share-sheet (see main.py share_file()).
# res/xml/provider_paths.xml declares the shareable paths. The <provider>
# tag itself is injected by the p4a build hook (p4a/hook.py) which directly
# edits the generated AndroidManifest.xml on disk - this replaces an
# earlier approach (android.extra_manifest_application_arguments passing
# a multi-line XML string as a CLI argument) that caused a
# ManifestMerger2$MergeFailureException: multi-line strings don't
# reliably survive being passed as a CLI arg across the buildozer ->
# python-for-android process boundary. Both approaches are committed to
# the repo, so both are applied automatically on every fresh CI build
# (unlike a manual edit to a *generated* file, which gets wiped every run).
android.add_resources = res
p4a.hook = p4a/hook.py

[buildozer]
log_level = 2
warn_on_root = 1
