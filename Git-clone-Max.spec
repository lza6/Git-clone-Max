# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

- onefile（主产物）：单文件自包含 exe，双击即用。
- onedir（便携版）：exe + 依赖目录（G23-7），启动快、SmartScreen 拦截率低，
  发布时打成 zip 作为第二产物。
- 两个 target 共用 Analysis；onefile 用 EXE(..., a.binaries, a.datas, ...)，
  onedir 用 COLLECT。
- version 文件（G05-5 版本资源）：Windows 资源管理器显示版本号。
"""
from PyInstaller.utils.hooks import collect_all

datas = [('gcm', 'gcm')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('PyQt6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# G05-5 Windows 版本资源（VERSIONINFO）
version_info = None
try:
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
        VarFileInfo, VarStruct,
    )
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=(7, 3, 1, 0),
            prodvers=(7, 3, 1, 0),
            mask=0x3f,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable('040904B0', [
                    StringStruct('CompanyName', 'Git-clone-Max'),
                    StringStruct('FileDescription', 'GitHub repository batch download tool'),
                    StringStruct('FileVersion', '7.3.1'),
                    StringStruct('InternalName', 'Git-clone-Max'),
                    StringStruct('OriginalFilename', 'Git-clone-Max.exe'),
                    StringStruct('ProductName', 'Git-clone-Max'),
                    StringStruct('ProductVersion', '7.3.1'),
                ])
            ]),
            VarFileInfo([VarStruct('Translation', [1033, 1200])]),
        ],
    )
except Exception:
    version_info = None


a = Analysis(
    ['gcm/__main__.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Git-clone-Max',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Git-clone-Max-portable',
)
