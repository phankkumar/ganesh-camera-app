"""
GaneshCameraApp - Android camera capture app built with Kivy.

Three-screen flow:
  Home        -> pick Back / Front / Dual camera, or Exit
  Camera      -> live preview, rotate tuner(s), Capture, Share, Home
  Preview     -> full-size view of the captured photo/photos, Back / Share

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
- In Dual mode, BOTH captured photos are saved and both are shown on
  the preview screen (side by side), with a single Share that sends
  both files together
- Responsive layout: sizing is pulled from a DEVICE_PROFILES table
  keyed by a runtime screen-size classification (phone / small tablet /
  large tablet, using the same sw600dp/sw720dp breakpoints Android
  itself uses) - adding support for a new device class later is one
  new dict entry, not a rewrite of every screen
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


# ============================================================
# Device profile system - phone vs. tablet responsive sizing
# ============================================================
#
# Classifies the current screen at runtime using "shortest side in dp",
# the same convention Android itself uses for sw600dp/sw720dp resource
# qualifiers. To support a new device class later (a foldable's unfolded
# state, a Chromebook window, a larger tablet tier, etc.), add one entry
# to DEVICE_PROFILES and, if needed, one more breakpoint in
# classify_device() - no other screen code needs to change, since every
# screen reads its sizing from get_profile() rather than hardcoding
# dp()/sp() values directly.

DEVICE_PROFILES = {
    "phone": {
        "button_height": dp(40),
        "font_size": '13sp',
        "title_font_size": '24sp',
        "home_button_height": dp(56),
        "home_button_width_hint": 1.0,   # full width on phones
        "padding": dp(24),
        "spacing": dp(10),
        "tuner_font_size": '12sp',
    },
    "tablet_small": {
        # e.g. Samsung Galaxy Tab A7 Lite (8.7")
        "button_height": dp(52),
        "font_size": '16sp',
        "title_font_size": '30sp',
        "home_button_height": dp(70),
        "home_button_width_hint": 0.65,  # centered, not edge-to-edge
        "padding": dp(36),
        "spacing": dp(14),
        "tuner_font_size": '15sp',
    },
    "tablet_large": {
        # e.g. Samsung Galaxy Tab A7 (10.4")
        "button_height": dp(60),
        "font_size": '18sp',
        "title_font_size": '36sp',
        "home_button_height": dp(80),
        "home_button_width_hint": 0.5,
        "padding": dp(48),
        "spacing": dp(18),
        "tuner_font_size": '17sp',
    },
}


def _shortest_side_dp():
    density = (Window.dpi / 160.0) if Window.dpi else 1.0
    shortest_px = min(Window.width, Window.height)
    return shortest_px / density


def classify_device():
    """Returns 'phone', 'tablet_small', or 'tablet_large' based on the
    current window's shortest side in dp (mirrors Android's own
    sw600dp / sw720dp breakpoints)."""
    shortest = _shortest_side_dp()
    if shortest >= 720:
        return "tablet_large"
    elif shortest >= 600:
        return "tablet_small"
    else:
        return "phone"


def get_profile():
    return DEVICE_PROFILES[classify_device()]


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
    """Open the native Android share sheet for a single file."""
    if not ANDROID:
        print(f"[desktop stub] would share: {filepath}")
        return
    try:
        Intent = autoclass("android.content.Intent")
        File = autoclass("java.io.File")
        FileProvider = autoclass("androidx.core.content.FileProvider")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")

        activity = PythonActivity.mActivity
        f = File(filepath)

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


def share_files(filepaths):
    """Open the native Android share sheet for multiple files at once
    (e.g. both photos from a Dual-mode capture), via ACTION_SEND_MULTIPLE."""
    if not filepaths:
        return
    if len(filepaths) == 1:
        share_file(filepaths[0])
        return
    if not ANDROID:
        print(f"[desktop stub] would share multiple: {filepaths}")
        return
    try:
        Intent = autoclass("android.content.Intent")
        File = autoclass("java.io.File")
        FileProvider = autoclass("androidx.core.content.FileProvider")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        ArrayList = autoclass("java.util.ArrayList")

        activity = PythonActivity.mActivity
        authority = activity.getPackageName() + ".fileprovider"

        uris = ArrayList()
        for path in filepaths:
            f = File(path)
            uri = FileProvider.getUriForFile(activity, authority, f)
            uris.add(uri)

        share_intent = Intent(Intent.ACTION_SEND_MULTIPLE)
        share_intent.setType("image/png")
        share_intent.putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
        share_intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

        chooser = Intent.createChooser(share_intent, "Share photos via")
        activity.startActivity(chooser)
    except Exception as e:
        print("Multi-share failed:", e)


# Package names for direct-share targets (bypassing the generic chooser).
PACKAGE_WHATSAPP = "com.whatsapp"
PACKAGE_GMAIL = "com.google.android.gm"


def share_files_to_package(filepaths, package_name):
    """
    Share one or more files directly to a specific app (e.g. WhatsApp,
    Gmail) via Intent.setPackage(), skipping the generic chooser. Falls
    back to the generic chooser if the target app isn't installed or the
    direct intent fails for any reason.
    """
    if not filepaths:
        return
    if not ANDROID:
        print(f"[desktop stub] would share {filepaths} directly to {package_name}")
        return
    try:
        Intent = autoclass("android.content.Intent")
        File = autoclass("java.io.File")
        FileProvider = autoclass("androidx.core.content.FileProvider")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        ArrayList = autoclass("java.util.ArrayList")

        activity = PythonActivity.mActivity
        authority = activity.getPackageName() + ".fileprovider"

        if len(filepaths) == 1:
            intent = Intent(Intent.ACTION_SEND)
            f = File(filepaths[0])
            uri = FileProvider.getUriForFile(activity, authority, f)
            intent.putExtra(Intent.EXTRA_STREAM, uri)
        else:
            intent = Intent(Intent.ACTION_SEND_MULTIPLE)
            uris = ArrayList()
            for path in filepaths:
                f = File(path)
                uri = FileProvider.getUriForFile(activity, authority, f)
                uris.add(uri)
            intent.putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)

        intent.setType("image/png")
        intent.setPackage(package_name)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        activity.startActivity(intent)
    except Exception as e:
        print(f"Direct share to {package_name} failed, falling back to chooser:", e)
        share_files(filepaths)


class HomeScreen(Screen):
    """Landing screen: pick a camera mode, or exit the app."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        p = get_profile()

        root = BoxLayout(orientation="vertical", padding=p["padding"], spacing=p["spacing"])
        with root.canvas.before:
            Color(*COLOR_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        root.add_widget(BoxLayout(size_hint_y=0.12))

        title = Label(
            text="Ganesh Camera App", font_size=p["title_font_size"], bold=True,
            color=COLOR_TEXT, size_hint_y=None, height=dp(48),
        )
        subtitle = Label(
            text="Choose a camera mode", font_size=p["font_size"],
            color=COLOR_TEXT_MUTED, size_hint_y=None, height=dp(28),
        )
        root.add_widget(title)
        root.add_widget(subtitle)

        root.add_widget(BoxLayout(size_hint_y=0.08))

        def make_mode_button(text, bg):
            """Wraps each mode button in a horizontally-centered row, so
            on tablets (home_button_width_hint < 1.0) buttons don't
            stretch edge-to-edge - they stay a comfortable, centered
            width instead."""
            row = BoxLayout(size_hint_y=None, height=p["home_button_height"])
            btn = styled_button(text, bg=bg, font_size=p["font_size"], bold=True)
            btn.size_hint_x = p["home_button_width_hint"]
            btn.pos_hint = {"center_x": 0.5}
            row.add_widget(btn)
            return row, btn

        row_back, btn_back = make_mode_button("Back Camera", COLOR_ACCENT)
        btn_back.bind(on_release=lambda *_: self.go_to_camera("back"))

        row_front, btn_front = make_mode_button("Front Camera", COLOR_ACCENT)
        btn_front.bind(on_release=lambda *_: self.go_to_camera("front"))

        row_dual, btn_dual = make_mode_button("Dual Camera", COLOR_ACCENT_DARK)
        btn_dual.bind(on_release=lambda *_: self.go_to_camera("dual"))

        for row in (row_back, row_front, row_dual):
            root.add_widget(row)
            root.add_widget(BoxLayout(size_hint_y=None, height=p["spacing"]))

        root.add_widget(BoxLayout())  # flexible spacer pushes Exit to the bottom

        exit_row = BoxLayout(size_hint_y=None, height=dp(46))
        btn_exit = styled_button("Exit App", bg=COLOR_DANGER, font_size=p["font_size"])
        btn_exit.size_hint_x = p["home_button_width_hint"]
        btn_exit.pos_hint = {"center_x": 0.5}
        btn_exit.bind(on_release=lambda *_: self.exit_app())
        exit_row.add_widget(btn_exit)
        root.add_widget(exit_row)

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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.profile = get_profile()
        self.BUTTON_HEIGHT = self.profile["button_height"]
        self.FONT_SIZE = self.profile["font_size"]
        self.TUNER_FONT_SIZE = self.profile["tuner_font_size"]

        self.facing = "back"
        self.last_saved_paths = []
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

        # Rotate tuner row(s) - one row normally, two independent rows
        # (Back Rotate / Front Rotate) when in Dual mode. Rebuilt in start().
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
                # Lower resolution than single-camera mode - two
                # concurrent streams sharing the same image signal
                # processor is inherently more contended than one, and a
                # smaller frame size reduces the bandwidth each stream
                # is fighting the other for, which can reduce (though not
                # fully eliminate) preview flicker on devices where the
                # hardware doesn't cleanly support two live streams.
                box_back = RotatedCameraBox(index=0, resolution=(480, 360), rotation=self.rotation_back)
                box_front = RotatedCameraBox(index=1, resolution=(480, 360), rotation=self.rotation_front)
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
            # Short, explicit prefix (B/F) on every button in each row -
            # not just the readout label - so it's unambiguous which row
            # controls which camera even at a glance, since the two rows
            # sit close together in Dual mode.
            rows = [("back", "Back", "B")]
            rows.append(("front", "Front", "F"))
        else:
            rows = [(self.facing, "Rotate", "")]

        self.tuner_container.height = (self.BUTTON_HEIGHT + dp(10)) * len(rows)

        for cam_key, label_text, short_prefix in rows:
            row = BoxLayout(
                size_hint_y=None, height=self.BUTTON_HEIGHT + dp(10),
                spacing=4, padding=(6, 2),
            )
            current = self.rotation_back if cam_key == "back" else self.rotation_front
            readout = Label(
                text=f"{label_text}: {int(current)}\u00b0", size_hint_x=0.38,
                font_size=self.TUNER_FONT_SIZE, color=COLOR_TEXT_MUTED,
            )
            prefix = f"{short_prefix} " if short_prefix else ""
            btn_minus = styled_button(f"{prefix}-45\u00b0", font_size=self.TUNER_FONT_SIZE)
            btn_minus.bind(on_release=lambda *_, k=cam_key: self.adjust_rotation(k, -45))

            btn_reset = styled_button(f"{prefix}Reset" if prefix else "Reset", font_size=self.TUNER_FONT_SIZE)
            btn_reset.bind(on_release=lambda *_, k=cam_key: self.adjust_rotation(k, 0, reset=True))

            btn_plus = styled_button(f"{prefix}+45\u00b0", font_size=self.TUNER_FONT_SIZE)
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

        self._pending_save_dir = get_save_dir()
        self._pending_timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._pending_boxes = list(enumerate(boxes))
        self._pending_multi = len(boxes) > 1
        self._pending_saved = []

        if self._pending_multi:
            # Pause every camera up front. Each one gets resumed alone,
            # briefly, right before its own capture below - see the
            # explanation in _capture_next_box.
            for box in boxes:
                box.camera.play = False

        self._capture_next_box()

    def _capture_next_box(self, *_args):
        """
        Captures one camera at a time. In Dual mode, this also pauses
        every OTHER camera before capturing this one, then briefly waits
        before reading its texture.

        Why: most phones don't reliably support two truly concurrent
        camera hardware streams via the legacy Camera API this widget
        uses - the OS hands exclusive access back and forth between them
        rather than genuinely running both at once. A fixed delay alone
        (the previous fix) mostly worked but could still occasionally
        catch a camera mid-handoff, producing the same (usually front)
        image for both files. Explicitly pausing the other camera(s)
        removes the guesswork - only one stream is actually requesting
        hardware access at the moment we capture, so there's nothing
        for it to lose a handoff race against.
        """
        if not self._pending_boxes:
            if self._pending_multi:
                for box in self.active_boxes:
                    box.camera.play = True
            if self._pending_saved:
                self.last_saved_paths = self._pending_saved
                preview_screen = self.manager.get_screen("preview")
                preview_screen.show(self._pending_saved)
                self.manager.transition = SlideTransition(direction="up")
                self.manager.current = "preview"
            return

        i, box = self._pending_boxes.pop(0)

        if self._pending_multi:
            box.camera.play = True
            Clock.schedule_once(lambda dt: self._do_capture(i, box), 0.5)
        else:
            self._do_capture(i, box)

    def _do_capture(self, i, box):
        suffix = f"_{i}" if self._pending_multi else ""
        filename = f"IMG_{self._pending_timestamp}{suffix}.png"
        filepath = os.path.join(self._pending_save_dir, filename)
        # Renders exactly what's on screen for this widget, including
        # the canvas Rotate correction - guaranteed to match the live
        # preview, since it's the same canvas being rendered.
        box.export_to_png(filepath)
        notify_gallery(filepath)
        self._pending_saved.append(filepath)

        if self._pending_multi:
            box.camera.play = False

        if self._pending_boxes:
            Clock.schedule_once(self._capture_next_box, 0.4)
        else:
            self._capture_next_box()

    def share_last(self):
        if not self.last_saved_paths:
            self._toast("No photo captured yet")
            return
        share_files(self.last_saved_paths)

    def _toast(self, message):
        popup = Popup(title="", content=Label(text=message), size_hint=(0.8, 0.2))
        popup.open()
        Clock.schedule_once(lambda *_: popup.dismiss(), 1.5)


class PhotoPreviewScreen(Screen):
    """Full-size view of the just-captured photo(s). Shows a single
    image normally, or two side by side after a Dual-mode capture, with
    a button back to the live camera and a Share (both, if two)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        p = get_profile()
        self.BUTTON_HEIGHT = p["button_height"]
        self.FONT_SIZE = p["font_size"]

        self.filepaths = []

        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(*COLOR_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        # Image area - rebuilt in show() depending on 1 vs 2 photos
        self.image_container = BoxLayout(orientation="horizontal", spacing=dp(4))

        controls = BoxLayout(
            size_hint_y=None, height=self.BUTTON_HEIGHT + dp(16),
            spacing=6, padding=(8, 6),
        )
        btn_back = styled_button("< Back", font_size=self.FONT_SIZE)
        btn_back.size_hint_y = None
        btn_back.height = self.BUTTON_HEIGHT
        btn_back.bind(on_release=lambda *_: self.go_back())

        btn_whatsapp = styled_button("WhatsApp", bg=(0.15, 0.55, 0.35, 1), font_size=self.FONT_SIZE)
        btn_whatsapp.size_hint_y = None
        btn_whatsapp.height = self.BUTTON_HEIGHT
        btn_whatsapp.bind(on_release=lambda *_: self.share_to(PACKAGE_WHATSAPP))

        btn_gmail = styled_button("Gmail", bg=(0.75, 0.30, 0.20, 1), font_size=self.FONT_SIZE)
        btn_gmail.size_hint_y = None
        btn_gmail.height = self.BUTTON_HEIGHT
        btn_gmail.bind(on_release=lambda *_: self.share_to(PACKAGE_GMAIL))

        self.btn_share = styled_button("More", bg=COLOR_ACCENT, font_size=self.FONT_SIZE, bold=True)
        self.btn_share.size_hint_y = None
        self.btn_share.height = self.BUTTON_HEIGHT
        self.btn_share.bind(on_release=lambda *_: self.share())

        controls.add_widget(btn_back)
        controls.add_widget(btn_whatsapp)
        controls.add_widget(btn_gmail)
        controls.add_widget(self.btn_share)

        root.add_widget(self.image_container)
        root.add_widget(controls)
        self.add_widget(root)

    def _update_bg(self, instance, *args):
        self._bg_rect.pos = instance.pos
        self._bg_rect.size = instance.size

    def show(self, filepaths):
        self.filepaths = filepaths
        self.image_container.clear_widgets()

        labels = ["Back", "Front"] if len(filepaths) == 2 else [None]
        for path, label in zip(filepaths, labels):
            col = BoxLayout(orientation="vertical")
            if label:
                col.add_widget(Label(
                    text=label, size_hint_y=None, height=dp(22),
                    font_size=self.FONT_SIZE, color=COLOR_TEXT_MUTED,
                ))
            img = Image(source=path, allow_stretch=True, keep_ratio=True)
            col.add_widget(img)
            self.image_container.add_widget(col)

        self.btn_share.text = "More" if len(filepaths) == 1 else "More"

    def go_back(self):
        self.manager.transition = SlideTransition(direction="down")
        self.manager.current = "camera"

    def share(self):
        if self.filepaths:
            share_files(self.filepaths)

    def share_to(self, package_name):
        if self.filepaths:
            share_files_to_package(self.filepaths, package_name)


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
