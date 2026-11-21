"""Renders assets/icon.png, icon.ico and (on macOS) icon.icns from tokenwatt/gui/icon.py."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from tokenwatt.gui.icon import render  # noqa: E402

out = Path(__file__).resolve().parent.parent / "assets"
out.mkdir(exist_ok=True)
app = QGuiApplication(sys.argv)

render(1024).save(str(out / "icon.png"))
render(256).save(str(out / "icon.ico"), "ICO")

if sys.platform == "darwin":
    iconset = out / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        render(size).save(str(iconset / "icon_{0}x{0}.png".format(size)))
        render(size * 2).save(str(iconset / "icon_{0}x{0}@2x.png".format(size)))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "icon.icns")], check=True)
    shutil.rmtree(iconset)
print("icons written to", out)
