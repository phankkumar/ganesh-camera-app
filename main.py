"""
GaneshCameraApp - Android camera capture app built with Kivy.

Features:
- Switch between front / back camera (instant toggle)
- Attempt dual (front+back) preview on devices that support it,
  with automatic fallback to single-camera mode
- Post-capture preview screen with a Back button to return to live camera
- Capture photo, save to device Gallery (Pictures/CameraApp)
- Share captured photo via the native Android share sheet
- Requests CAMERA + storage permissions at runtime
- Declares camera as a REQUIRED hardware feature (see buildozer.spec)
  so the Play Store / package manager refuses to install on devices
  without a camera at all.
"""

import os
import time

# Config must be set before any other Kivy module is imported, since
# Window/graphics settings are read at import time. These two settings
# are the standard mitigation for the preview flicker commonly seen with
# Kivy's legacy Android camera provider: multisampling can conflict with
# how the camera preview surface updates, and an unpinned frame rate can
# cause the texture update and screen redraw to fall out of sync.
from kivy.config import Config
Config.set('graphics', 'multisamples', '0')
Config.set('graphics', 'maxfps', '30')

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.camera import Camera
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.graphics.texture import Texture
from kivy.graphics import PushMatrix, PopMatrix, Rotate

ANDROID = True
try:
    from jnius import autoclass, cast
    from android.permissions import request_permissions, Permission, check_permission
    from android.storage import primary_external_storage_path
except Exception:
    # Lets you run/test the UI on desktop without Android-only libs installed.
    ANDROID = False

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


APP_FOLDER_NAME = "CameraApp"

# Sensor-mount rotation correction, in degrees. Kivy's legacy Camera
# widget doesn't auto-correct for how the phone's camera sensors are
# physically mounted relative to the screen - back and front sensors
# are usually mounted differently, which is why they skew in opposite
# directions if left uncorrected.
#
# These starting values (90 / -90) are the most common correction on
# Android phones, but the exact angle needed varies by device/manufacturer.
# If the preview still looks off after this change, try adjusting these
# two numbers - common values to try are 90, -90, 180, and 270 (or 0 if
# a given camera turns out to need no correction at all).
ROTATION_BACK = 90
ROTATION_FRONT = -90


class RotatedCameraBox(BoxLayout):
    """
    Wraps a Camera widget and applies a canvas rotation to correct for
    the physical sensor mount orientation. self.camera is the actual
    Camera instance inside - use that (not the wrapper) to read .texture
    for capture, since the rotation here is a display-only transform and
    doesn't rotate the underlying captured texture itself.
    """

    def __init__(self, index, resolution, rotation=0, **kwargs):
        super().__init__(**kwargs)
        self.camera = Camera(index=index, play=True, resolution=resolution)

        with self.canvas.before:
            PushMatrix()
            self._rotate = Rotate(angle=rotation, axis=(0, 0, 1), origin=self.center)
        with self.canvas.after:
            PopMatrix()

        self.add_widget(self.camera)
        self.bind(pos=self._update_origin, size=self._update_origin)

    def _update_origin(self, *args):
        self._rotate.origin = self.center


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


def rotate_saved_image(filepath, angle):
    """
    Rotate a saved photo file to match the on-screen preview correction.
    The canvas Rotate used for the live preview is display-only and
    doesn't affect the raw texture data that gets saved - this applies
    the equivalent correction to the actual file on disk.

    If the sign/direction ends up backwards from the preview (e.g. photo
    rotated the opposite way from what the preview showed), try negating
    the angle passed in here relative to ROTATION_BACK/ROTATION_FRONT.
    """
    if not PIL_AVAILABLE or angle == 0:
        return
    try:
        img = PILImage.open(filepath)
        # PIL's rotate() is counter-clockwise for positive angles;
        # negate to match Kivy canvas Rotate's convention.
        rotated = img.rotate(-angle, expand=True)
        rotated.save(filepath)
    except Exception as e:
        print(f"Could not rotate saved image {filepath}: {e}")


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
    BUTTON_HEIGHT = dp(34)
    BUTTON_FONT_SIZE = '12sp'

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)

        self.current_facing = "back"  # "back", "front", or "dual"
        self.last_saved_path = None
        self.active_cameras = []  # list of actual Camera (not wrapper) instances
        self.active_camera_rotations = []  # matching rotation angle for each, for save correction

        # --- Live camera view ---
        self.camera_container = BoxLayout(orientation="horizontal")

        self.controls = BoxLayout(
            size_hint_y=None, height=self.BUTTON_HEIGHT + dp(16),
            spacing=4, padding=(6, 4),
        )
        self.status_label = Label(
            text="Back camera", size_hint_x=0.28, font_size=self.BUTTON_FONT_SIZE,
        )

        btn_back_cam = Button(text="Back", font_size=self.BUTTON_FONT_SIZE)
        btn_back_cam.bind(on_release=lambda *_: self.switch_camera("back"))

        btn_front = Button(text="Front", font_size=self.BUTTON_FONT_SIZE)
        btn_front.bind(on_release=lambda *_: self.switch_camera("front"))

        btn_dual = Button(text="Dual", font_size=self.BUTTON_FONT_SIZE)
        btn_dual.bind(on_release=lambda *_: self.switch_camera("dual"))

        btn_capture = Button(
            text="Capture", font_size=self.BUTTON_FONT_SIZE,
            background_color=(0.2, 0.7, 0.3, 1),
        )
        btn_capture.bind(on_release=lambda *_: self.capture())

        btn_share = Button(text="Share", font_size=self.BUTTON_FONT_SIZE)
        btn_share.bind(on_release=lambda *_: self.share_last())

        for w in (self.status_label, btn_back_cam, btn_front, btn_dual, btn_capture, btn_share):
            w.size_hint_y = None
            w.height = self.BUTTON_HEIGHT
            self.controls.add_widget(w)

        self.add_widget(self.camera_container)
        self.add_widget(self.controls)

        # --- Post-capture preview screen (built once, shown/hidden as needed) ---
        self.preview_container = BoxLayout(orientation="vertical")
        self.preview_image = Image(allow_stretch=True, keep_ratio=True)

        preview_controls = BoxLayout(
            size_hint_y=None, height=self.BUTTON_HEIGHT + dp(16),
            spacing=4, padding=(6, 4),
        )
        btn_preview_back = Button(text="< Back", font_size=self.BUTTON_FONT_SIZE)
        btn_preview_back.bind(on_release=lambda *_: self.hide_preview())

        btn_preview_share = Button(text="Share", font_size=self.BUTTON_FONT_SIZE)
        btn_preview_share.bind(on_release=lambda *_: self.share_last())

        for w in (btn_preview_back, btn_preview_share):
            w.size_hint_y = None
            w.height = self.BUTTON_HEIGHT
            preview_controls.add_widget(w)

        self.preview_container.add_widget(self.preview_image)
        self.preview_container.add_widget(preview_controls)

        Clock.schedule_once(lambda *_: self.switch_camera("back"), 0.3)

    def _clear_cameras(self):
        for child in list(self.camera_container.children):
            if isinstance(child, RotatedCameraBox):
                child.camera.play = False
            self.camera_container.remove_widget(child)
        self.active_cameras = []
        self.active_camera_rotations = []

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
            box = RotatedCameraBox(index=0, resolution=(1280, 720), rotation=ROTATION_BACK)
            self.camera_container.add_widget(box)
            self.active_cameras = [box.camera]
            self.active_camera_rotations = [ROTATION_BACK]
            self.status_label.text = "Back camera"

        elif facing == "front":
            box = RotatedCameraBox(index=1, resolution=(1280, 720), rotation=ROTATION_FRONT)
            self.camera_container.add_widget(box)
            self.active_cameras = [box.camera]
            self.active_camera_rotations = [ROTATION_FRONT]
            self.status_label.text = "Front camera"

        elif facing == "dual":
            try:
                box_back = RotatedCameraBox(index=0, resolution=(640, 480), rotation=ROTATION_BACK)
                box_front = RotatedCameraBox(index=1, resolution=(640, 480), rotation=ROTATION_FRONT)
                self.camera_container.add_widget(box_back)
                self.camera_container.add_widget(box_front)
                self.active_cameras = [box_back.camera, box_front.camera]
                self.active_camera_rotations = [ROTATION_BACK, ROTATION_FRONT]
                self.status_label.text = "Dual (if supported)"
            except Exception as e:
                print("Dual camera open failed, falling back to back:", e)
                self._clear_cameras()
                box = RotatedCameraBox(index=0, resolution=(1280, 720), rotation=ROTATION_BACK)
                self.camera_container.add_widget(box)
                self.active_cameras = [box.camera]
                self.active_camera_rotations = [ROTATION_BACK]
                self.status_label.text = "Back (dual unsupported)"

    def capture(self):
        cams = self.active_cameras
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

            rotation = self.active_camera_rotations[i] if i < len(self.active_camera_rotations) else 0
            rotate_saved_image(filepath, rotation)

            notify_gallery(filepath)
            saved.append(filepath)

        if saved:
            self.last_saved_path = saved[-1]
            self.show_preview(self.last_saved_path)

    def show_preview(self, filepath):
        """Switch from the live camera view to a full-size preview of the
        just-captured photo, with a Back button to return to the camera."""
        self.remove_widget(self.camera_container)
        self.remove_widget(self.controls)

        self.preview_image.source = filepath
        self.preview_image.reload()
        self.add_widget(self.preview_container)

    def hide_preview(self):
        """Return from the preview screen to the live camera view. The
        Camera widgets were never stopped while hidden, just removed from
        the visible layout - no need to recreate/reopen them here."""
        self.remove_widget(self.preview_container)
        self.add_widget(self.camera_container)
        self.add_widget(self.controls)

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
    title = "GaneshCameraApp"

    def build(self):
        ensure_permissions()
        # Solid clear color instead of Kivy's default - avoids a visible
        # white/gray flash in the gap between camera preview frame updates.
        Window.clearcolor = (0, 0, 0, 1)
        return CameraScreen()


if __name__ == "__main__":
    CameraApp().run()
