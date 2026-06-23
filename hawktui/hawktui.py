#!/usr/bin/env python3

from __future__ import annotations

__version__ = "1.0.1"

import os
import re
import shutil
import subprocess
import threading
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

from textual import on, work
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

PROG = "@@YTW@@" 
PROGRESS_TEMPLATE = (
    f"download:{PROG}\t%(progress._percent_str)s\t%(progress._speed_str)s"
    f"\t%(progress._eta_str)s\t%(info.title)s"
)
URL_RE = re.compile(r"https?://[^\s<>\"']+")

PREFERRED_THEMES = [
    "nord", "tokyo-night", "gruvbox", "catppuccin-mocha", "dracula",
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
    "theme": "nord",
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
        cmd += ["--download-archive", os.path.join(ddir, ".yui-archive.txt")]

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

    def acquire(self):
        with self.cv:
            while self.active >= self.limit:
                self.cv.wait()
            self.active += 1

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
        self._counter = count(1)
        try:
            self.gate = Gate(int(self.cfg["max_parallel"]))
        except (TypeError, ValueError):
            self.gate = Gate(2)
        self.col_keys: list = []

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
                yield self._row("URL filter (substring)", self._in("url_filter", "leave empty for any http(s)"))
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

    def _check_environment(self) -> None:
        if shutil.which("yt-dlp") is None:
            self._write_log("WARNING: yt-dlp not found on PATH (pip install yt-dlp).")
        if pyperclip is None:
            self._write_log("WARNING: pyperclip not installed; clipboard watch disabled.")
        elif shutil.which("xclip") is None and shutil.which("xsel") is None:
            self._write_log("WARNING: install xclip or xsel for X11 clipboard access.")
        self._write_log("ready. press 'w' to toggle watching, 'a' to add a URL.")

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
        bar.update(
            f"{state}   active {active}  queued {queued}  done {done}  failed {failed}"
            f"   │ dir: {self.cfg['download_dir']}   │ parallel: {self.gate.limit}"
        )

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
        for url in URL_RE.findall(text):
            self.enqueue(url)

    def enqueue(self, url: str) -> None:
        url = url.strip()
        if not url or url in self.seen:
            return
        flt = self.cfg["url_filter"].strip()
        if flt and flt not in url:
            return
        self.seen.add(url)
        dl = Download(id=f"d{next(self._counter)}", url=url)
        self.downloads[dl.id] = dl
        table = self.query_one("#queue-table", DataTable)
        table.add_row(dl.id[1:], dl.display_title(), dl.status, dl.percent,
                      dl.speed, dl.eta, key=dl.id)
        self._write_log(f"queued: {url}")
        self.download_worker(dl)

    @work(thread=True, group="downloads")
    def download_worker(self, dl: Download) -> None:
        self.gate.acquire()
        try:
            dl.status = "downloading"
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
                    self.call_from_thread(self._write_log, line)
            proc.wait()
            dl.returncode = proc.returncode
            if proc.returncode == 0:
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

    def _update_row(self, dl: Download) -> None:
        table = self.query_one("#queue-table", DataTable)
        try:
            table.update_cell(dl.id, self.col_keys[1], dl.display_title())
            table.update_cell(dl.id, self.col_keys[2], dl.status)
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
        self.cfg[key] = value
        save_config(self.cfg)

    @on(Switch.Changed)
    def _on_switch(self, event: Switch.Changed) -> None:
        if event.switch.id and event.switch.id.startswith("set-"):
            self._persist(event.switch.id[4:], bool(event.value))

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
            self._persist(event.input.id[4:], event.value)

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
        for url in URL_RE.findall(text) or [text]:
            self.enqueue(url)
        box.value = ""

    def action_toggle_watch(self) -> None:
        self.watching = not self.watching
        if self.watching and pyperclip is not None:
            try:
                self.last_clip = (pyperclip.paste() or "").strip()
            except Exception:
                pass
        self._write_log("watching ON" if self.watching else "watching PAUSED")
        self.update_status()

    def action_focus_add(self) -> None:
        self.query_one(TabbedContent).active = "tab-queue"
        self.query_one("#add-url", Input).focus()

    def action_list_formats(self) -> None:
        url = self.query_one("#add-url", Input).value.strip()
        if not url:
            self._write_log("enter a URL in the Add box first, then press 'f'.")
            return
        m = URL_RE.findall(url)
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
        self.update_status()

    def action_retry_failed(self) -> None:
        for dl in list(self.downloads.values()):
            if dl.status.startswith("error") or dl.status == "missing":
                dl.status, dl.percent, dl.speed, dl.eta = "queued", "—", "—", "—"
                self._update_row(dl)
                self.download_worker(dl)

    def action_quit(self) -> None:
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