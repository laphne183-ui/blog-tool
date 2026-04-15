# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files

# customtkinter, openpyxl 데이터 파일 수집
datas  = collect_data_files("customtkinter")
datas += collect_data_files("openpyxl")
datas += [("app_icon.ico", ".")]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.styles.fills",
        "openpyxl.drawing",
        "openpyxl.drawing.image",
        "openpyxl.drawing.spreadsheet_drawing",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        "customtkinter",
        "playwright",
        "playwright.sync_api",
        "playwright._impl._api_types",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",
    ],
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
    name="블로그순위체커",
    debug=False,
    strip=False,
    upx=True,
    console=False,          # 콘솔 창 없이 실행
    icon="app_icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="blogranker",   # dist\ 하위 폴더명 (ASCII 권장)
)
