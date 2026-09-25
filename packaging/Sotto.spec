# PyInstaller spec for a distributable Sotto.app.
#
# The install.sh path builds a venv into Application Support and points a thin
# launcher at it. That cannot be handed to someone else: the venv's python is a
# symlink into Homebrew, so a copied bundle is a dead link on a machine without
# Homebrew Python 3.13. This spec embeds the interpreter and every dependency
# instead, producing a bundle that runs on a stock Mac.
#
# Build:  pyinstaller packaging/Sotto.spec --noconfirm

import os

block_cipher = None

a = Analysis(
    ["../sotto.py"],
    pathex=[],
    binaries=[],
    datas=[("../assets/Sotto.icns", ".")],
    # mlx_whisper and mlx_lm resolve model code lazily, so PyInstaller's static
    # analysis misses these
    hiddenimports=[
        "mlx_whisper",
        "mlx_lm",
        "mlx_lm.models",
        "mlx_lm.tokenizer_utils",
        "transformers",
        "tokenizers",
        "sounddevice",
        "huggingface_hub",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # torch ships CUDA/ROCm kernels and test suites that never run on Apple
    # Silicon; excluding them is most of the size win
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "pytest",
        "IPython",
        "notebook",
        "torch.distributed",
        "torch.testing",
        "torch.utils.tensorboard",
    ],
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
    name="Sotto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,  # signed separately, after the bundle is assembled
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Sotto",
)

app = BUNDLE(
    coll,
    name="Sotto.app",
    icon="../assets/Sotto.icns",
    bundle_identifier="com.utsavanand.sotto",
    info_plist={
        "CFBundleName": "Sotto",
        "CFBundleDisplayName": "Sotto",
        "CFBundleShortVersionString": os.environ.get("SOTTO_VERSION", "1.7.3"),
        "CFBundleVersion": os.environ.get("SOTTO_VERSION", "1.7.3"),
        "LSUIElement": True,  # menu bar only, no Dock icon by default
        "LSMinimumSystemVersion": "14.0",
        "NSMicrophoneUsageDescription":
            "Sotto records while you hold the hotkey and transcribes on-device.",
        "NSHighResolutionCapable": True,
    },
)
