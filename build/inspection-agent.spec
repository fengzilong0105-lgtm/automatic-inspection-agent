# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — desktop app (PySide6), onedir layout.

Run: pyinstaller build/inspection-agent.spec

Output: dist/SteadyOps/SteadyOps.exe + dist/SteadyOps/_internal/
(Use Inno Setup to wrap this folder into SteadyOps-Setup-x.y.z.exe.)
"""

import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

project_root = os.path.abspath(os.path.join(SPECPATH, ".."))
launcher = os.path.join(project_root, "agent", "launcher.py")
assets_dir = os.path.join(project_root, "agent", "desktop", "assets")
icon_path = os.path.join(assets_dir, "icon.ico")

hiddenimports = [
    "agent",
    "agent.desktop.app",
    "agent.desktop.main_window",
    "agent.desktop.setup_wizard",
    "agent.desktop.pages.home_page",
    "agent.desktop.pages.incidents_page",
    "agent.desktop.pages.chat_page",
    "agent.desktop.pages.settings_page",
    "agent.runtime.background",
    "agent.services.agent_service",
    "agent.langchain.chat_graph",
    "agent.langchain.tools",
    "agent.langchain.llm_factory",
    "agent.updater",
    "agent.updater.service",
    "agent.version",
    "asyncssh",
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "shiboken6",
    "langchain_openai",
    "langchain_ollama",
    "langgraph.prebuilt",
    "langgraph.checkpoint.memory",
]

for pkg in (
    "langchain",
    "langchain_core",
    "langchain_community",
    "langgraph",
    "pydantic",
):
    hiddenimports += collect_submodules(pkg)

a = Analysis(
    [launcher],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, "agent", "desktop", "styles.qss"), os.path.join("agent", "desktop")),
        (assets_dir, os.path.join("agent", "desktop", "assets")),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff", "uvicorn", "fastapi", "starlette"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SteadyOps",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if os.path.isfile(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SteadyOps",
)
