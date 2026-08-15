[app]
title = CameraApp
package.name = cameraapp
package.domain = org.example
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,xml
version = 0.1
# Pinned to Python 3.11 - newer Python (3.14, whatever the build image
# defaults to) has a known incompatibility with Android/NDK cross-compilation
# (remote_debugging module doesn't build - see kivy/python-for-android#3274).

requirements = python3==3.11.9,kivy,pyjnius

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
# You'll need a res/xml/provider_paths.xml and a matching <provider> entry
# in AndroidManifest.xml -- see README.md for the exact snippet.
android.add_resources = res

[buildozer]
log_level = 2
warn_on_root = 1
