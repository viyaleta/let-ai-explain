# PyInstaller spec for the Let AI Explain desktop app (macOS .app and Windows folder build).
#
#   pip install -r packaging/requirements-build.txt
#   pyinstaller --noconfirm --clean packaging/let_ai_explain.spec
#
# Environment:
#   APP_VERSION   version shown in the app's info (default 0.0.0)
#   TARGET_ARCH   macOS only: universal2, arm64 or x86_64 (default: the Python's own arch)
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICONS = Path(SPECPATH) / "icons"
NAME = "Let AI Explain"
VERSION = os.environ.get("APP_VERSION", "0.0.0")
BUNDLE_ID = "io.github.viyaleta.let-ai-explain"

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "index.html"), "."), (str(ROOT / "manifest.webmanifest"), "."),
           (str(ROOT / "sw.js"), "."), (str(ROOT / "icons"), "icons")],
    hiddenimports=["let_ai_explain", "lae_reader"],
    excludes=["tkinter", "unittest", "pydoc_data"],
    noarchive=False,
)
pyz = PYZ(a.pure)


def windows_version_file():
    """Version details shown in the .exe's Properties (Windows needs four numbers)."""
    import re
    nums = [int(n) for n in re.findall(r"\d+", VERSION)[:4]] + [0] * 4
    v = tuple(nums[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Let AI Explain'),
      StringStruct('FileDescription', '{NAME}'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', '{NAME}'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = Path(workpath) / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # no terminal window
    icon=str(ICONS / ("icon.icns" if sys.platform == "darwin" else "icon.ico")),
    target_arch=os.environ.get("TARGET_ARCH") or None,
    version=windows_version_file() if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(ICONS / "icon.icns"),
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "MIT License",
            # The window loads the app from its own server on 127.0.0.1 over plain http.
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
