#!/usr/bin/env python3
"""
Build a Debian package (.deb) for HawkTUI.
No Python runtime dependency; yt-dlp / ffmpeg are only Recommends.

    python3 packaging/build_deb.py                        
    python3 packaging/build_deb.py --binary dist/hawktui  

Requires dpkg-deb (Debian/Ubuntu). Output lands in dist/hawktui_<version>_<arch>.deb.
"""

from __future__ import annotations

import argparse
import gzip
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = Path(__file__).resolve().parent

APP_NAME = "hawktui"
MAINTAINER = "Christopher Dickinson <theelderemo@theelderemo.dev>"
HOMEPAGE = "https://github.com/theelderemo/hawktui"

MAIN_SCRIPT = REPO_ROOT / "hawktui" / "hawktui.py"
DESKTOP_FILE = PKG_DIR / "hawktui.desktop"
ICON_FILE = REPO_ROOT / "assets" / "hawktui.svg"
LICENSE_FILE = REPO_ROOT / "LICENSE"

CONTROL_TEMPLATE = """\
Package: {name}
Version: {version}
Section: net
Priority: optional
Architecture: {arch}
Maintainer: {maintainer}
Installed-Size: {installed_size}
Recommends: yt-dlp, ffmpeg
Suggests: xclip, xsel, wl-clipboard
Homepage: {homepage}
Description: Clipboard-watching yt-dlp frontend (TUI)
 HawkTUI watches your clipboard and automatically queues copied URLs for
 download with yt-dlp, showing live progress in a terminal UI. It ships as a
 self-contained binary; yt-dlp and ffmpeg are required at runtime, and xclip
 or xsel (or wl-clipboard on Wayland) is needed for clipboard watching on Linux.
"""

POSTINST = """\
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
"""

POSTRM = """\
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
"""

COPYRIGHT_HEADER = (
    "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
    "Upstream-Name: hawktui\n"
    f"Source: {HOMEPAGE}\n\n"
    "Files: *\n"
    "Copyright: Christopher Dickinson\n"
    "License: MIT\n"
)


def read_version() -> str:
    text = MAIN_SCRIPT.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        sys.exit("ERROR: could not read __version__ from hawktui.py")
    return m.group(1)


def deb_arch() -> str:
    try:
        return subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "amd64"


def run(cmd, cwd=None) -> None:
    print("→", " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, cwd=cwd)


def build_binary() -> Path:
    print("dist/hawktui not found — building it with PyInstaller...")
    run([sys.executable, "-m", "PyInstaller",
         "--onefile", "--name", APP_NAME, "--clean", "--noconfirm",
         "--collect-all", "textual", "--collect-all", "pyperclip",
         str(MAIN_SCRIPT)], cwd=REPO_ROOT)
    return REPO_ROOT / "dist" / APP_NAME


def _dir_size_kb(root: Path) -> int:
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    return max(1, total // 1024)


def build_deb(binary: Path, version: str, arch: str, outdir: Path) -> Path:
    for label, path in (("binary", binary), ("icon", ICON_FILE),
                        ("desktop file", DESKTOP_FILE)):
        if not path.exists():
            sys.exit(f"ERROR: {label} not found: {path}")
    if shutil.which("dpkg-deb") is None:
        sys.exit("ERROR: dpkg-deb not found (install dpkg / build on Debian/Ubuntu).")

    stage = outdir / f"{APP_NAME}_{version}_{arch}"
    if stage.exists():
        shutil.rmtree(stage)

    debian = stage / "DEBIAN"
    bindir = stage / "usr" / "bin"
    appsdir = stage / "usr" / "share" / "applications"
    icondir = stage / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    docdir = stage / "usr" / "share" / "doc" / APP_NAME
    for d in (debian, bindir, appsdir, icondir, docdir):
        d.mkdir(parents=True, exist_ok=True)

    shutil.copy2(binary, bindir / APP_NAME)
    (bindir / APP_NAME).chmod(0o755)

    shutil.copy2(DESKTOP_FILE, appsdir / f"{APP_NAME}.desktop")
    (appsdir / f"{APP_NAME}.desktop").chmod(0o644)

    shutil.copy2(ICON_FILE, icondir / f"{APP_NAME}.svg")
    (icondir / f"{APP_NAME}.svg").chmod(0o644)

    copyright_text = COPYRIGHT_HEADER
    if LICENSE_FILE.exists():
        body = LICENSE_FILE.read_text(encoding="utf-8")
        copyright_text += " " + "\n ".join(body.splitlines()) + "\n"
    (docdir / "copyright").write_text(copyright_text, encoding="utf-8")
    (docdir / "copyright").chmod(0o644)

    changelog = (
        f"{APP_NAME} ({version}) unstable; urgency=medium\n\n"
        f"  * Release {version} -- see the GitHub releases page.\n\n"
        f" -- {MAINTAINER}  {format_datetime(datetime.now(timezone.utc))}\n"
    )
    with gzip.GzipFile(str(docdir / "changelog.gz"), "wb", mtime=0) as fh:
        fh.write(changelog.encode("utf-8"))
    (docdir / "changelog.gz").chmod(0o644)

    control = CONTROL_TEMPLATE.format(
        name=APP_NAME, version=version, arch=arch, maintainer=MAINTAINER,
        homepage=HOMEPAGE, installed_size=_dir_size_kb(stage))
    (debian / "control").write_text(control, encoding="utf-8")
    (debian / "control").chmod(0o644)

    for name, content in (("postinst", POSTINST), ("postrm", POSTRM)):
        script = debian / name
        script.write_text(content, encoding="utf-8")
        script.chmod(0o755)

    for d in (stage, *(p for p in stage.rglob("*") if p.is_dir())):
        d.chmod(0o755)

    outdir.mkdir(parents=True, exist_ok=True)
    deb_path = outdir / f"{APP_NAME}_{version}_{arch}.deb"
    run(["dpkg-deb", "--build", "--root-owner-group", str(stage), str(deb_path)])
    shutil.rmtree(stage)
    print(f"\n✓ Built {deb_path}")
    return deb_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a .deb for HawkTUI")
    ap.add_argument("--binary", type=Path, default=None,
                    help="path to an existing standalone binary "
                         "(default: use dist/hawktui, building it if absent)")
    ap.add_argument("--outdir", type=Path, default=REPO_ROOT / "dist",
                    help="where to write the .deb (default: dist/)")
    args = ap.parse_args()

    version = read_version()
    arch = deb_arch()
    print(f"=== HawkTUI .deb builder — v{version} ({arch}) ===\n")

    if args.binary is not None:
        binary = args.binary
    else:
        default = REPO_ROOT / "dist" / APP_NAME
        binary = default if default.exists() else build_binary()

    build_deb(binary, version, arch, args.outdir)


if __name__ == "__main__":
    main()
