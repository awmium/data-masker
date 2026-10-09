"""Data Masker window: an Awmium-styled pywebview shell over the datamask engine."""
from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import webview
from webview.dom import DOMEventHandler
from PIL import Image, ImageDraw, ImageFont

import datamask as dm

HERE = Path(__file__).parent
ICON = HERE / "assets" / "icon.ico"
SETTINGS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "awmium" / "data-masker" / "settings.json"
DEFAULTS = {"theme": "dark", "mode": "full", "align": "left", "factor": "", "keep_dates": True,
            "keep_percent": True, "thorough": False, "recursive": False, "dest": ""}
ENGINE = threading.Lock()


def _data_url(img: Image.Image, max_side: int = 1800) -> str:
    im = img.convert("RGB")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _warm_image() -> Image.Image:
    """A small fake report so the OCR models compile their shapes before the first real image."""
    im = Image.new("RGB", (1400, 520), "#f6f6f6")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype(dm.FONT_REG, 22)
    except OSError:
        f = ImageFont.load_default()
    for r in range(8):
        for c, v in enumerate(["Region", "1,284,530", "-21.6%", "$84,220.50", "0.77M", "12,880"]):
            d.text((30 + c * 220, 30 + r * 60), v, fill="#222", font=f)
    return im


class Api:
    def __init__(self):
        self._window: webview.Window | None = None
        self._items: list[dict] = []
        self._next_id = 1
        self._running = False
        self._stop_flag = False
        self._engine_state = "warming"
        self._orig_cache: dict[str, str] = {}

    # ---------- plumbing ----------
    def _emit(self, payload: dict) -> None:
        if self._window:
            try:
                self._window.evaluate_js(f"window.ui && ui.on({json.dumps(payload)})")
            except Exception:
                pass

    def _public(self, it: dict) -> dict:
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in it.items()}

    def info(self) -> dict:
        return {"version": dm.VERSION, "threads": dm.OCR_THREADS, "engine": self._engine_state,
                "settings": self.load_settings(), "items": [self._public(i) for i in self._items]}

    def load_settings(self) -> dict:
        try:
            return {**DEFAULTS, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
        except Exception:
            return dict(DEFAULTS)

    def save_settings(self, s: dict) -> bool:
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({**DEFAULTS, **s}, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    # ---------- queue ----------
    def add_paths(self, paths: list[str], recursive: bool = False) -> list[dict]:
        known = {i["path"] for i in self._items}
        added = []
        for f in dm.collect([Path(p) for p in paths if p], recursive):
            if str(f) in known:
                continue
            it = {"id": self._next_id, "path": str(f), "name": f.name, "folder": f.parent.name,
                  "status": "queued", "count": None, "out": None, "secs": None, "error": None}
            self._next_id += 1
            self._items.append(it)
            added.append(it)
        return [self._public(i) for i in added]

    def remove(self, ids: list[int]) -> bool:
        if self._running:
            return False
        self._items = [i for i in self._items if i["id"] not in set(ids)]
        return True

    def clear(self) -> bool:
        if self._running:
            return False
        self._items.clear()
        self._orig_cache.clear()
        return True

    def pick_files(self, recursive: bool = False) -> list[dict]:
        exts = ";".join(f"*{e}" for e in sorted(dm.IMAGE_EXTS))
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                             file_types=(f"Images ({exts})", "All files (*.*)"))
        return self.add_paths(list(res or []), recursive)

    def pick_folder(self, recursive: bool = False) -> list[dict]:
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return self.add_paths(list(res or []), recursive)

    def pick_dest(self) -> str:
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return res[0] if res else ""

    def open_path(self, path: str) -> bool:
        p = Path(path)
        target = p.parent if p.is_file() else p
        if target.exists():
            os.startfile(target)
            return True
        return False

    # ---------- preview ----------
    def preview(self, item_id: int) -> dict:
        it = next((i for i in self._items if i["id"] == item_id), None)
        if not it:
            return {}
        out = {"original": None, "masked": None}
        try:
            if it["path"] not in self._orig_cache:
                with Image.open(it["path"]) as im:
                    self._orig_cache[it["path"]] = _data_url(im)
            out["original"] = self._orig_cache[it["path"]]
            if it["out"] and Path(it["out"]).exists():
                with Image.open(it["out"]) as im:
                    out["masked"] = _data_url(im)
        except Exception as e:
            out["error"] = str(e)
        return out

    # ---------- run ----------
    def start(self, s: dict) -> bool:
        if self._running or not self._items:
            return False
        factor = None
        if s.get("mode") == "ratio" and str(s.get("factor", "")).strip():
            try:
                factor = float(s["factor"])
            except ValueError:
                factor = None
        opts = dm.Options(s.get("mode", "full"), s.get("align", "left"), factor, bool(s.get("keep_dates", True)),
                          bool(s.get("keep_percent", True)), bool(s.get("thorough", False)))
        dest = Path(s["dest"]) if s.get("dest") else None
        todo = [i for i in self._items if i["status"] != "done"] or self._items
        for i in todo:
            i.update(status="queued", count=None, out=None, secs=None, error=None)
            self._emit({"type": "item", "item": self._public(i)})
        self._running, self._stop_flag = True, False
        threading.Thread(target=self._work, args=(todo, dest, opts), daemon=True).start()
        return True

    def stop(self) -> bool:
        self._stop_flag = True
        return True

    def _work(self, todo: list[dict], dest: Path | None, opts: dm.Options) -> None:
        total, done, numbers, t0 = len(todo), 0, 0, time.time()
        if opts.mode == "ratio":
            self._emit({"type": "note", "text": f"ratio factor {opts.resolve_factor()}"})
        for it in todo:
            if self._stop_flag:
                break
            it["status"] = "running"
            self._emit({"type": "item", "item": self._public(it)})
            started = time.time()
            try:
                with ENGINE:
                    out, n = dm.process_file(Path(it["path"]), dest, opts)
                it.update(status="done", count=n, out=str(out), secs=round(time.time() - started, 1))
                numbers += n
            except Exception as e:
                it.update(status="error", error=str(e))
            done += 1
            self._emit({"type": "item", "item": self._public(it)})
            self._emit({"type": "progress", "done": done, "total": total})
        for it in todo:
            if it["status"] in ("queued", "running"):
                it["status"] = "queued"
                self._emit({"type": "item", "item": self._public(it)})
        self._running = False
        self._emit({"type": "finished", "done": done, "total": total, "numbers": numbers,
                    "secs": round(time.time() - t0, 1), "stopped": self._stop_flag,
                    "factor": opts.factor if opts.mode == "ratio" else None})

    def warm(self) -> None:
        try:
            with ENGINE:
                im = _warm_image()
                dm._rapid_candidates(im)
                dm.find_candidates(im, rapid=False)
            self._engine_state = "ready" if dm._rapid_engine() else "windows-only"
        except Exception:
            self._engine_state = "windows-only"
        self._emit({"type": "engine", "state": self._engine_state})


def _set_app_identity() -> None:
    """Own taskbar entry and icon instead of python's."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("awmium.data-masker")
    except Exception:
        pass


def _apply_icon(title: str) -> None:
    try:
        user32 = ctypes.windll.user32
        hwnd = 0
        for _ in range(50):
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return
        big = user32.LoadImageW(None, str(ICON), 1, 256, 256, 0x10)
        small = user32.LoadImageW(None, str(ICON), 1, 32, 32, 0x10)
        user32.SendMessageW(hwnd, 0x80, 1, big)
        user32.SendMessageW(hwnd, 0x80, 0, small)
    except Exception:
        pass


def main() -> None:
    dm.lower_priority()
    _set_app_identity()
    api = Api()
    title = "Data Masker"
    window = webview.create_window(title, str(HERE / "ui" / "index.html"), js_api=api, width=1320, height=860,
                                   min_size=(1000, 660), background_color="#141311", text_select=False)
    api._window = window

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        recursive = api.load_settings().get("recursive", False)
        added = api.add_paths(paths, recursive)
        api._emit({"type": "added", "items": added, "dropped": len(paths)})

    def on_loaded():
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        threading.Thread(target=_apply_icon, args=(title,), daemon=True).start()
        threading.Thread(target=api.warm, daemon=True).start()

    window.events.loaded += on_loaded
    webview.start(private_mode=False, storage_path=str(SETTINGS.parent / "webview"))


if __name__ == "__main__":
    main()
