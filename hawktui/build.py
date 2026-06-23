#!/usr/bin/env python3
"""
HawkTUI self-building installer using PyInstaller.

python3 build.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP_NAME = "hawktui"
MAIN_SCRIPT = "hawktui.py"           
TARGET_BIN_DIR = Path.home() / ".local" / "bin"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"→ {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=cwd)


def main() -> None:
    print("=== HawkTUI PyInstaller Builder ===\n")

    if not Path(MAIN_SCRIPT).exists():
        print(f"ERROR: {MAIN_SCRIPT} not found in current directory.")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        venv_dir = Path(tmp) / "venv"
        print(f"Creating temporary virtual environment...")
        run([sys.executable, "-m", "venv", str(venv_dir)])

        if os.name == "nt":
            pip = venv_dir / "Scripts" / "pip.exe"
            python = venv_dir / "Scripts" / "python.exe"
        else:
            pip = venv_dir / "bin" / "pip"
            python = venv_dir / "bin" / "python"

        print("\nInstalling latest versions of everything (no pinning)...")
        run([str(pip), "install", "--upgrade", "pip", "wheel"])
        run([str(pip), "install", "--upgrade", "pyinstaller", "textual", "pyperclip"])

        print("\nBuilding standalone binary with PyInstaller...")
        pyinstaller_cmd = [
            str(python), "-m", "PyInstaller",
            "--onefile",
            "--name", APP_NAME,
            "--clean",
            "--noconfirm",
            MAIN_SCRIPT,
        ]
        run(pyinstaller_cmd)

    dist_dir = Path("dist")
    binary = dist_dir / (f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME)

    if not binary.exists():
        print(f"\nERROR: Build failed. Binary not found at {binary}")
        sys.exit(1)

    print(f"\n✓ Built: {binary.resolve()}")

    TARGET_BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = TARGET_BIN_DIR / (f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME)

    print(f"→ Installing to {target} ...")
    shutil.copy2(binary, target)

    if os.name != "nt":
        target.chmod(0o755)

    print(f"\nSuccessfully installed `{APP_NAME}` → {target}")
    print(f"\nYou can now run it from anywhere with:\n    {APP_NAME}")

    path_env = os.environ.get("PATH", "")
    if str(TARGET_BIN_DIR) not in path_env:
        print("\n~/.local/bin is not in your PATH yet.")
        print("Add this line to your ~/.bashrc, ~/.zshrc, or ~/.profile:")
        print('    export PATH="$HOME/.local/bin:$PATH"')
        print("Then reload your shell.")


if __name__ == "__main__":
    main()