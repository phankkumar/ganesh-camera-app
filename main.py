"""
GaneshCameraApp - Android camera capture app built with Kivy.

Three-screen flow:
  Home        -> pick Back / Front / Dual camera, or Exit
  Camera      -> live preview, rotate tuner(s), Capture, Share, Home
  Preview     -> full-size view of the captured photo, Back / Share

Features:
- Switch between front / back camera, or attempt dual (front+back)
  preview on devices that support it, with automatic fallback
- Per-camera rotation correction that persists across mode switches,
  with separate tuner controls for Back and Front when both are shown
  at once in Dual mode
- Capture uses export_to_png() on the (already rotation-corrected)
  camera widget, so the saved photo always matches the live preview
  exactly - display and save share a single source of truth for
  orientation instead of two code paths that can disagree
- Save to device Gallery (Pictures/CameraApp), share via the native
  Android share sheet
- Requests CAMERA + storage permissions at runtime
- Declares camera as a REQUIRED hardware feature (see buildozer.spec)
  so the Play Store / package manager refuses to install on devices
  without a camera at all
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
from kivy.uix.screenmanager import ScreenManager, Screen, NoTransition, SlideTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.camera import Camera
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, PushMatrix, PopMatrix, Rotate

ANDROID = True
try:
    from jnius import autoclass
    from android.permissions import request_permissions, Permission, check_permission
    from android.storage import primary_external_storage_path
except Exception:
    # Lets you run/test the UI on desktop without Android-only libs installed.
    ANDROID = False


APP_FOLDER_NAME = "CameraApp"

# --- Color palette ---
COLOR_BG = (0.07, 0.08, 0.10, 1)
COLOR_ACCENT = (0.20, 0.55, 0.95, 1)
COLOR_ACCENT_DARK = (0.15, 0.42, 0.75, 1)
COLOR_SUCCESS = (0.18, 0.60, 0.32, 1)
COLOR_DANGER = (0.75, 0.24, 0.24, 1)
COLOR_MUTED = (0.24, 0.26, 0.30, 1)
COLOR_TEXT = (0.92, 0.93, 0.95, 1)
COLOR_TEXT_MUTED = (0.62, 0.65, 0.70, 1)


def styled_button(text, bg=COLOR_MUTED, color=COLOR_TEXT, font_size='14sp', bold=False):
    """Flat-colored button (Kivy's default button has a themed texture
    that tints background_color rather than showing it directly - setting
    background_normal/background_down to '' gives a true flat color)."""
    return Button(
        text=text,
        background_normal='',
        background_down='',
        background_color=bg,
        color=color,
        font_size=font_size,
        bold=bold,
    )


class RotatedCameraBox(BoxLayout):
    """
    Wraps a Camera widget and applies a canvas rotation to correct for
    the physical sensor mount orientation.

    Capturing is done via export_to_png() on THIS widget (not the raw
    camera texture) - see LiveCameraScreen.capture() - so the saved
    photo always matches exactly what's rendered on screen. There is a
    single source of truth for orientation (this canvas transform),
    not two separate code paths (display vs. save) that can disagree.
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
        share_intent.setType("image/png")
        share_intent.putExtra(Intent.EXTRA_STREAM, uri)
        share_intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

        chooser = Intent.createChooser(share_intent, "Share photo via")
        activity.startActivity(chooser)
    except Exception as e:
        print("Share failed:", e)


class HomeScreen(Screen):
    """Landing screen: pick a camera mode, or exit the app."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        root = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(10))
        with root.canvas.before:
            Color(*COLOR_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        root.add_widget(BoxLayout(size_hint_y=0.12))

        title = Label(
            text="Ganesh Camera App", font_size='24sp', bold=True,
            color=COLOR_TEXT, size_hint_y=None, height=dp(40),
        )
        subtitle = Label(
            text="Choose a camera mode", font_size='13sp',
            color=COLOR_TEXT_MUTED, size_hint_y=None, height=dp(24),
        )
        root.add_widget(title)
        root.add_widget(subtitle)

        root.add_widget(BoxLayout(size_hint_y=0.08))

        btn_back = styled_button("Back Camera", bg=COLOR_ACCENT, font_size='16sp', bold=True)
        btn_back.size_hint_y = None
        btn_back.height = dp(56)
        btn_back.bind(on_release=lambda *_: self.go_to_camera("back"))

        btn_front = styled_button("Front Camera", bg=COLOR_ACCENT, font_size='16sp', bold=True)
        btn_front.size_hint_y = None
        btn_front.height = dp(56)
        btn_front.bind(on_release=lambda *_: self.go_to_camera("front"))

        btn_dual = styled_button("Dual Camera", bg=COLOR_ACCENT_DARK, font_size='16sp', bold=True)
        btn_dual.size_hint_y = None
        btn_dual.height = dp(56)
        btn_dual.bind(on_release=lambda *_: self.go_to_camera("dual"))

        for b in (btn_back, btn_front, btn_dual):
            root.add_widget(b)
            root.add_widget(BoxLayout(size_hint_y=None, height=dp(10)))

        root.add_widget(BoxLayout())  # flexible spacer pushes Exit to the bottom

        btn_exit = styled_button("Exit App", bg=COLOR_DANGER, font_size='14sp')
        btn_exit.size_hint_y = None
        btn_exit.height = dp(46)
        btn_exit.bind(on_release=lambda *_: self.exit_app())
        root.add_widget(btn_exit)

        self.add_widget(root)

    def _update_bg(self, instance, *args):
        self._bg_rect.pos = instance.pos
        self._bg_rect.size = instance.size

    def go_to_camera(self, facing):
        camera_screen = self.manager.get_screen("camera")
        camera_screen.start(facing)
        self.manager.transition = SlideTransition(direction="left")
        self.manager.current = "camera"

    def exit_app(self):
        App.get_running_app().stop()
        if ANDROID:
            try:
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                PythonActivity.mActivity.finishAndRemoveTask()
            except Exception as e:
                print("Exit failed:", e)


class LiveCameraScreen(Screen):
    """Live camera preview with rotate tuner(s), Capture, Share, and a
    button back to the Home screen."""

    BUTTON_HEIGHT = dp(40)
    FONT_SIZE = '13sp'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.facing = "back"
        self.last_saved_path = None
        self.active_cameras = []
        self.active_boxes = []

        # Persistent per-camera rotation - set once via the tuner and
        # kept across mode switches (this used to reset to 0 every time
        # you changed modes, which made tuning pointless; fixed now).
        self.rotation_back = 0
        self.rotation_front = 0

        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(*COLOR_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        top_bar = BoxLayout(
            size_hint_y=None, height=self.BUTTON_HEIGHT + dp(12),
            padding=(6, 4), spacing=6,
        )
        btn_home = styled_button("< Home", font_size=self.FONT_SIZE)
        btn_home.size_hint_x = 0.32
        btn_home.size_hint_y = None
        btn_home.height = self.BUTTON_HEIGHT
        btn_home.bind(on_release=lambda *_: self.go_home())

        self.status_label = Label(text="", font_size=self.FONT_SIZE, color=COLOR_TEXT)
        top_bar.add_widget(btn_home)
        top_bar.add_widget(self.status_label)

        self.camera_container = BoxLayout(orientation="horizontal")

        # Rotate tuner row(s) - one row normally, two rows (independent
        # Back/Front controls) when in Dual mode. Rebuilt in start().
        self.tuner_container = BoxLayout(orientation="vertical", size_hint_y=None)

        bottom_bar = BoxLayout(
            size_hint_y=None, height=self.BUTTON_HEIGHT + dp(16),
            spacing=8, padding=(8, 6),
        )
        btn_capture = styled_button("\u25CF  Capture", bg=COLOR_SUCCESS, font_size=self.FONT_SIZE, bold=True)
        btn_capture.size_hint_y = None
        btn_capture.height = self.BUTTON_HEIGHT
        btn_capture.bind(on_release=lambda *_: self.capture())

        btn_share = styled_button("Share last", bg=COLOR_ACCENT, font_size=self.FONT_SIZE)
        btn_share.size_hint_y = None
        btn_share.height = self.BUTTON_HEIGHT
        btn_share.bind(on_release=lambda *_: self.share_last())

        bottom_bar.add_widget(btn_capture)
        bottom_bar.add_widget(btn_share)

        root.add_widget(top_bar)
        root.add_widget(self.camera_container)
        root.add_widget(self.tuner_container)
        root.add_widget(bottom_bar)
        self.add_widget(root)

    def _update_bg(self, instance, *args):
        self._bg_rect.pos = instance.pos
        self._bg_rect.size = instance.size

    # ---------- lifecycle ----------

    def start(self, facing):
        self.facing = facing
        self._rebuild_tuner_rows()
        self._open_cameras(facing)

    def go_home(self):
        self._clear_cameras()
        self.manager.transition = SlideTransition(direction="right")
        self.manager.current = "home"

    # ---------- camera management ----------

    def _clear_cameras(self):
        for child in list(self.camera_container.children):
            if isinstance(child, RotatedCameraBox):
                child.camera.play = False
            self.camera_container.remove_widget(child)
        self.active_cameras = []
        self.active_boxes = []

    def _open_cameras(self, facing):
        self._clear_cameras()

        if facing == "back":
            box = RotatedCameraBox(index=0, resolution=(1280, 720), rotation=self.rotation_back)
            self.camera_container.add_widget(box)
            self.active_cameras = [box.camera]
            self.active_boxes = [box]
            self.status_label.text = "Back camera"

        elif facing == "front":
            box = RotatedCameraBox(index=1, resolution=(1280, 720), rotation=self.rotation_front)
            self.camera_container.add_widget(box)
            self.active_cameras = [box.camera]
            self.active_boxes = [box]
            self.status_label.text = "Front camera"

        elif facing == "dual":
            try:
                box_back = RotatedCameraBox(index=0, resolution=(640, 480), rotation=self.rotation_back)
                box_front = RotatedCameraBox(index=1, resolution=(640, 480), rotation=self.rotation_front)
                self.camera_container.add_widget(box_back)
                self.camera_container.add_widget(box_front)
                self.active_cameras = [box_back.camera, box_front.camera]
                self.active_boxes = [box_back, box_front]
                self.status_label.text = "Dual camera"
            except Exception as e:
                print("Dual camera open failed, falling back to back:", e)
                self.facing = "back"
                box = RotatedCameraBox(index=0, resolution=(1280, 720), rotation=self.rotation_back)
                self.camera_container.add_widget(box)
                self.active_cameras = [box.camera]
                self.active_boxes = [box]
                self.status_label.text = "Back (dual unsupported)"
                self._rebuild_tuner_rows()

    # ---------- rotation tuner ----------

    def _rebuild_tuner_rows(self):
        self.tuner_container.clear_widgets()

        if self.facing == "dual":
            rows = [("back", "Back rot"), ("front", "Front rot")]
        else:
            rows = [(self.facing, "Rotate")]

        self.tuner_container.height = (self.BUTTON_HEIGHT + dp(10)) * len(rows)

        for cam_key, label_text in rows:
            row = BoxLayout(
                size_hint_y=None, height=self.BUTTON_HEIGHT + dp(10),
                spacing=4, padding=(6, 2),
            )
            current = self.rotation_back if cam_key == "back" else self.rotation_front
            readout = Label(
                text=f"{label_text}: {int(current)}\u00b0", size_hint_x=0.34,
                font_size=self.FONT_SIZE, color=COLOR_TEXT_MUTED,
            )
            btn_minus = styled_button("-45\u00b0", font_size=self.FONT_SIZE)
            btn_minus.bind(on_release=lambda *_, k=cam_key: self.adjust_rotation(k, -45))

            btn_reset = styled_button("Reset", font_size=self.FONT_SIZE)
            btn_reset.bind(on_release=lambda *_, k=cam_key: self.adjust_rotation(k, 0, reset=True))

            btn_plus = styled_button("+45\u00b0", font_size=self.FONT_SIZE)
            btn_plus.bind(on_release=lambda *_, k=cam_key: self.adjust_rotation(k, 45))

            for w in (readout, btn_minus, btn_reset, btn_plus):
                w.size_hint_y = None
                w.height = self.BUTTON_HEIGHT
                row.add_widget(w)

            row.readout_label = readout
            row.cam_key = cam_key
            row.label_text = label_text
            self.tuner_container.add_widget(row)

    def adjust_rotation(self, cam_key, delta, reset=False):
        current = self.rotation_back if cam_key == "back" else self.rotation_front
        new_val = 0 if reset else (current + delta) % 360
        if cam_key == "back":
            self.rotation_back = new_val
        else:
            self.rotation_front = new_val

        target_index = 0 if cam_key == "back" else 1
        for box in self.active_boxes:
            if box.camera.index == target_index:
                box._rotate.angle = new_val

        for row in self.tuner_container.children:
            if getattr(row, "cam_key", None) == cam_key:
                row.readout_label.text = f"{row.label_text}: {int(new_val)}\u00b0"

    # ---------- capture / share ----------

    def capture(self):
        boxes = self.active_boxes
        if not boxes:
            return

        save_dir = get_save_dir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        saved = []

        for i, box in enumerate(boxes):
            suffix = f"_{i}" if len(boxes) > 1 else ""
            filename = f"IMG_{timestamp}{suffix}.png"
            filepath = os.path.join(save_dir, filename)
            # Renders exactly what's on screen for this widget, including
            # the canvas Rotate correction - guaranteed to match the live
            # preview, since it's the same canvas being rendered.
            box.export_to_png(filepath)
            notify_gallery(filepath)
            saved.append(filepath)

        if saved:
            self.last_saved_path = saved[-1]
            preview_screen = self.manager.get_screen("preview")
            preview_screen.show(saved[-1])
            self.manager.transition = SlideTransition(direction="up")
            self.manager.current = "preview"

    def share_last(self):
        if not self.last_saved_path:
            self._toast("No photo captured yet")
            return
        share_file(self.last_saved_path)

    def _toast(self, message):
        popup = Popup(title="", content=Label(text=message), size_hint=(0.8, 0.2))
        popup.open()
        Clock.schedule_once(lambda *_: popup.dismiss(), 1.5)


class PhotoPreviewScreen(Screen):
    """Full-size view of the just-captured photo, with a button back to
    the live camera and a Share button."""

    BUTTON_HEIGHT = dp(42)
    FONT_SIZE = '13sp'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.filepath = None

        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(*COLOR_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        self.image = Image(allow_stretch=True, keep_ratio=True)

        controls = BoxLayout(
            size_hint_y=None, height=self.BUTTON_HEIGHT + dp(16),
            spacing=8, padding=(8, 6),
        )
        btn_back = styled_button("< Back to camera", font_size=self.FONT_SIZE)
        btn_back.size_hint_y = None
        btn_back.height = self.BUTTON_HEIGHT
        btn_back.bind(on_release=lambda *_: self.go_back())

        btn_share = styled_button("Share", bg=COLOR_ACCENT, font_size=self.FONT_SIZE, bold=True)
        btn_share.size_hint_y = None
        btn_share.height = self.BUTTON_HEIGHT
        btn_share.bind(on_release=lambda *_: self.share())

        controls.add_widget(btn_back)
        controls.add_widget(btn_share)

        root.add_widget(self.image)
        root.add_widget(controls)
        self.add_widget(root)

    def _update_bg(self, instance, *args):
        self._bg_rect.pos = instance.pos
        self._bg_rect.size = instance.size

    def show(self, filepath):
        self.filepath = filepath
        self.image.source = filepath
        self.image.reload()

    def go_back(self):
        self.manager.transition = SlideTransition(direction="down")
        self.manager.current = "camera"

    def share(self):
        if self.filepath:
            share_file(self.filepath)


class GaneshCameraApp(App):
    title = "GaneshCameraApp"

    def build(self):
        ensure_permissions()
        Window.clearcolor = COLOR_BG

        sm = ScreenManager(transition=NoTransition())
        sm.add_widget(HomeScreen(name="home"))
        sm.add_widget(LiveCameraScreen(name="camera"))
        sm.add_widget(PhotoPreviewScreen(name="preview"))
        sm.current = "home"
        return sm


if __name__ == "__main__":
    GaneshCameraApp().run()
