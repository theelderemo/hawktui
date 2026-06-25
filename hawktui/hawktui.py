#!/usr/bin/env python3

from __future__ import annotations

__version__ = "1.1.0"

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path

try:
    import tomllib  
except ModuleNotFoundError:  
    tomllib = None

try:
    import pyperclip
except ModuleNotFoundError:  
    pyperclip = None

from textual import events, on, work
from textual.worker import get_current_worker
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hawktui"
CONFIG_PATH = CONFIG_DIR / "config.toml"

PROG = "@@HAWK@@"
PROGRESS_TEMPLATE = (
    f"download:{PROG}\t%(progress._percent_str)s\t%(progress._speed_str)s"
    f"\t%(progress._eta_str)s\t%(info.title)s"
)
# Brand accent pulled from assets/hawktui.svg (the rust/terracotta hawk).
BRAND_COLOR = "#b6623f"

# On-brand labels for the queue's Status column. Internal status strings stay
# canonical everywhere else (counts, clear/retry checks all compare against
# them); only the rendered label changes. "Professional mode" (the `sfw`
# setting) shows the plain internal status instead.
STATUS_VERBS = {
    "queued": "lurking",
    "downloading": "swallowing",
    "done": "swallowed",
    "missing": "limp",
    "cancelled": "spat out",
}


def display_status(status: str, sfw: bool) -> str:
    """Map an internal status to its on-brand label, unless SFW mode is on."""
    if sfw:
        return status
    if status.startswith("error"):
        # Preserve any "(code)" suffix, e.g. error(1) -> floppy(1).
        return "floppy" + status[len("error"):]
    return STATUS_VERBS.get(status, status)


URL_RE = re.compile(r"https?://[^\s<>\"']+")
# Punctuation that commonly trails a URL in prose/markdown but isn't part of it
# (e.g. a Markdown link "(https://youtu.be/abc)" or a sentence "...see abc.").
_URL_TRAILING = ").,;!?'\""


def extract_urls(text: str) -> list[str]:
    """Find URLs in text, stripping punctuation that trails them in prose.

    URL_RE's character class only stops at whitespace/<>"', so copying a URL
    inside parentheses or with trailing punctuation captures that punctuation
    (e.g. "https://youtu.be/abc)" or "...abc."), which then gets passed
    verbatim to yt-dlp and fails. Trim the common trailing characters here so
    every call site benefits.
    """
    urls = []
    for match in URL_RE.findall(text):
        url = match.rstrip(_URL_TRAILING)
        if url:
            urls.append(url)
    return urls


def split_patterns(text: str) -> list[str]:
    """Split an allow/deny setting into individual patterns.

    Patterns are separated by commas or newlines; blanks are dropped, so an
    empty/whitespace setting yields no patterns (i.e. the filter is off).
    """
    return [p.strip() for p in re.split(r"[,\n]", text or "") if p.strip()]


def url_matches_any(url: str, patterns: list[str]) -> bool:
    """True if `url` matches any pattern, regex first then substring fallback.

    Each pattern is tried as a regex (so users can write per-site rules like
    `youtube\\.com|youtu\\.be`); if it isn't valid regex it falls back to a
    plain case-sensitive substring test so a literal like `?list=` still works.
    """
    for pat in patterns:
        try:
            if re.search(pat, url):
                return True
        except re.error:
            if pat in url:
                return True
    return False

# yt-dlp announces the output path in a few shapes depending on whether a merge
# or post-processing step ran. Capture whichever we see so the per-row "open"
# action can point at the real file instead of just the download folder.
_DEST_RE = re.compile(r"\bDestination:\s*(.+?)\s*$")
_MERGE_RE = re.compile(r'Merging formats into "(.+?)"')
_ALREADY_RE = re.compile(r"^\[download\]\s+(.+?)\s+has already been downloaded")


def human_size(n: int) -> str:
    """Bytes as a compact human string (e.g. 45.2 MiB)."""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def human_time(seconds: float) -> str:
    """Seconds as a compact human string (e.g. 9s, 1m03s, 1h02m)."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"

PREFERRED_THEMES = [
    # Gruvbox first: its warm earthy palette matches the rust hawk logo, and it
    # backs the L3 `themes[0]` fallback when a saved theme is unavailable.
    "gruvbox", "nord", "tokyo-night", "catppuccin-mocha", "dracula",
    "monokai", "flexoki", "textual-dark", "textual-light",
    "catppuccin-latte", "solarized-light",
]

CONTAINER_FMTS = [
    ("— off —", ""), ("mp4", "mp4"), ("mkv", "mkv"), ("webm", "webm"),
    ("mov", "mov"), ("avi", "avi"), ("flv", "flv"), ("mp3", "mp3"),
    ("m4a", "m4a"), ("opus", "opus"), ("flac", "flac"), ("wav", "wav"),
    ("aac", "aac"), ("ogg", "ogg"),
]
BROWSERS = [
    ("— none —", ""), ("firefox", "firefox"), ("chrome", "chrome"),
    ("chromium", "chromium"), ("brave", "brave"), ("edge", "edge"),
    ("opera", "opera"), ("vivaldi", "vivaldi"), ("safari", "safari"),
    ("whale", "whale"),
]
AUDIO_FMTS = [
    ("best", "best"), ("mp3", "mp3"), ("m4a", "m4a"), ("opus", "opus"),
    ("flac", "flac"), ("wav", "wav"), ("aac", "aac"), ("vorbis", "vorbis"),
]
SPONSORBLOCK = [
    ("— off —", ""), ("all", "all"), ("sponsor", "sponsor"), ("default", "default"),
]
PARALLEL = [("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("6", "6"), ("8", "8")]

DEFAULTS: dict = {
    "download_dir": str(Path.home() / "Downloads"),
    "output_template": "",
    "format": "",
    "max_parallel": "2",
    "watch_on_start": True,
    "url_filter": "",
    # Allow/deny lists, off by default (empty = no effect). Each is a
    # comma/newline-separated list of patterns, tried as regex and falling
    # back to a plain substring match. A URL is rejected if it matches any
    # deny pattern, or — when the allow list is non-empty — matches none of it.
    "url_allow": "",
    "url_deny": "",
    # Append a record of every finished download to a history log in the
    # download directory (see _write_history).
    "download_history": True,
    "theme": "gruvbox",
    "sfw": False,
    "notify_toast": True,
    "notify_desktop": False,
    "no_playlist": False,
    "lazy_playlist": False,
    "max_downloads": "",
    "concurrent_fragments": "1",
    "no_abort_on_error": True,
    "no_overwrites": False,
    "limit_rate": "",
    "retries": "",
    "download_archive": False,
    "restrict_filenames": False,
    "write_subs": False,
    "write_auto_subs": False,
    "embed_subs": False,
    "sub_langs": "en",
    "remux_video": "",
    "recode_video": "",
    "extract_audio": False,
    "audio_format": "best",
    "embed_thumbnail": False,
    "write_thumbnail": False,
    "embed_metadata": False,
    "embed_chapters": False,
    "sponsorblock_remove": "",
    "cookies_from_browser": "",
}

BOOL_KEYS = {k for k, v in DEFAULTS.items() if isinstance(v, bool)}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists() and tomllib is not None:
        try:
            with CONFIG_PATH.open("rb") as fh:
                data = tomllib.load(fh)
            for k in DEFAULTS:
                if k in data:
                    cfg[k] = data[k]
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# hawktui config — edited live by the TUI\n"]
    for k in DEFAULTS:
        v = cfg.get(k, DEFAULTS[k])
        if isinstance(v, bool):
            lines.append(f"{k} = {str(v).lower()}")
        elif isinstance(v, int):
            lines.append(f"{k} = {v}")
        else:
            s = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{s}"')
    CONFIG_PATH.write_text("\n".join(lines) + "\n")


def build_command(cfg: dict, url: str) -> list[str]:
    cmd = ["yt-dlp", "--color", "never", "--newline",
           "--progress-template", PROGRESS_TEMPLATE]

    ddir = os.path.expanduser(cfg["download_dir"]).strip()
    if ddir:
        cmd += ["-P", ddir]
    if cfg["output_template"].strip():
        cmd += ["-o", cfg["output_template"].strip()]
    if cfg["format"].strip():
        cmd += ["-f", cfg["format"].strip()]

    if cfg["no_playlist"]:
        cmd.append("--no-playlist")
    if cfg["lazy_playlist"]:
        cmd.append("--lazy-playlist")
    if str(cfg["max_downloads"]).strip():
        cmd += ["--max-downloads", str(cfg["max_downloads"]).strip()]

    cf = str(cfg["concurrent_fragments"]).strip()
    if cf and cf != "1":
        cmd += ["-N", cf]
    if cfg["no_abort_on_error"]:
        cmd.append("--no-abort-on-error")
    if cfg["no_overwrites"]:
        cmd.append("--no-overwrites")
    if str(cfg["limit_rate"]).strip():
        cmd += ["--limit-rate", str(cfg["limit_rate"]).strip()]
    if str(cfg["retries"]).strip():
        cmd += ["--retries", str(cfg["retries"]).strip()]
    if cfg["restrict_filenames"]:
        cmd.append("--restrict-filenames")
    if cfg["download_archive"] and ddir:
        cmd += ["--download-archive", os.path.join(ddir, ".hawktui-archive.txt")]

    if cfg["write_subs"]:
        cmd.append("--write-subs")
    if cfg["write_auto_subs"]:
        cmd.append("--write-auto-subs")
    if cfg["embed_subs"]:
        cmd.append("--embed-subs")
    if (cfg["write_subs"] or cfg["write_auto_subs"] or cfg["embed_subs"]) \
            and cfg["sub_langs"].strip():
        cmd += ["--sub-langs", cfg["sub_langs"].strip()]

    if cfg["remux_video"]:
        cmd += ["--remux-video", cfg["remux_video"]]
    if cfg["recode_video"]:
        cmd += ["--recode-video", cfg["recode_video"]]
    if cfg["extract_audio"]:
        cmd.append("-x")
        if cfg["audio_format"] and cfg["audio_format"] != "best":
            cmd += ["--audio-format", cfg["audio_format"]]
    if cfg["embed_thumbnail"]:
        cmd.append("--embed-thumbnail")
    if cfg["write_thumbnail"]:
        cmd.append("--write-thumbnail")
    if cfg["embed_metadata"]:
        cmd.append("--embed-metadata")
    if cfg["embed_chapters"]:
        cmd.append("--embed-chapters")
    if cfg["sponsorblock_remove"]:
        cmd += ["--sponsorblock-remove", cfg["sponsorblock_remove"]]

    if cfg["cookies_from_browser"]:
        cmd += ["--cookies-from-browser", cfg["cookies_from_browser"]]

    cmd.append(url)
    return cmd


class Gate:
    def __init__(self, limit: int):
        self.limit = max(1, limit)
        self.active = 0
        self.cv = threading.Condition()

    def acquire(self, timeout: float | None = None) -> bool:
        with self.cv:
            if timeout is None:
                while self.active >= self.limit:
                    self.cv.wait()
            else:
                deadline = time.monotonic() + timeout
                while self.active >= self.limit:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self.cv.wait(remaining)
            self.active += 1
            return True

    def release(self):
        with self.cv:
            self.active = max(0, self.active - 1)
            self.cv.notify()

    def set_limit(self, n: int):
        with self.cv:
            self.limit = max(1, n)
            self.cv.notify_all()

@dataclass
class Download:
    id: str
    url: str
    title: str = ""
    status: str = "queued"
    percent: str = "—"
    speed: str = "—"
    eta: str = "—"
    returncode: int | None = None
    # Final output path, captured from yt-dlp's "Destination:" / "Merging
    # formats into" lines when available. Lets per-row "open" target the actual
    # file; falls back to the download directory when unknown.
    filepath: str = ""
    # Wall-clock start (monotonic) and, once finished, elapsed seconds + the
    # final file size in bytes — surfaced in the completion log line.
    started_at: float = 0.0
    elapsed: float = 0.0
    filesize: int = 0

    def display_title(self) -> str:
        return self.title or self.url

class hawktui(App):
    TITLE = "HawkTUI"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    Screen { layout: vertical; }

    #statusbar {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
        content-align: left middle;
    }
    #statusbar.on  { background: $success 20%; }
    #statusbar.off { background: $warning 20%; }

    /* Custom branding line in the brand rust (#b6623f from hawktui.svg).
       Left in normal flow so the 1fr TabbedContent pushes it to sit directly
       above the docked Footer. */
    #brandbar {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: #b6623f;
        text-style: bold;
        content-align: center middle;
    }
    Footer { background: $panel; }

    TabbedContent { height: 1fr; }

    #queue-table { height: 1fr; border: round $primary; }
    #addbar { height: 3; padding: 0 1; }
    #addbar Input { width: 1fr; }
    #addbar Button { width: auto; margin-left: 1; }

    Log { height: 1fr; border: round $primary; background: $surface; }

    .settings-scroll { height: 1fr; padding: 0 1; }

    .row { layout: horizontal; height: auto; padding: 1 0 0 0; }
    .row > Label { width: 34; padding: 1 1 0 0; color: $text-muted; }
    .row > Input { width: 1fr; }
    .row > Select { width: 1fr; }
    .row > Switch { height: auto; }

    Collapsible { border: round $primary 30%; margin: 1 0; }
    """

    BINDINGS = [
        Binding("w", "toggle_watch", "Watch on/off"),
        Binding("a", "focus_add", "Add URL"),
        Binding("f", "list_formats", "List formats (-F)"),
        Binding("c", "clear_done", "Clear finished"),
        Binding("r", "retry_failed", "Retry failed"),
        Binding("p", "toggle_pause", "Pause/resume queue"),
        Binding("x", "cancel_selected", "Cancel row"),
        Binding("d", "remove_selected", "Remove row"),
        Binding("y", "copy_url", "Copy URL"),
        Binding("b", "open_browser", "Open in browser"),
        Binding("o", "open_downloads", "Open folder"),
        Binding("ctrl+p", "command_palette", "Palette"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.watching: bool = bool(self.cfg["watch_on_start"])
        self.last_clip: str = ""
        self.seen: set[str] = set()
        self.downloads: dict[str, Download] = {}
        self.procs: dict[str, subprocess.Popen] = {}
        # IDs the user explicitly cancelled, so the worker reports a clean
        # "cancelled" status instead of the SIGTERM error code.
        self._cancelled: set[str] = set()
        self._counter = count(1)
        try:
            self.gate = Gate(int(self.cfg["max_parallel"]))
        except (TypeError, ValueError):
            self.gate = Gate(2)
        self.col_keys: list = []
        self._save_timer = None
        # Pending downloads wait here as lightweight objects; a single
        # dispatcher thread (started in on_mount) pulls from this queue and
        # only spawns a worker once a concurrency slot is free. This keeps the
        # number of live OS threads bounded by max_parallel no matter how many
        # URLs are pasted at once, instead of one parked thread per URL.
        self.pending: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        # When True the dispatcher stops pulling new work off the queue; any
        # in-flight downloads keep running (see action_toggle_pause).
        self._queue_paused = False
        # Brief "saved" flash: monotonic deadline until which the status bar
        # shows the indicator (see _mark_saved / update_status).
        self._saved_until = 0.0
        # Re-armed on each enqueue; guards the one-shot "all done" notification.
        self._announced_alldone = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="statusbar")
        with TabbedContent(initial="tab-queue"):
            with TabPane("Queue", id="tab-queue"):
                with Horizontal(id="addbar"):
                    yield Input(placeholder="Paste a URL and press Enter…", id="add-url")
                    yield Button("Add", id="btn-add", variant="primary")
                    yield Button("-F", id="btn-formats")
                yield DataTable(id="queue-table", zebra_stripes=True, cursor_type="row")
            with TabPane("Settings", id="tab-settings"):
                yield from self._compose_settings()
            with TabPane("Log", id="tab-log"):
                yield Log(id="log", highlight=True, auto_scroll=True)
        yield Static(self._tagline(), id="brandbar")
        yield Footer()

    def _row(self, label: str, control):
        return Horizontal(Label(label), control, classes="row")

    def _sw(self, key: str) -> Switch:
        return Switch(value=bool(self.cfg[key]), id=f"set-{key}")

    def _in(self, key: str, placeholder: str = "") -> Input:
        return Input(value=str(self.cfg[key]), placeholder=placeholder, id=f"set-{key}")

    def _sel(self, key: str, options) -> Select:
        return Select(options, value=self.cfg[key], allow_blank=False, id=f"set-{key}")

    def _compose_settings(self) -> ComposeResult:
        with VerticalScroll(classes="settings-scroll"):
            themes = [(t, t) for t in PREFERRED_THEMES if t in self.available_themes]
            cur_theme = self.cfg["theme"] if self.cfg["theme"] in self.available_themes else themes[0][1]
            with Collapsible(title="General", collapsed=False):
                yield self._row("Theme", Select(themes, value=cur_theme, allow_blank=False, id="set-theme"))
                yield self._row("Download directory", self._in("download_dir", "~/Downloads"))
                yield self._row("Output template (-o)", self._in("output_template", "%(title)s.%(ext)s"))
                yield self._row("Format selector (-f)", self._in("format", "bv*+ba/b"))
                yield self._row("Parallel downloads", self._sel("max_parallel", PARALLEL))
                yield self._row("Watch on start", self._sw("watch_on_start"))
                yield self._row("Professional mode (SFW)", self._sw("sfw"))
                yield self._row("URL filter (substring)", self._in("url_filter", "leave empty for any http(s)"))
                yield self._row("URL allow list", self._in("url_allow", "regex/substring, comma-sep (off if empty)"))
                yield self._row("URL deny list", self._in("url_deny", "regex/substring, comma-sep (off if empty)"))
                yield self._row("Save download history", self._sw("download_history"))
            with Collapsible(title="Notifications"):
                yield self._row("In-app toasts", self._sw("notify_toast"))
                yield self._row("Desktop notifications", self._sw("notify_desktop"))
            with Collapsible(title="Playlist"):
                yield self._row("--no-playlist", self._sw("no_playlist"))
                yield self._row("--lazy-playlist", self._sw("lazy_playlist"))
                yield self._row("--max-downloads", self._in("max_downloads", "number"))
            with Collapsible(title="Network & behaviour"):
                yield self._row("-N concurrent-fragments", self._in("concurrent_fragments", "1"))
                yield self._row("--no-abort-on-error", self._sw("no_abort_on_error"))
                yield self._row("-w --no-overwrites", self._sw("no_overwrites"))
                yield self._row("--limit-rate", self._in("limit_rate", "e.g. 2M"))
                yield self._row("--retries", self._in("retries", "e.g. 10 / infinite"))
                yield self._row("--download-archive", self._sw("download_archive"))
                yield self._row("--restrict-filenames", self._sw("restrict_filenames"))
            with Collapsible(title="Subtitles"):
                yield self._row("--write-subs", self._sw("write_subs"))
                yield self._row("--write-auto-subs", self._sw("write_auto_subs"))
                yield self._row("--embed-subs", self._sw("embed_subs"))
                yield self._row("--sub-langs", self._in("sub_langs", "en,en-US"))
            with Collapsible(title="Post-processing"):
                yield self._row("--remux-video", self._sel("remux_video", CONTAINER_FMTS))
                yield self._row("--recode-video", self._sel("recode_video", CONTAINER_FMTS))
                yield self._row("-x --extract-audio", self._sw("extract_audio"))
                yield self._row("--audio-format", self._sel("audio_format", AUDIO_FMTS))
                yield self._row("--embed-thumbnail", self._sw("embed_thumbnail"))
                yield self._row("--write-thumbnail", self._sw("write_thumbnail"))
                yield self._row("--embed-metadata", self._sw("embed_metadata"))
                yield self._row("--embed-chapters", self._sw("embed_chapters"))
                yield self._row("--sponsorblock-remove", self._sel("sponsorblock_remove", SPONSORBLOCK))
            with Collapsible(title="Authentication"):
                yield self._row("--cookies-from-browser", self._sel("cookies_from_browser", BROWSERS))

    def on_mount(self) -> None:
        if self.cfg["theme"] in self.available_themes:
            self.theme = self.cfg["theme"]
        table = self.query_one("#queue-table", DataTable)
        self.col_keys = table.add_columns("#", "Title", "Status", "%", "Speed", "ETA")
        self._check_environment()
        try:
            self.last_clip = (pyperclip.paste() or "").strip() if pyperclip else ""
        except Exception:
            self.last_clip = ""
        self.set_interval(1.0, self.poll_clipboard)
        self.set_interval(0.5, self.update_status)
        self.update_status()
        self._dispatcher()

    def _check_environment(self) -> None:
        if shutil.which("yt-dlp") is None:
            self._write_log("WARNING: yt-dlp not found on PATH (pip install yt-dlp).")
        if pyperclip is None:
            self._write_log("WARNING: pyperclip not installed; clipboard watch disabled.")
        elif sys.platform.startswith("linux") \
                and shutil.which("xclip") is None and shutil.which("xsel") is None:
            # Only Linux relies on xclip/xsel; macOS (pbcopy/pbpaste) and
            # Windows (native clipboard) work without them, so don't nag there.
            self._write_log("WARNING: install xclip or xsel for X11 clipboard access.")
        self._write_log(self._voice(
            "locked and loaded — 'w' to start watching, 'a' to add a URL.",
            "ready. press 'w' to toggle watching, 'a' to add a URL.",
        ))

    def update_status(self) -> None:
        ds = self.downloads.values()
        active = sum(1 for d in ds if d.status == "downloading")
        queued = sum(1 for d in ds if d.status == "queued")
        done = sum(1 for d in ds if d.status == "done")
        failed = sum(1 for d in ds if d.status.startswith("error") or d.status == "missing")
        state = "● WATCHING" if self.watching else "○ PAUSED"
        bar = self.query_one("#statusbar", Static)
        bar.set_class(self.watching, "on")
        bar.set_class(not self.watching, "off")
        qstate = "   ⏸ QUEUE PAUSED" if self._queue_paused else ""
        saved = "   ✓ saved" if time.monotonic() < self._saved_until else ""
        bar.update(
            f"{state}{qstate}   active {active}  queued {queued}  done {done}  failed {failed}"
            f"   │ dir: {self.cfg['download_dir']}   │ parallel: {self.gate.limit}{saved}"
        )
        # Mirror the watch state into the Header sub-title (title-bar indicator).
        watch = "● watching" if self.watching else "○ paused"
        self.sub_title = f"v{__version__} · {watch}"

    def poll_clipboard(self) -> None:
        if not self.watching or pyperclip is None:
            return
        try:
            text = (pyperclip.paste() or "").strip()
        except Exception:
            return
        if not text or text == self.last_clip:
            return
        self.last_clip = text
        for url in extract_urls(text):
            self.enqueue(url)

    def enqueue(self, url: str) -> None:
        url = url.strip()
        if not url:
            return
        if url in self.seen:
            self._write_log(f"skipped (already in list): {url}")
            return
        flt = self.cfg["url_filter"].strip()
        if flt and flt not in url:
            return
        deny = split_patterns(self.cfg.get("url_deny", ""))
        if deny and url_matches_any(url, deny):
            self._write_log(f"skipped (deny list): {url}")
            return
        allow = split_patterns(self.cfg.get("url_allow", ""))
        if allow and not url_matches_any(url, allow):
            self._write_log(f"skipped (not in allow list): {url}")
            return
        self.seen.add(url)
        dl = Download(id=f"d{next(self._counter)}", url=url)
        self.downloads[dl.id] = dl
        table = self.query_one("#queue-table", DataTable)
        table.add_row(dl.id[1:], dl.display_title(),
                      display_status(dl.status, bool(self.cfg["sfw"])),
                      dl.percent, dl.speed, dl.eta, key=dl.id)
        self._write_log(f"queued: {url}")
        # New work arrived — re-arm the one-shot "all downloads complete" toast.
        self._announced_alldone = False
        self.pending.put(dl)

    @work(thread=True, group="dispatcher")
    def _dispatcher(self) -> None:
        """Pull queued downloads and start them as slots free up.

        Runs as a single long-lived thread. It blocks on the pending queue,
        then blocks on a free concurrency slot, and only then spawns the actual
        download worker — so waiting URLs sit in the queue as data, not as
        parked threads. Short timeouts keep it responsive to shutdown.
        """
        worker = get_current_worker()

        def stopping() -> bool:
            # Stop on explicit quit OR when Textual cancels this worker during
            # app shutdown, so the thread never outlives the app and blocks the
            # executor join on teardown.
            return self._stopping.is_set() or worker.is_cancelled

        while not stopping():
            if self._queue_paused:
                time.sleep(0.15)
                continue
            try:
                dl = self.pending.get(timeout=0.25)
            except queue.Empty:
                continue
            while not self.gate.acquire(timeout=0.25):
                if stopping():
                    return
            if stopping():
                self.gate.release()
                return
            if self._queue_paused:
                # Paused while we were waiting for a slot — hand the work back
                # so it isn't started until the user resumes.
                self.gate.release()
                self.pending.put(dl)
                continue
            self.download_worker(dl)

    @work(thread=True, group="downloads")
    def download_worker(self, dl: Download) -> None:
        # The dispatcher has already reserved a concurrency slot via
        # gate.acquire(); the matching release() happens in the finally below.
        try:
            dl.status = "downloading"
            dl.started_at = time.monotonic()
            self.call_from_thread(self._update_row, dl)
            cmd = build_command(self.cfg, dl.url)
            self.call_from_thread(self._write_log, "$ " + " ".join(cmd))
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
            except FileNotFoundError:
                dl.status = "missing"
                self.call_from_thread(self._write_log, "ERROR: yt-dlp not found.")
                return
            self.procs[dl.id] = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.startswith(PROG):
                    p = line.split("\t")
                    if len(p) >= 4:
                        dl.percent = p[1].strip() or dl.percent
                        dl.speed = p[2].strip() or dl.speed
                        dl.eta = p[3].strip() or dl.eta
                        if len(p) >= 5 and p[4].strip() and not dl.title:
                            dl.title = p[4].strip()
                    self.call_from_thread(self._update_row, dl)
                elif line.strip():
                    self._capture_filepath(dl, line)
                    self.call_from_thread(self._write_log, line)
            proc.wait()
            dl.returncode = proc.returncode
            if dl.id in self._cancelled:
                self._cancelled.discard(dl.id)
                dl.status = "cancelled"
            elif proc.returncode == 0:
                dl.status, dl.percent = "done", "100%"
            elif proc.returncode == 101:
                dl.status = "done"
            else:
                dl.status = f"error({proc.returncode})"
        except Exception as exc:  
            dl.status = "error"
            self.call_from_thread(self._write_log, f"ERROR: {exc}")
        finally:
            self.procs.pop(dl.id, None)
            self.gate.release()
            self.call_from_thread(self._update_row, dl)
            self.call_from_thread(self._on_download_finished, dl)

    def _update_row(self, dl: Download) -> None:
        table = self.query_one("#queue-table", DataTable)
        try:
            table.update_cell(dl.id, self.col_keys[1], dl.display_title())
            table.update_cell(dl.id, self.col_keys[2],
                              display_status(dl.status, bool(self.cfg["sfw"])))
            table.update_cell(dl.id, self.col_keys[3], dl.percent)
            table.update_cell(dl.id, self.col_keys[4], dl.speed)
            table.update_cell(dl.id, self.col_keys[5], dl.eta)
        except Exception:
            pass

    def _write_log(self, msg: str) -> None:
        try:
            self.query_one("#log", Log).write_line(msg)
        except Exception:
            pass

    def _capture_filepath(self, dl: Download, line: str) -> None:
        """Record the output path from yt-dlp's chatter, if this line has one.

        Runs in the download worker thread and only sets a plain attribute, so
        it's safe without call_from_thread. Later matches (e.g. a merge target
        or a post-processing Destination) overwrite earlier ones, which is
        normally the final file the user cares about.
        """
        for rx in (_MERGE_RE, _DEST_RE, _ALREADY_RE):
            m = rx.search(line)
            if m:
                path = m.group(1).strip().strip('"')
                if path:
                    dl.filepath = path
                return

    def _toast(self, msg: str, severity: str = "information") -> None:
        """In-app toast, gated by the notify_toast setting."""
        if not self.cfg.get("notify_toast"):
            return
        try:
            self.notify(msg, severity=severity)
        except Exception:
            pass

    def _desktop_notify(self, title: str, msg: str) -> None:
        """OS-level notification, gated by the notify_desktop setting.

        Best-effort and non-blocking: notify-send on Linux, osascript on
        macOS. Silently does nothing elsewhere or if the tool is missing.
        """
        if not self.cfg.get("notify_desktop"):
            return
        try:
            if sys.platform.startswith("darwin"):
                script = f"display notification {json.dumps(msg)} with title {json.dumps(title)}"
                subprocess.Popen(["osascript", "-e", script])
            elif sys.platform.startswith("linux") and shutil.which("notify-send"):
                subprocess.Popen(["notify-send", title, msg])
        except Exception:
            pass

    def _write_history(self, dl: Download) -> None:
        """Append a record of a finished download to a history log.

        The log lives in the download directory as `hawktui-history.log`, one
        tab-separated line per download: timestamp, outcome, title, URL, output
        path, final size, and elapsed time. Best-effort and gated by the
        download_history setting; failures (read-only dir, etc.) are ignored so
        they never interfere with the download itself.
        """
        if not self.cfg.get("download_history"):
            return
        ddir = os.path.expanduser(self._downloads_dir())
        try:
            os.makedirs(ddir, exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            size = human_size(dl.filesize) if dl.filesize else "-"
            took = human_time(dl.elapsed) if dl.elapsed else "-"
            title = dl.display_title().replace("\t", " ").replace("\n", " ")
            line = (f"[{ts}]\t{dl.status}\t{title}\t{dl.url}\t"
                    f"{dl.filepath or '-'}\t{size}\t{took}\n")
            with open(os.path.join(ddir, "hawktui-history.log"),
                      "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    def _on_download_finished(self, dl: Download) -> None:
        """Runs on the main thread once a worker exits (any terminal status).

        Computes elapsed time + final size, logs a summary, fires
        notifications, and checks whether the whole queue just drained.
        """
        if dl.started_at:
            dl.elapsed = time.monotonic() - dl.started_at
        if dl.filepath:
            p = os.path.expanduser(dl.filepath)
            try:
                if os.path.exists(p):
                    dl.filesize = os.path.getsize(p)
            except OSError:
                pass

        self._write_history(dl)

        title = dl.display_title()
        if dl.status == "done":
            size = human_size(dl.filesize) if dl.filesize else "?"
            took = human_time(dl.elapsed)
            self._write_log(self._voice(
                f"swallowed: {title} — {size} in {took}",
                f"done: {title} — {size} in {took}",
            ))
            self._toast(self._voice(f"swallowed: {title}", f"done: {title}"))
            self._desktop_notify("HawkTUI", f"Finished: {title}")
        elif dl.status.startswith("error") or dl.status == "missing":
            self._toast(self._voice(f"floppy: {title}", f"failed: {title}"),
                        severity="error")
            self._desktop_notify("HawkTUI", f"Failed: {title}")
        # Cancelled downloads are user-initiated; no notification.
        self._maybe_all_done()

    def _maybe_all_done(self) -> None:
        """Fire a one-shot 'all downloads complete' notification when idle."""
        if self._stopping.is_set() or self._announced_alldone:
            return
        ds = list(self.downloads.values())
        if any(d.status in ("queued", "downloading") for d in ds):
            return
        if not self.pending.empty():
            return
        if not any(d.status == "done" for d in ds):
            return
        self._announced_alldone = True
        self._toast(self._voice("all swallowed 🦅", "all downloads complete"))
        self._desktop_notify("HawkTUI", "All downloads complete")

    def _mark_saved(self) -> None:
        """Flash a brief 'saved' indicator in the status bar."""
        self._saved_until = time.monotonic() + 1.5
        try:
            self.update_status()
        except Exception:
            pass

    def _voice(self, spicy: str, plain: str) -> str:
        """Pick on-brand or neutral copy depending on Professional mode."""
        return plain if self.cfg.get("sfw") else spicy

    def _tagline(self) -> str:
        return self._voice(
            "🦅 HawkTUI — copy a link and it's already swallowed",
            "HawkTUI — clipboard-watching yt-dlp frontend",
        )

    def _apply_voice(self) -> None:
        """Re-render brand copy + status labels after the SFW toggle flips."""
        try:
            self.query_one("#brandbar", Static).update(self._tagline())
        except Exception:
            pass
        for dl in self.downloads.values():
            self._update_row(dl)

    @work(thread=True, group="formats")
    def list_formats_worker(self, url: str) -> None:
        cmd = ["yt-dlp", "--color", "never", "-F"]
        if self.cfg["cookies_from_browser"]:
            cmd += ["--cookies-from-browser", self.cfg["cookies_from_browser"]]
        cmd.append(url)
        self.call_from_thread(self._write_log, "$ " + " ".join(cmd))
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            self.call_from_thread(self._write_log, "ERROR: yt-dlp not found.")
            return
        for line in (res.stdout + res.stderr).splitlines():
            self.call_from_thread(self._write_log, line)

    def _persist(self, key: str, value) -> None:
        """Update in-memory config and write it to disk immediately.

        Used for discrete, low-frequency controls (switches, selects).
        """
        self.cfg[key] = value
        save_config(self.cfg)
        self._mark_saved()

    def _persist_debounced(self, key: str, value) -> None:
        """Update in-memory config now, but defer the disk write.

        Input.Changed fires on every keystroke (and once per field for the
        initial programmatic values during compose), so writing the config on
        each event hammers the disk and can persist a half-typed value. The
        in-memory cfg is updated immediately — downloads always use the current
        value — while rapid edits are coalesced into a single delayed write.
        A normal quit (action_quit) calls save_config, flushing anything left.
        """
        self.cfg[key] = value
        if self._save_timer is not None:
            self._save_timer.stop()
        self._save_timer = self.set_timer(0.75, self._flush_save)

    def _flush_save(self) -> None:
        """Write a pending debounced config change to disk, if any."""
        if self._save_timer is None:
            return
        self._save_timer.stop()
        self._save_timer = None
        save_config(self.cfg)
        self._mark_saved()

    @on(Switch.Changed)
    def _on_switch(self, event: Switch.Changed) -> None:
        if event.switch.id and event.switch.id.startswith("set-"):
            key = event.switch.id[4:]
            self._persist(key, bool(event.value))
            if key == "sfw":
                # Re-render the tagline and status labels in the new voice.
                self._apply_voice()

    @on(Select.Changed)
    def _on_select(self, event: Select.Changed) -> None:
        sid = event.select.id or ""
        if not sid.startswith("set-"):
            return
        key = sid[4:]
        value = "" if event.value is Select.BLANK else event.value
        self._persist(key, value)
        if key == "theme" and value in self.available_themes:
            self.theme = value
        elif key == "max_parallel":
            try:
                self.gate.set_limit(int(value))
            except (TypeError, ValueError):
                pass

    @on(Input.Changed)
    def _on_input(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("set-"):
            self._persist_debounced(event.input.id[4:], event.value)

    @on(Input.Submitted)
    def _on_setting_submit(self, event: Input.Submitted) -> None:
        # Pressing Enter in a settings field flushes the pending write now.
        if event.input.id and event.input.id.startswith("set-"):
            self._flush_save()

    @on(events.DescendantBlur)
    def _on_descendant_blur(self) -> None:
        # Moving focus off a settings field flushes the pending write now.
        self._flush_save()

    @on(Input.Submitted, "#add-url")
    def _on_add_submit(self, event: Input.Submitted) -> None:
        self._add_from_input()

    @on(Button.Pressed, "#btn-add")
    def _on_add_btn(self) -> None:
        self._add_from_input()

    @on(Button.Pressed, "#btn-formats")
    def _on_fmt_btn(self) -> None:
        self.action_list_formats()

    def _add_from_input(self) -> None:
        box = self.query_one("#add-url", Input)
        text = box.value.strip()
        if not text:
            return
        for url in extract_urls(text) or [text]:
            self.enqueue(url)
        box.value = ""

    def action_toggle_watch(self) -> None:
        self.watching = not self.watching
        if self.watching and pyperclip is not None:
            try:
                self.last_clip = (pyperclip.paste() or "").strip()
            except Exception:
                pass
        if self.watching:
            self._write_log(self._voice("watching — copy a link and it's mine", "watching ON"))
        else:
            self._write_log(self._voice("watching paused — your links are safe", "watching PAUSED"))
        self.update_status()

    def action_toggle_pause(self) -> None:
        self._queue_paused = not self._queue_paused
        if self._queue_paused:
            self._write_log(self._voice(
                "queue held — in-flight downloads finish, nothing new starts.",
                "queue paused — running downloads continue; new ones wait.",
            ))
        else:
            self._write_log(self._voice(
                "queue loosed — back to swallowing.",
                "queue resumed.",
            ))
        self.update_status()

    def action_focus_add(self) -> None:
        self.query_one(TabbedContent).active = "tab-queue"
        self.query_one("#add-url", Input).focus()

    def action_list_formats(self) -> None:
        url = self.query_one("#add-url", Input).value.strip()
        if not url:
            self._write_log("enter a URL in the Add box first, then press 'f'.")
            return
        m = extract_urls(url)
        target = m[0] if m else url
        self.query_one(TabbedContent).active = "tab-log"
        self.list_formats_worker(target)

    def action_clear_done(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        for dl in list(self.downloads.values()):
            if dl.status == "done" or dl.status.startswith("error") or dl.status == "missing":
                try:
                    table.remove_row(dl.id)
                except Exception:
                    pass
                self.downloads.pop(dl.id, None)
                # Forget the URL too, so re-copying it later re-downloads
                # instead of being silently swallowed by the seen-set.
                self.seen.discard(dl.url)
        self.update_status()

    def action_retry_failed(self) -> None:
        for dl in list(self.downloads.values()):
            if dl.status.startswith("error") or dl.status == "missing":
                dl.status, dl.percent, dl.speed, dl.eta = "queued", "—", "—", "—"
                self._update_row(dl)
                # Re-queue through the dispatcher so a concurrency slot is
                # reserved before the worker runs (see _dispatcher); calling
                # download_worker directly would bypass the gate.
                self.pending.put(dl)

    def _selected_download(self) -> Download | None:
        """The Download under the queue table's row cursor, if any."""
        try:
            table = self.query_one("#queue-table", DataTable)
        except Exception:
            return None
        if table.row_count == 0:
            return None
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
            dl_id = cell_key.row_key.value
        except Exception:
            return None
        return self.downloads.get(dl_id) if dl_id else None

    def _open_path(self, path: str) -> bool:
        """Reveal a file or folder in the OS file manager / default handler."""
        target = os.path.expanduser(path)
        if not os.path.exists(target):
            self._write_log(f"can't open — not found: {target}")
            return False
        try:
            if sys.platform.startswith("darwin"):
                subprocess.Popen(["open", target])
            elif os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", target])
            return True
        except Exception as exc:
            self._write_log(f"could not open {target}: {exc}")
            return False

    def _downloads_dir(self) -> str:
        return self.cfg["download_dir"].strip() or str(Path.home() / "Downloads")

    def action_open_downloads(self) -> None:
        ddir = self._downloads_dir()
        if self._open_path(ddir):
            self._write_log(self._voice(f"opening the nest: {ddir}", f"opening {ddir}"))

    def action_cancel_selected(self) -> None:
        dl = self._selected_download()
        if dl is None:
            return
        proc = self.procs.get(dl.id)
        if proc is None:
            self._write_log(self._voice(
                "nothing to spit out — that one isn't in flight.",
                "nothing to cancel — that row isn't downloading.",
            ))
            return
        # Mark it before terminating so the worker reports "cancelled" rather
        # than the raw SIGTERM exit code.
        self._cancelled.add(dl.id)
        try:
            proc.terminate()
        except Exception:
            pass
        self._write_log(self._voice(f"spat it back out: {dl.url}", f"cancelled: {dl.url}"))

    def action_remove_selected(self) -> None:
        dl = self._selected_download()
        if dl is None:
            return
        proc = self.procs.get(dl.id)
        if proc is not None:
            self._cancelled.add(dl.id)
            try:
                proc.terminate()
            except Exception:
                pass
        table = self.query_one("#queue-table", DataTable)
        try:
            table.remove_row(dl.id)
        except Exception:
            pass
        self.downloads.pop(dl.id, None)
        # Forget the URL so re-copying it later re-downloads instead of being
        # silently skipped by the seen-set (mirrors action_clear_done).
        self.seen.discard(dl.url)
        self.update_status()

    def action_copy_url(self) -> None:
        dl = self._selected_download()
        if dl is None:
            return
        if pyperclip is None:
            self._write_log("clipboard unavailable (pyperclip not installed).")
            return
        try:
            pyperclip.copy(dl.url)
        except Exception as exc:
            self._write_log(f"couldn't copy: {exc}")
            return
        # Don't let our own clipboard write bounce back through poll_clipboard.
        self.last_clip = dl.url
        self._write_log(self._voice(
            f"yanked back to your clipboard: {dl.url}",
            f"copied to clipboard: {dl.url}",
        ))

    def action_open_browser(self) -> None:
        dl = self._selected_download()
        if dl is None:
            return
        try:
            webbrowser.open(dl.url)
        except Exception as exc:
            self._write_log(f"couldn't open browser: {exc}")
            return
        self._write_log(f"opening in browser: {dl.url}")

    @on(DataTable.RowSelected, "#queue-table")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter on a row opens the downloaded file if we captured its path,
        # otherwise reveals the download folder.
        dl = self.downloads.get(event.row_key.value) if event.row_key else None
        if dl is not None and dl.filepath \
                and os.path.exists(os.path.expanduser(dl.filepath)):
            self._open_path(dl.filepath)
        else:
            self._open_path(self._downloads_dir())

    def action_quit(self) -> None:
        self._stopping.set()
        for proc in list(self.procs.values()):
            try:
                proc.terminate()
            except Exception:
                pass
        save_config(self.cfg)
        self.exit()


def main() -> None:
    hawktui().run()


if __name__ == "__main__":
    main()