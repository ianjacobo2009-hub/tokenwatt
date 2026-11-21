# PyInstaller spec: builds tokenwatt.app (macOS) or tokenwatt.exe (Windows).
# Run from the repo root:  pyinstaller --noconfirm tokenwatt.spec
import sys

# Qt pieces the app never touches; leaving them out keeps the download small.
qt_excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtBluetooth", "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtOpenGL", "PySide6.QtSvg", "PySide6.QtDesigner",
]

a = Analysis(
    ["run_app.py"],
    pathex=["src"],
    excludes=["tkinter", "matplotlib", "numpy", "IPython", "pytest"] + qt_excludes,
)
pyz = PYZ(a.pure)
icon = "assets/icon.icns" if sys.platform == "darwin" else "assets/icon.ico"
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="tokenwatt", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="tokenwatt")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="tokenwatt.app",
        icon=icon,
        bundle_identifier="com.ianamado.tokenwatt",
        info_plist={
            "CFBundleDisplayName": "tokenwatt",
            "CFBundleShortVersionString": "1.1.0",
            "CFBundleVersion": "1.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "MIT license, Ian Amado",
        },
    )
