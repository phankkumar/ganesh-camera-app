"""
CameraApp (Desktop / Windows) - pure Python, no VM/emulator needed.

Run this to iterate on the camera logic quickly before porting to the
Android build (main.py). Uses OpenCV for capture and Tkinter (built into
Python) for the UI - no compiler, no extra system packages.

Install:
    pip install opencv-python pillow

Run:
    python capture_app.py
"""

import os
import sys
import time
import subprocess
import tkinter as tk
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk

APP_FOLDER_NAME = "CameraApp"
MAX_CAMERAS_TO_PROBE = 4
FRAME_DELAY_MS = 30  # ~33 fps preview refresh


def find_available_cameras(max_index=MAX_CAMERAS_TO_PROBE):
    """
    Probe camera indices 0..max_index and return the ones that actually
    open and deliver a frame. This is our 'feature check' - equivalent
    in spirit to Android's <uses-feature> hardware check.
    """
    found = []
    for idx in range(max_index):
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)  # CAP_DSHOW = fast open on Windows
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                found.append(idx)
        cap.release()
    return found


def get_save_dir():
    path = os.path.join(os.path.expanduser("~"), "Pictures", APP_FOLDER_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def reveal_in_explorer(filepath):
    """
    Windows equivalent of a 'share sheet': open Explorer with the file
    highlighted, so the user can right-click -> Share / Send to / etc.
    """
    filepath = os.path.normpath(filepath)
    subprocess.run(["explorer", "/select,", filepath])


class CameraSource:
    """Wraps a single cv2.VideoCapture with a friendly label."""

    def __init__(self, index):
        self.index = index
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        self.label = f"Camera {index}"

    def read(self):
        if not self.cap or not self.cap.isOpened():
            return None
        ok, frame = self.cap.read()
        if not ok:
            return None
        return frame

    def release(self):
        if self.cap:
            self.cap.release()


class CameraApp:
    def __init__(self, root, camera_indices):
        self.root = root
        self.root.title("CameraApp (Desktop)")

        self.available_indices = camera_indices
        self.sources = []  # active CameraSource objects, 1 or 2 depending on mode
        self.last_saved_paths = []
        self.dual_mode = False

        self._build_ui()
        self._activate(single_index=self.available_indices[0])
        self._update_frame()

    # ---------- UI ----------

    def _build_ui(self):
        top = tk.Frame(self.root)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=8)

        tk.Label(top, text="Camera:").pack(side=tk.LEFT)

        self.camera_var = tk.StringVar(value=str(self.available_indices[0]))
        for idx in self.available_indices:
            b = tk.Radiobutton(
                top, text=f"Camera {idx}", variable=self.camera_var,
                value=str(idx), command=self._on_single_select
            )
            b.pack(side=tk.LEFT, padx=4)

        if len(self.available_indices) >= 2:
            dual_btn = tk.Button(top, text="Dual view", command=self._on_dual_select)
            dual_btn.pack(side=tk.LEFT, padx=12)

        self.status_label = tk.Label(top, text="", fg="gray")
        self.status_label.pack(side=tk.RIGHT)

        # Video preview area - one or two panels side by side
        self.preview_frame = tk.Frame(self.root)
        self.preview_frame.pack(side=tk.TOP, padx=8, pady=4)
        self.panels = []  # tk.Label widgets that display frames

        bottom = tk.Frame(self.root)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)

        tk.Button(
            bottom, text="Capture", bg="#2e7d32", fg="white",
            command=self._capture, width=12
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            bottom, text="Share last photo", command=self._share_last, width=16
        ).pack(side=tk.LEFT, padx=4)

        self.save_dir_label = tk.Label(bottom, text=f"Saving to: {get_save_dir()}", fg="gray")
        self.save_dir_label.pack(side=tk.RIGHT)

    def _rebuild_panels(self, count):
        for p in self.panels:
            p.destroy()
        self.panels = []
        for _ in range(count):
            panel = tk.Label(self.preview_frame)
            panel.pack(side=tk.LEFT, padx=4)
            self.panels.append(panel)

    # ---------- Camera activation ----------

    def _release_all(self):
        for s in self.sources:
            s.release()
        self.sources = []

    def _activate(self, single_index=None):
        self._release_all()
        self.dual_mode = False
        self.sources = [CameraSource(single_index)]
        self._rebuild_panels(1)
        self.status_label.config(text=f"Showing Camera {single_index}")

    def _activate_dual(self):
        self._release_all()
        self.dual_mode = True
        idx_a, idx_b = self.available_indices[0], self.available_indices[1]
        self.sources = [CameraSource(idx_a), CameraSource(idx_b)]
        self._rebuild_panels(2)
        self.status_label.config(text=f"Dual: Camera {idx_a} + Camera {idx_b}")

    def _on_single_select(self):
        self._activate(single_index=int(self.camera_var.get()))

    def _on_dual_select(self):
        self._activate_dual()

    # ---------- Frame loop ----------

    def _update_frame(self):
        for source, panel in zip(self.sources, self.panels):
            frame = source.read()
            if frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                img.thumbnail((480, 360))
                imgtk = ImageTk.PhotoImage(image=img)
                panel.imgtk = imgtk  # keep a reference, tkinter needs it
                panel.configure(image=imgtk)
        self.root.after(FRAME_DELAY_MS, self._update_frame)

    # ---------- Capture / Save / Share ----------

    def _capture(self):
        save_dir = get_save_dir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        saved = []

        for i, source in enumerate(self.sources):
            frame = source.read()
            if frame is None:
                continue
            suffix = f"_{i}" if len(self.sources) > 1 else ""
            filename = f"IMG_{timestamp}{suffix}.jpg"
            filepath = os.path.join(save_dir, filename)
            cv2.imwrite(filepath, frame)
            saved.append(filepath)

        if saved:
            self.last_saved_paths = saved
            self.status_label.config(text=f"Saved {len(saved)} photo(s)")
            messagebox.showinfo("Saved", f"Saved to:\n" + "\n".join(saved))
        else:
            messagebox.showerror("Capture failed", "Could not read a frame from the camera.")

    def _share_last(self):
        if not self.last_saved_paths:
            messagebox.showwarning("No photo", "Capture a photo first.")
            return
        # Reveal the most recent file in Explorer; user right-clicks -> Share
        reveal_in_explorer(self.last_saved_paths[-1])

    def on_close(self):
        self._release_all()
        self.root.destroy()


def main():
    print("Checking for available cameras...")
    cameras = find_available_cameras()

    if not cameras:
        # Desktop equivalent of Android's install-time feature gate:
        # refuse to proceed at all if the required hardware is missing.
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "No camera found",
            "This app requires at least one camera, and none was detected.\n\n"
            "Check that a webcam is connected and not in use by another app, "
            "then restart."
        )
        sys.exit(1)

    print(f"Found cameras: {cameras}")
    root = tk.Tk()
    app = CameraApp(root, cameras)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
