"""
CameraApp - Android camera capture app built with Kivy.

Features:
- Switch between front / back camera (instant toggle)
- Attempt dual (front+back) preview on devices that support it,
  with automatic fallback to single-camera mode
- Capture photo, save to device Gallery (Pictures/CameraApp)
- Share captured photo via the native Android share sheet
- Requests CAMERA + storage permissions at runtime
- Declares camera as a REQUIRED hardware feature (see buildozer.spec)
  so the Play Store / package manager refuses to install on devices
  without a camera at all.
"""

import os
import time

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.camera import Camera
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.graphics.texture import Texture

ANDROID = True
try:
    from jnius import autoclass, cast
    from android.permissions import request_permissions, Permission, check_permission
    from android.storage import primary_external_storage_path
except Exception:
    # Lets you run/test the UI on desktop without Android-only libs installed.
    ANDROID = False


APP_FOLDER_NAME = "CameraApp"


def ensure_permissions():
    """Ask for camera + storage permissions at runtime (Android 6+)."""
    if not ANDROID:
        return
    needed = [
        Permission.CAMERA,
        Permission.WRITE_EXTERNAL_STORAGE,
        Permission.READ_EXTERNAL_STORAGE,
    ]
    missing = [p for p in needed if not check_permission(p)]
    if missing:
        request_permissions(missing)


def get_save_dir():
    """Return (and create) the folder photos are saved into."""
    if ANDROID:
        base = primary_external_storage_path()
        path = os.path.join(base, "Pictures", APP_FOLDER_NAME)
    else:
        path = os.path.join(os.path.expanduser("~"), "Pictures", APP_FOLDER_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def notify_gallery(filepath):
    """
    Tell Android's MediaStore about the new file so it shows up
    immediately in the Gallery / Photos app (otherwise it's invisible
    until the next media scan).
    """
    if not ANDROID:
        return
    try:
        Context = autoclass("android.content.Context")
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        File = autoclass("java.io.File")

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity

        f = File(filepath)
        uri = Uri.fromFile(f)
        intent = Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE, uri)
        activity.sendBroadcast(intent)
    except Exception as e:
        print("Gallery notify failed:", e)


def share_file(filepath):
    """Open the native Android share sheet for the given file."""
    if not ANDROID:
        print(f"[desktop stub] would share: {filepath}")
        return
    try:
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        File = autoclass("java.io.File")
        FileProvider = autoclass("androidx.core.content.FileProvider")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")

        activity = PythonActivity.mActivity
        f = File(filepath)

        # authority MUST match the one declared in AndroidManifest.xml
        # (see buildozer.spec / provider_paths.xml)
        authority = activity.getPackageName() + ".fileprovider"
        uri = FileProvider.getUriForFile(activity, authority, f)

        share_intent = Intent(Intent.ACTION_SEND)
        share_intent.setType("image/jpeg")
        share_intent.putExtra(Intent.EXTRA_STREAM, uri)
        share_intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

        chooser = Intent.createChooser(share_intent, "Share photo via")
        activity.startActivity(chooser)
    except Exception as e:
        print("Share failed:", e)


class CameraScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)

        self.current_facing = "back"  # "back", "front", or "dual"
        self.last_saved_path = None

        self.camera_container = BoxLayout(orientation="horizontal")
        self.add_widget(self.camera_container)

        controls = BoxLayout(size_hint_y=0.18, spacing=8, padding=8)
        self.status_label = Label(text="Back camera", size_hint_x=0.3)

        btn_back = Button(text="Back")
        btn_back.bind(on_release=lambda *_: self.switch_camera("back"))

        btn_front = Button(text="Front")
        btn_front.bind(on_release=lambda *_: self.switch_camera("front"))

        btn_dual = Button(text="Dual")
        btn_dual.bind(on_release=lambda *_: self.switch_camera("dual"))

        btn_capture = Button(text="Capture", background_color=(0.2, 0.7, 0.3, 1))
        btn_capture.bind(on_release=lambda *_: self.capture())

        btn_share = Button(text="Share last")
        btn_share.bind(on_release=lambda *_: self.share_last())

        controls.add_widget(self.status_label)
        controls.add_widget(btn_back)
        controls.add_widget(btn_front)
        controls.add_widget(btn_dual)
        controls.add_widget(btn_capture)
        controls.add_widget(btn_share)
        self.add_widget(controls)

        Clock.schedule_once(lambda *_: self.switch_camera("back"), 0.3)

    def _clear_cameras(self):
        for child in list(self.camera_container.children):
            if isinstance(child, Camera):
                child.play = False
            self.camera_container.remove_widget(child)

    def switch_camera(self, facing):
        """
        facing: "back" (index 0), "front" (index 1), or "dual" (both, if
        the device exposes two independent camera indices Kivy can open
        concurrently -- most single-sensor phones will fail the second
        `Camera` open and we fall back to back-only with a warning).
        """
        self._clear_cameras()
        self.current_facing = facing

        if facing == "back":
            cam = Camera(index=0, play=True, resolution=(1280, 720))
            self.camera_container.add_widget(cam)
            self.status_label.text = "Back camera"

        elif facing == "front":
            cam = Camera(index=1, play=True, resolution=(1280, 720))
            self.camera_container.add_widget(cam)
            self.status_label.text = "Front camera"

        elif facing == "dual":
            try:
                cam_back = Camera(index=0, play=True, resolution=(640, 480))
                cam_front = Camera(index=1, play=True, resolution=(640, 480))
                self.camera_container.add_widget(cam_back)
                self.camera_container.add_widget(cam_front)
                self.status_label.text = "Dual (if supported)"
            except Exception as e:
                print("Dual camera open failed, falling back to back:", e)
                self._clear_cameras()
                cam = Camera(index=0, play=True, resolution=(1280, 720))
                self.camera_container.add_widget(cam)
                self.status_label.text = "Back (dual unsupported)"

    def capture(self):
        cams = [c for c in self.camera_container.children if isinstance(c, Camera)]
        if not cams:
            return

        save_dir = get_save_dir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        saved = []

        for i, cam in enumerate(cams):
            texture = cam.texture
            if texture is None:
                continue
            suffix = f"_{i}" if len(cams) > 1 else ""
            filename = f"IMG_{timestamp}{suffix}.png"
            filepath = os.path.join(save_dir, filename)
            texture.save(filepath, flipped=False)
            notify_gallery(filepath)
            saved.append(filepath)

        if saved:
            self.last_saved_path = saved[-1]
            self._toast(f"Saved {len(saved)} photo(s) to {save_dir}")

    def share_last(self):
        if not self.last_saved_path:
            self._toast("No photo captured yet")
            return
        share_file(self.last_saved_path)

    def _toast(self, message):
        popup = Popup(
            title="",
            content=Label(text=message),
            size_hint=(0.8, 0.2),
        )
        popup.open()
        Clock.schedule_once(lambda *_: popup.dismiss(), 1.5)


class CameraApp(App):
    def build(self):
        ensure_permissions()
        return CameraScreen()


if __name__ == "__main__":
    CameraApp().run()
