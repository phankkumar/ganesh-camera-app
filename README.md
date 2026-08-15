# CameraApp (Kivy, Android)

A Python/Kivy Android app that opens the front, back, or (where supported)
both cameras, captures photos, saves them to the device gallery, and shares
them via the standard Android share sheet.

## Project files

- `main.py` — the app
- `buildozer.spec` — packaging config (permissions, required hardware features)
- `res/xml/provider_paths.xml` — needed for the share-sheet FileProvider

## Build

You need a Linux machine (or WSL) with Buildozer installed:

```bash
pip install buildozer cython
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf \
    libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev \
    libtinfo5 cmake libffi-dev libssl-dev

buildozer -v android debug
```

The first build downloads the Android SDK/NDK and will take a while.
Output APK lands in `bin/`. Install with:

```bash
adb install -r bin/cameraapp-0.1-arm64-v8a-debug.apk
```

## Important: install-time hardware gating

The requirement "don't install if a feature is missing" is implemented via
Android's `<uses-feature android:required="true">` manifest tag — the Play
Store (and `pm install` on modern Android) will refuse to install an app on
a device that lacks a declared *required* feature.

I've set this via `android.uses_feature` in `buildozer.spec`. **Verify this
against your installed Buildozer version** — this manifest key has changed
across `python-for-android` releases, and if it isn't picked up, you can add
it manually:

1. Run `buildozer android debug` once (it generates the manifest template).
2. Find `AndroidManifest.tmpl.xml` under
   `.buildozer/android/platform/build-*/dists/<appname>/templates/`.
3. Add before `</manifest>`:
   ```xml
   <uses-feature android:name="android.hardware.camera" android:required="true" />
   <uses-feature android:name="android.hardware.camera.any" android:required="true" />
   ```
4. Also add the FileProvider block (needed for sharing):
   ```xml
   <provider
       android:name="androidx.core.content.FileProvider"
       android:authorities="${applicationId}.fileprovider"
       android:exported="false"
       android:grantUriPermissions="true">
       <meta-data
           android:name="android.support.FILE_PROVIDER_PATHS"
           android:resource="@xml/provider_paths" />
   </provider>
   ```
5. Rebuild.

## Known limitations (please read)

- **Dual camera capture is device-dependent.** Kivy's built-in `Camera`
  widget wraps the legacy Android Camera API, which on most phones only
  allows *one* camera to be opened at a time — opening a second `Camera`
  while the first is active will throw, and the app falls back to
  back-camera-only. True concurrent front+back capture requires Android's
  Camera2/CameraX "logical multi-camera" API and only works on a subset of
  Android 9+ devices. If concurrent dual capture is a hard requirement,
  the Kivy route isn't the right tool — you'd want a native Kotlin/Java app
  (optionally with Python business logic embedded via **Chaquopy**) using
  CameraX's `ConcurrentCamera` API directly.
- The legacy camera API Kivy relies on is deprecated by Google and can
  behave inconsistently (resolution, orientation) across manufacturers.
  For production-quality camera control, consider swapping the capture
  layer for `pyjnius` calls into `Camera2`/`CameraX`, keeping the rest of
  this app (permissions, save, share, UI) unchanged.
- Runtime permission prompts (camera, storage) still happen on first launch
  even though the feature is *required* at install time — required hardware
  presence and permission grants are two separate Android systems.
- On Android 10+, writing directly to `Pictures/CameraApp` works without
  extra permissions for files your own app creates (scoped storage); if you
  later target `WRITE_EXTERNAL_STORAGE`-restricted flows, prefer
  `MediaStore.Images.Media.insert()` instead of raw file paths.

## Testing on desktop

`main.py` detects when Android-only modules aren't available and stubs them
out, so `python main.py` will run the Kivy UI on your desktop (camera index
0 is usually your webcam) for quick UI iteration before building an APK.
