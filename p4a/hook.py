"""
python-for-android build hook.

Injects the FileProvider <provider> tag into the generated
AndroidManifest.xml by directly editing the file on disk, instead of
passing a multi-line XML string as a command-line argument (which is
what buildozer.spec's android.extra_manifest_application_arguments
does under the hood, and what caused a ManifestMerger2$MergeFailureException
- multi-line strings don't reliably survive being passed as a CLI arg
across process boundaries between buildozer and python-for-android).

This runs as part of the normal p4a build via the p4a.hook setting in
buildozer.spec, so - unlike a manual edit to a generated file - it's
committed to the repo and applied automatically on every fresh build,
including in ephemeral CI runners where .buildozer/ is recreated from
scratch each time.
"""

from pathlib import Path

PROVIDER_XML = """    <provider
        android:name="androidx.core.content.FileProvider"
        android:authorities="${applicationId}.fileprovider"
        android:exported="false"
        android:grantUriPermissions="true">
        <meta-data
            android:name="android.support.FILE_PROVIDER_PATHS"
            android:resource="@xml/provider_paths" />
    </provider>
"""


def _patch_manifest(dist_dir):
    manifest_file = Path(dist_dir) / "src" / "main" / "AndroidManifest.xml"
    if not manifest_file.exists():
        # Some p4a/bootstrap versions place it at the dist root instead.
        manifest_file = Path(dist_dir) / "AndroidManifest.xml"
    if not manifest_file.exists():
        print(f"[hook] AndroidManifest.xml not found under {dist_dir}, skipping FileProvider injection")
        return

    manifest_text = manifest_file.read_text(encoding="utf-8")

    if "FileProvider" in manifest_text:
        print("[hook] FileProvider already present in manifest, skipping")
        return

    if "</application>" not in manifest_text:
        print("[hook] Could not find </application> in manifest, skipping FileProvider injection")
        return

    patched = manifest_text.replace("</application>", PROVIDER_XML + "    </application>")
    manifest_file.write_text(patched, encoding="utf-8")
    print(f"[hook] Injected FileProvider <provider> tag into {manifest_file}")


def before_apk_build(toolchain):
    _patch_manifest(toolchain._dist.dist_dir)


def after_apk_build(toolchain):
    # Some p4a versions only expose the dist at this point rather than
    # before_apk_build - patch here too (the "already present" check
    # above makes this safe to call twice).
    _patch_manifest(toolchain._dist.dist_dir)
