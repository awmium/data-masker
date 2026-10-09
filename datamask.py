"""Data Masker engine: find every number in an image with Windows OCR and paint over it.

Modes
  fake   random digits, same shape (1,234.56 -> 7,051.92); the same number always gets the same fake within a run
  hash   digits become # and the format stays ($1,234.56 -> $#,###.##)
  full   the whole token becomes a block of # ($1,234.56 -> #########)
  ratio  every number is multiplied by one factor, so proportions between numbers survive

CLI:  python datamask.py <files or folders> [--mode fake|hash|full|ratio] [--factor 0.73] [--out DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

PREFIX = "Masked - "
VERSION = "2.1"
OCR_THREADS = max(1, min(4, (os.cpu_count() or 2) // 2))
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif"}
FONT_REG = "C:/Windows/Fonts/segoeui.ttf"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"

NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
KEEP_RES = [
    re.compile(r"^\(?(19|20)\d{2}\)?[,.:;]?$"),
    re.compile(r"^\(?\d{1,4}([-/.])\d{1,2}\1\d{2,4}\)?[,.]?$"),
    re.compile(r"^\d{1,2}:\d{2}(:\d{2})?([ap]m)?$", re.I),
    re.compile(r"^[A-Za-z]{1,3}\d[,.:;]?$"),
    re.compile(r"^(FY|CY)'?\d{2,4}$", re.I),
]


@dataclass
class Options:
    mode: str = "fake"
    align: str = "left"
    factor: float | None = None
    keep_dates: bool = True
    keep_percent: bool = True
    thorough: bool = False
    seed: int | None = None
    _fake_cache: dict = field(default_factory=dict)

    def resolve_factor(self) -> float:
        if self.factor is None:
            rng = random.Random(self.seed)
            self.factor = round(rng.choice([rng.uniform(0.45, 0.85), rng.uniform(1.15, 1.9)]), 3)
        return self.factor


@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float
    first: bool = True
    fh: float = 0


# ---------- OCR ----------

def _ocr_engine():
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language

    engine = OcrEngine.try_create_from_language(Language("en-US"))
    return engine or OcrEngine.try_create_from_user_profile_languages()


async def _recognize(engine, img: Image.Image) -> list[Word]:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    rgba = img.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes("raw", "BGRA"))
    bmp = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, rgba.width, rgba.height
    )
    result = await engine.recognize_async(bmp)
    words = []
    for line in result.lines:
        for wd in line.words:
            r = wd.bounding_rect
            words.append(Word(wd.text, r.x, r.y, r.width, r.height))
    return words


def _overlap(a: Word, b: Word) -> float:
    ix = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    small = min(a.w * a.h, b.w * b.h)
    if not small or iy < 0.5 * min(a.h, b.h):
        return 0
    return ix * iy / small


def normalize(token: str) -> str:
    """Undo the OCR misreads that show up on report numbers: % read as 0/0, O for 0, l/I for 1, S for $."""
    t = re.sub(r"(?<=\d)\s*[0oO]/[0oO]$", "%", token)
    t = re.sub(r"(?<=\d)(tv|rv|IVI|lVl)$", "M", t)
    t = re.sub(r"(?<=[\d,.])[Oo]|[Oo](?=[\d,.]\d)", "0", t)
    t = re.sub(r"(?<=[\d,.])[lI|]|[lI|](?=[\d,.]\d)", "1", t)
    t = re.sub(r"^([-(]?)S(?=\d)", r"\1$", t)
    if re.fullmatch(r"[-$(]*\d{1,3}(?:\.\d{3}){2,}\)?", t):
        t = t.replace(".", ",")
    return t


def _is_number(token: str) -> bool:
    """At least half the letters-and-digits are digits: '1.21M' and '$84,220' pass, 'P8L' (a misread P&L) does not."""
    alnum = [c for c in token if c.isalnum()]
    digits = sum(c.isdigit() for c in alnum)
    return digits > 0 and digits * 2 >= len(alnum)


def _group(cands: list[Word]) -> list[Word]:
    """Join overlapping number reads from all passes into one box each; the best-supported reading names it."""
    parent = list(range(len(cands)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if _overlap(cands[i], cands[j]) > 0.15:
                parent[root(i)] = root(j)

    groups: dict[int, list[Word]] = {}
    for i, w in enumerate(cands):
        groups.setdefault(root(i), []).append(w)

    out = []
    for members in groups.values():
        x0 = min(m.x for m in members)
        y0 = min(m.y for m in members)
        x1 = max(m.x + m.w for m in members)
        y1 = max(m.y + m.h for m in members)
        votes = {}
        for m in members:
            votes[m.text] = votes.get(m.text, 0) + 1
        best = max(members, key=lambda m: (m.first, m.w >= 0.85 * (x1 - x0), votes[m.text],
                                           sum(c.isdigit() for c in m.text), len(m.text)))
        if best.w >= 0.85 * (x1 - x0):
            # reads that agree on the text: take their median edges, so one read that wandered onto an
            # arrow or a neighbouring letter does not widen the erase
            agree = [m for m in members if m.text == best.text]
            x0 = float(np.median([m.x for m in agree]))
            x1 = float(np.median([m.x + m.w for m in agree]))
        hs = sorted(m.h for m in members)
        out.append(Word(best.text, x0, y0, x1 - x0, y1 - y0, fh=hs[len(hs) // 2]))
    return out


def _tiles(width: int, height: int, tw: int, th: int):
    xs = list(range(0, max(1, width - tw) + 1, tw // 2)) or [0]
    ys = list(range(0, max(1, height - th) + 1, th // 2)) or [0]
    if xs[-1] + tw < width:
        xs.append(width - tw)
    if ys[-1] + th < height:
        ys.append(height - th)
    for y in ys:
        for x in xs:
            yield max(0, x), max(0, y)


_RAPID = None
TOKEN_RE = re.compile(r"(?<![A-Za-z\d])(?:[A-Za-z]{1,3}'?)?[-+(]?[$€£]?\d[\d,./:\-]*(?:\s?%|[A-Za-z]{1,3}(?![A-Za-z]))?\)?")


def _rapid_engine():
    """RapidOCR (PaddleOCR models on ONNX, fully local). Loaded once; None when the package is missing."""
    global _RAPID
    if _RAPID is None:
        try:
            import onnxruntime as ort
            import rapidocr_onnxruntime.utils as ru
            from rapidocr_onnxruntime import RapidOCR

            class _CappedOptions(ort.SessionOptions):
                def __init__(self):
                    super().__init__()
                    self.intra_op_num_threads = OCR_THREADS
                    self.inter_op_num_threads = 1

            ru.SessionOptions = _CappedOptions
            _RAPID = RapidOCR()
            _bucket_recognizer(_RAPID.text_recognizer)
        except Exception:
            _RAPID = False
    return _RAPID or None


def _bucket_recognizer(rec) -> None:
    """ONNX Runtime re-plans for every new input shape, which made each new screenshot size cost 2-3x.
    Round batch widths up to fixed buckets and pad every batch to full size so shapes repeat across images."""
    import time as _t

    def call(img_list):
        if isinstance(img_list, np.ndarray):
            img_list = [img_list]
        n = len(img_list)
        order = np.argsort([im.shape[1] / float(im.shape[0]) for im in img_list])
        out = [["", 0.0]] * n
        size, elapse = rec.rec_batch_num, 0.0
        c, h, _ = rec.rec_image_shape
        for b in range(0, n, size):
            idx = order[b:b + size]
            ratio = max(img_list[i].shape[1] / img_list[i].shape[0] for i in idx)
            ratio = max(4, int(np.ceil(ratio / 4) * 4))
            batch = [rec.resize_norm_img(img_list[i], ratio)[None] for i in idx]
            batch += [np.zeros((1, c, h, int(h * ratio)), np.float32)] * (size - len(batch))
            t0 = _t.time()
            res = rec.postprocess_op(rec.session(np.concatenate(batch).astype(np.float32))[0])
            for k, i in enumerate(idx):
                out[i] = res[k]
            elapse += _t.time() - t0
        return out, elapse

    rec.__call__ = call
    rec.__class__ = type("BucketedRecognizer", (rec.__class__,), {"__call__": lambda self, x: call(x)})


def _trim_to_ink(arr: np.ndarray, x0: float, y0: float, x1: float, y1: float,
                 lim0: float, lim1: float) -> tuple[float, float, float, float] | None:
    """Snap an estimated span to the ink actually there: grow across small gaps for estimation drift,
    never past the limits (midpoints to the neighbouring numbers), then tighten top and bottom."""
    H, W = arr.shape[:2]
    h = y1 - y0
    gx0, gx1 = max(0, int(max(lim0, x0 - 0.3 * h))), min(W, int(min(lim1, x1 + 0.3 * h)))
    ry0, ry1 = max(0, int(y0)), min(H, int(np.ceil(y1)))
    band = arr[ry0:ry1, gx0:gx1].astype(float)
    if band.size == 0:
        return None
    bg = np.median(band.reshape(-1, 3), axis=0)
    inkmap = np.linalg.norm(band - bg, axis=2) > 60
    cols = np.flatnonzero(inkmap.any(axis=0))
    core = cols[(cols + gx0 >= x0) & (cols + gx0 <= x1)]
    if core.size == 0:
        return None
    gap = max(2, 0.22 * h)
    lo, hi = core[0], core[-1]
    for c in cols[::-1]:
        if c < lo and lo - c <= gap:
            lo = c
    for c in cols:
        if c > hi and c - hi <= gap:
            hi = c
    rows = np.flatnonzero(inkmap[:, lo:hi + 1].any(axis=1))
    return gx0 + lo, ry0 + rows[0], gx0 + hi + 1, ry0 + rows[-1] + 1


def _rapid_candidates(rgb: Image.Image) -> list[Word]:
    eng = _rapid_engine()
    if eng is None:
        return []
    arr = np.asarray(rgb)
    res, _ = eng(arr, use_cls=False)
    measure = _font(False, 100)
    out = []
    for poly, text, _score in res or []:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
        text = normalize(text)
        total = measure.getlength(text) or 1
        toks = []
        for m in TOKEN_RE.finditer(text):
            if _is_number(m.group().strip()):
                sx = bx0 + (bx1 - bx0) * measure.getlength(text[:m.start()]) / total
                ex = bx0 + (bx1 - bx0) * measure.getlength(text[:m.end()]) / total
                toks.append((m.group().strip(), sx, ex))
        for k, (tok, sx, ex) in enumerate(toks):
            lim0 = (toks[k - 1][2] + sx) / 2 if k else -1e9
            lim1 = (ex + toks[k + 1][1]) / 2 if k + 1 < len(toks) else 1e9
            box = _trim_to_ink(arr, sx, by0, ex, by1, lim0, lim1)
            if box:
                out.append(Word(tok, box[0], box[1], box[2] - box[0], box[3] - box[1]))
    return out


def find_candidates(img: Image.Image, thorough: bool = False, rapid: bool = True) -> list[Word]:
    """Two offline engines. RapidOCR reads digits reliably but returns whole lines; Windows OCR gives
    tight word boxes but skips many cells and only reads a band of text heights, so it runs at several
    scales (plus overlapping tiles when thorough). Every number any pass saw is kept."""
    from winrt.windows.media.ocr import OcrEngine

    engine = _ocr_engine()
    rgb = img.convert("RGB")
    rapid_cands = _rapid_candidates(rgb) if rapid else []
    cands: list[Word] = []
    longest = max(rgb.size)
    limit = OcrEngine.max_image_dimension
    scales = {s for s in (3, 2, 1.5, 1, 0.75, 0.5) if longest * s <= limit and 400 <= longest * s <= 6000}
    if not thorough:
        scales &= {2, 1, 0.75, 0.5} if longest > 1600 else {3, 2, 1, 0.75}
    scales = scales or {1}

    def resized(im, s):
        return im if s == 1 else im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)

    def read(im, s, ox=0, oy=0):
        for w in asyncio.run(_recognize(engine, resized(im, s))):
            text = normalize(w.text)
            junk = re.match(r"^[^\w$€£(+\-]+", text)
            if junk and len(junk.group()) < len(text):
                cut = _font(False, 100).getlength(junk.group()) / max(1, _font(False, 100).getlength(text))
                w = Word(text, w.x + w.w * cut, w.y, w.w * (1 - cut), w.h)
                text = text[junk.end():]
            if _is_number(text):
                cands.append(Word(text, ox + w.x / s, oy + w.y / s, w.w / s, w.h / s))

    for s in sorted(scales, reverse=True):
        read(rgb, s)
    if thorough:
        read(ImageOps.invert(ImageOps.autocontrast(rgb.convert("L"))).convert("RGB"), 1)
        tw, th = 640, 320
        if rgb.width > tw * 1.2 or rgb.height > th * 1.2:
            for x, y in _tiles(rgb.width, rgb.height, tw, th):
                tile = rgb.crop((x, y, min(rgb.width, x + tw), min(rgb.height, y + th)))
                read(tile, 1, x, y)
                read(ImageOps.autocontrast(tile.convert("L")).convert("RGB"), 2, x, y)
    # RapidOCR boxes are estimates split out of whole lines; where Windows read the same number, its box is exact
    cands += [r for r in rapid_cands if not any(w.text == r.text and _overlap(w, r) > 0.15 for w in cands)]
    return cands


# ---------- replacement text ----------

LABEL_RE = re.compile(r"^\d{1,3}(?!(?:[MKB]|bn|k)$)[A-Za-z]{1,3}$")


def _keep(token: str, opts: Options) -> bool:
    bare = token.strip("()[]{},.:;'\"")
    if opts.keep_dates and (any(r.search(bare) for r in KEEP_RES) or LABEL_RE.match(bare)):
        return True
    return opts.mode == "ratio" and opts.keep_percent and "%" in token


def _fake(token: str, opts: Options) -> str:
    if token in opts._fake_cache:
        return opts._fake_cache[token]
    rng = random.Random(f"{opts.seed}:{token}") if opts.seed is not None else random
    out, run_start = [], True
    for ch in token:
        if ch.isdigit():
            out.append(str(rng.randint(1, 9)) if run_start and ch != "0" else str(rng.randint(0, 9)))
            run_start = False
        else:
            out.append(ch)
            run_start = ch not in ",."
    opts._fake_cache[token] = "".join(out)
    return opts._fake_cache[token]


def _ratio(token: str, factor: float) -> str:
    def scale(m: re.Match) -> str:
        s = m.group()
        decimals = len(s.split(".")[1]) if "." in s else 0
        val = float(s.replace(",", "")) * factor
        return f"{val:,.{decimals}f}" if "," in s or val >= 10000 and len(s) > 4 else f"{val:.{decimals}f}"

    return NUMBER_RE.sub(scale, token)


def replacement(token: str, opts: Options) -> str | None:
    """Masked text for an OCR token, or None when it should stay as is."""
    if not any(c.isdigit() for c in token) or _keep(token, opts):
        return None
    if opts.mode == "hash":
        return re.sub(r"\d", "#", token)
    if opts.mode == "full":
        return "#" * max(3, len(token))
    if opts.mode == "ratio":
        return _ratio(token, opts.resolve_factor())
    return _fake(token, opts)


# ---------- painting ----------

def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    except OSError:
        return ImageFont.load_default(size)


def _fit_size(text: str, height: float, bold: bool) -> int:
    lo, hi = 4, 600
    while lo < hi:
        mid = (lo + hi + 1) // 2
        _, t, _, b = _font(bold, mid).getbbox(text, anchor="ls")
        if b - t <= height:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _density(mask: np.ndarray) -> float:
    """Ink share inside the ink's own bounding box, so loose OCR boxes do not dilute it."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return 0.0
    return float(mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1].mean())


def _plan(arr: np.ndarray, w: Word, new_text: str, opts: Options):
    """Colours, font and placement for one replacement, all read from the untouched original."""
    H, W = arr.shape[:2]
    x0, y0 = max(0, int(np.floor(w.x))), max(0, int(np.floor(w.y)))
    x1, y1 = min(W, int(np.ceil(w.x + w.w))), min(H, int(np.ceil(w.y + w.h)))
    th = w.fh or w.h
    pad = max(1, round(th * 0.15))

    ox0, oy0, ox1, oy1 = max(0, x0 - pad - 1), max(0, y0 - pad - 1), min(W, x1 + pad + 1), min(H, y1 + pad + 1)
    frame = np.zeros((oy1 - oy0, ox1 - ox0), bool)
    frame[:2, :] = frame[-2:, :] = frame[:, :2] = frame[:, -2:] = True
    border = arr[oy0:oy1, ox0:ox1][frame].astype(float)
    bg = np.median(border, axis=0) if border.size else np.array([255.0, 255.0, 255.0])
    box = arr[y0:y1, x0:x1].reshape(-1, 3).astype(float)
    if box.size == 0:
        return None
    dist = np.linalg.norm(box - bg, axis=1)
    ink = (dist > max(30, dist.max() * 0.5)).reshape(y1 - y0, x1 - x0)
    fg = np.median(box[dist >= dist.max() * 0.7], axis=0) if dist.max() > 20 else 255 - bg

    rows = np.flatnonzero(ink.any(axis=1))
    if rows.size >= 3:
        ink_top, ink_h = y0 + rows[0], rows[-1] - rows[0] + 1
        _, t0, _, b0 = _font(False, 100).getbbox(w.text, anchor="ls")
        if 0.5 * th < ink_h <= th * 1.05:
            th = ink_h
            w = Word(w.text, w.x, ink_top - (w.h - th) / 2, w.w, w.h, w.first, w.fh)
    reg_size = _fit_size(w.text, th, False)
    probe = Image.new("L", (max(1, x1 - x0), max(1, y1 - y0)), 0)
    _, t, _, _ = _font(False, reg_size).getbbox(w.text, anchor="ls")
    ImageDraw.Draw(probe).text((0, -t), w.text, fill=255, font=_font(False, reg_size), anchor="ls")
    bold = _density(ink) > _density(np.asarray(probe) > 127) * 1.35

    font = _font(bold, _fit_size(w.text, th, bold))
    _, t, _, b = font.getbbox(w.text, anchor="ls")
    baseline = w.y + (w.h - th) / 2 - t
    if opts.mode == "full":
        new_text = "#" * max(3, int(w.w / max(1, font.getlength("#"))))
    l, _, r, _ = font.getbbox(new_text, anchor="ls")
    if (r - l) > w.w * 1.08 + 2:
        font = _font(bold, max(4, int(font.size * (w.w * 1.08) / (r - l))))
        l, _, r, _ = font.getbbox(new_text, anchor="ls")
    if opts.align == "right":
        tx = w.x + w.w - r
    elif opts.align == "center":
        tx = w.x + w.w / 2 - (r - l) / 2 - l
    else:
        tx = w.x - l

    erase = [min(x0, tx + l) - pad, y0 - pad, max(x1, tx + r) + pad, y1 + pad]
    return erase, tuple(int(c) for c in bg), (tx, baseline), new_text, tuple(int(c) for c in fg), font


def _render(rgb: Image.Image, arr: np.ndarray, groups: list[Word], opts: Options) -> tuple[Image.Image, int]:
    plans = []
    for w in groups:
        new = replacement(w.text, opts)
        if new is not None:
            p = _plan(arr, w, new, opts)
            if p:
                plans.append(p)
    out = rgb.copy()
    draw = ImageDraw.Draw(out)
    for erase, bg, *_ in plans:
        draw.rectangle(erase, fill=bg)
    for _, _, pos, text, fg, font in plans:
        draw.text(pos, text, fill=fg, font=font, anchor="ls")
    return out, len(plans)


def _plausible(arr: np.ndarray, w: Word) -> bool:
    """Reject OCR 'words' that are really shapes: a row of bars can read as '-1.1'. Text is mostly background."""
    H, W = arr.shape[:2]
    if w.h > 0.25 * H or w.w > 0.8 * W:
        return False
    x0, y0, x1, y1 = int(w.x), int(w.y), int(np.ceil(w.x + w.w)), int(np.ceil(w.y + w.h))
    box = arr[max(0, y0):y1, max(0, x0):x1].reshape(-1, 3).astype(float)
    if box.size == 0:
        return False
    if w.h <= 60:
        frame = np.concatenate([arr[max(0, y0 - 2):max(0, y0), max(0, x0):x1].reshape(-1, 3),
                                arr[y1:y1 + 2, max(0, x0):x1].reshape(-1, 3)]).astype(float)
        if frame.size:
            ink = np.linalg.norm(box - np.median(frame, axis=0), axis=1) > 40
            return _density(ink.reshape(min(H, y1) - max(0, y0), -1)) < 0.72
        return True
    if w.h > 60:
        bg = np.median(box, axis=0)
        mode = np.mean(np.linalg.norm(box - bg, axis=1) < 30)
        return mode > 0.45
    return True


def _signature(groups: list[Word]) -> set:
    return {(round(g.x / 4), round(g.y / 4), round(g.w / 4)) for g in groups}


def lower_priority() -> None:
    """Run below normal priority so masking never makes the rest of the PC sluggish."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def mask_image(src: Image.Image, opts: Options, verify_rounds: int = 2) -> tuple[Image.Image, int]:
    """Mask, then OCR the result again: with the found numbers gone, OCR picks up what it skipped before.
    Repeat until a round finds nothing new. Every render starts from the original pixels."""
    rgb = src.convert("RGB")
    arr = np.asarray(rgb).copy()
    cands = [w for w in find_candidates(rgb, opts.thorough) if _plausible(arr, w)]
    groups = _group(cands)
    out, count = _render(rgb, arr, groups, opts)
    for _ in range(verify_rounds):
        # a re-read overlapping a known number may only widen it when it shows our own '#' (leftover digits
        # beside a hash mask); fake digits re-read with a neighbouring word must not widen the erase
        extra = [Word(w.text, w.x, w.y, w.w, w.h, first=False) for w in find_candidates(out, False, rapid=False)
                 if _plausible(arr, w) and ("#" in w.text or not any(_overlap(w, g) > 0.15 for g in groups))]
        new_groups = _group(cands + extra)
        if _signature(new_groups) <= _signature(groups) | {None}:
            break
        cands += extra
        groups = new_groups
        out, count = _render(rgb, arr, groups, opts)
    if src.mode in ("RGBA", "LA", "P") and "A" in src.convert("RGBA").getbands():
        out.putalpha(src.convert("RGBA").getchannel("A"))
    return out, count


# ---------- files ----------

def collect(paths: list[Path], recursive: bool = False) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            files += sorted(f for f in it if f.is_file() and f.suffix.lower() in IMAGE_EXTS
                            and not f.name.startswith(PREFIX) and not any(part.startswith(PREFIX) for part in f.relative_to(p).parts[:-1]))
        elif p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            files.append(p)
    seen, unique = set(), []
    for f in files:
        if f.resolve() not in seen:
            seen.add(f.resolve())
            unique.append(f)
    return unique


def output_path(src: Path, dest: Path | None) -> Path:
    """Explicit destination -> files land there; otherwise 'Masked - <folder>' inside the source folder."""
    folder = dest if dest else src.parent / f"{PREFIX}{src.parent.name or 'Images'}"
    return folder / f"{PREFIX}{src.name}"


def process_file(src: Path, dest: Path | None, opts: Options) -> tuple[Path, int]:
    with Image.open(src) as im:
        im.load()
        masked, n = mask_image(im, opts)
    out = output_path(src, dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".jpg", ".jpeg"):
        masked.convert("RGB").save(out, quality=95)
    else:
        masked.save(out)
    return out, n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mask every number in screenshots.")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--mode", choices=["fake", "hash", "full", "ratio"], default="fake")
    ap.add_argument("--factor", type=float, help="ratio mode multiplier (default: random 0.45-0.85 or 1.15-1.9)")
    ap.add_argument("--out", type=Path, help="destination folder (default: 'Masked - <folder>' beside the images)")
    ap.add_argument("--mask-dates", action="store_true", help="also mask years, dates, times and labels like Q1")
    ap.add_argument("--scale-percent", action="store_true", help="ratio mode: also scale percentages")
    ap.add_argument("--thorough", action="store_true", help="extra OCR passes (dark themes, faint text); slower")
    ap.add_argument("--align", choices=["left", "right", "center"], default="left", help="where a wider/narrower replacement sits")
    ap.add_argument("--recursive", action="store_true", help="include subfolders")
    a = ap.parse_args(argv)

    lower_priority()
    opts = Options(a.mode, a.align, a.factor, not a.mask_dates, not a.scale_percent, a.thorough)
    files = collect(a.paths, a.recursive)
    if not files:
        print("No images found.")
        return 1
    if a.mode == "ratio":
        print(f"Ratio factor: {opts.resolve_factor()}")
    for f in files:
        out, n = process_file(f, a.out, opts)
        print(f"{n:4d} masked  {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
