# PyInstaller spec for MGU Plus (the GUI over ssh_enable.py).
#   Build:  pyinstaller --noconfirm MGU-Plus.spec   (build_exe.bat does it all)
# Produces a one-folder app in dist/MGU-Plus/. The read-only assets (data/assets)
# and sshd-always-on.sh are NOT embedded - keep them next to MGU-Plus.exe so the
# tool can read them and write per-car keys into data/enrollments/ beside it.
# build_exe.bat copies them for you.
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = []
for pkg in ("cryptography", "cffi"):
    hiddenimports += collect_submodules(pkg)

datas = [("locales", "locales"), ("mgu_plus.ico", "."), ("mgu_plus.png", "."),
         ("mgu_plus_header.png", ".")]
for pkg in ("cryptography",):
    datas += collect_data_files(pkg)

a = Analysis(
    ["gui.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MGU-Plus",
    debug=False,
    strip=False,
    upx=False,
    console=False,   # windowed GUI; logs show inside the window
    version="version_info.txt",  # file version shown in Explorer > Properties
    icon="mgu_plus.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MGU-Plus",
)
