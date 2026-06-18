#!/usr/bin/env python3
"""Core text-to-handwriting renderer.

Extracted from the CLI tool — all rendering logic, no argument parsing.
"""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

try:
    import numpy as np
except Exception:
    np = None


# ── constants ──────────────────────────────────────────────────────────
A4_WIDTH_PX = 2480
A4_HEIGHT_PX = 3508
DEFAULT_DPI = 300

LINE_START_JITTER_PX = 18
LINE_STEP_JITTER_PX = 5
CHAR_ROTATION_DEGREES = 1.4
CHAR_SIDE_PAD_PX = 18

PUNCTUATION_CHARS = set(
    "\u002c\u002e\u0021\u003f\u003b\u003a"
    "\u3001\u3002\uff0c\uff01\uff1f\uff1b"
    "\u201c\u201d\u2018\u2019\uff08\uff09"
    "\u300a\u300b\u3008\u3009\u3010\u3011[]()"
)
COLON_CHARS = {":", "\uff1a"}


# ── types ──────────────────────────────────────────────────────────────
@dataclass
class RenderConfig:
    """All parameters needed to render a handwriting document."""

    font_path: Path
    output_dir: Path
    text: str = ""

    # visual
    paper: str = "white"
    ink: tuple[int, int, int] = (0, 0, 0)
    seed: int | None = None

    # layout
    margin: int = 220
    font_size: int = 90
    line_height: int = 100
    wrap_safety: int = 80

    # jitter
    font_size_jitter: float = 0.025
    line_y_jitter: float = 3.0
    char_y_jitter: float = 5.0
    char_x_jitter: float = 0.8
    latin_y_jitter: float = 1.6
    latin_x_jitter: float = 0.35

    # spacing
    char_spacing_min: float = -10.5
    char_spacing_max: float = -4.0
    punct_spacing_min: float = -24.0
    punct_spacing_max: float = -15.0
    colon_spacing_min: float = -8.0
    colon_spacing_max: float = 4.0

    # baseline drift
    baseline_drift_min: float = -0.8
    baseline_drift_max: float = 0.8
    baseline_drift_limit: float = 14.0
    baseline_drift_recovery: float = 0.35

    # baseline shock
    baseline_shock_chance: float = 0.16
    baseline_shock_min: float = 5.0
    baseline_shock_max: float = 11.0
    baseline_shock_cooldown: int = 2

    # line slope
    line_slope_chance: float = 0.5
    line_slope_min: float = -35.0
    line_slope_max: float = 35.0
    line_slope_recovery: float = 0.85
    line_slope_limit: float = 40.0

    # mistakes
    mistake_chance: float = 0.01
    mistake_crossout_lines: int = 15
    mistake_crossout_thickness: float = 4.0


@dataclass
class LaidOutPage:
    lines: list[tuple[str, float, float, float, list[bool]]] = field(default_factory=list)


# ── helpers ────────────────────────────────────────────────────────────
def _is_punctuation(char: str) -> bool:
    return char in PUNCTUATION_CHARS


def _is_colon(char: str) -> bool:
    return char in COLON_CHARS


def _is_latin_or_digit(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except OSError as exc:
        raise ValueError(f"Could not load font: {font_path}") from exc


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    if not text:
        return 0.0
    try:
        return float(font.getlength(text))
    except AttributeError:
        left, _top, right, _bottom = font.getbbox(text)
        return float(right - left)


# ── spacing ────────────────────────────────────────────────────────────
def _spacing_delta(char: str, width: float, config: RenderConfig, rng: random.Random | None = None) -> float:
    if _is_colon(char):
        minimum, maximum = config.colon_spacing_min, config.colon_spacing_max
    elif _is_punctuation(char):
        minimum = max(config.punct_spacing_min, -width * 0.78)
        maximum = max(config.punct_spacing_max, -width * 0.45)
    else:
        minimum, maximum = config.char_spacing_min, config.char_spacing_max

    if minimum > maximum:
        minimum, maximum = maximum, minimum
    if rng is None:
        return (minimum + maximum) / 2
    return rng.uniform(minimum, maximum)


def _effective_char_width(font: ImageFont.FreeTypeFont, char: str, config: RenderConfig, rng: random.Random | None = None) -> float:
    width = _text_width(font, char)
    return max(1.0, width + _spacing_delta(char, width, config, rng))


# ── text wrapping ──────────────────────────────────────────────────────
def _wrap_line(line: str, font: ImageFont.FreeTypeFont, max_width: int, config: RenderConfig) -> list[str]:
    normalized = line.replace("\t", "    ").rstrip("\r")
    if normalized == "":
        return [""]

    wrapped: list[str] = []
    current = ""
    current_width = 0.0

    for char in normalized:
        char_width = _effective_char_width(font, char, config)
        if current and current_width + char_width > max_width:
            wrapped.append(current.rstrip())
            current = "" if char.isspace() else char
            current_width = 0.0 if char.isspace() else char_width
        else:
            current += char
            current_width += char_width

    if current or not wrapped:
        wrapped.append(current.rstrip())
    return wrapped


def _iter_wrapped_lines(text: str, font: ImageFont.FreeTypeFont, max_width: int, config: RenderConfig) -> Iterable[str | None]:
    sections = text.replace("\r\n", "\n").replace("\r", "\n").split("\f")
    for section_index, section in enumerate(sections):
        if section_index > 0:
            yield None
        for source_line in section.split("\n"):
            for wrapped in _wrap_line(source_line, font, max_width, config):
                yield wrapped


# ── mistakes ───────────────────────────────────────────────────────────
def _inject_mistakes(line: str, config: RenderConfig, rng: random.Random) -> tuple[str, list[bool]]:
    if config.mistake_chance <= 0 or not line:
        return line, [False] * len(line)

    chars: list[str] = []
    mistakes: list[bool] = []

    for char in line:
        if char.isspace() or _is_punctuation(char):
            chars.append(char)
            mistakes.append(False)
        elif rng.random() < config.mistake_chance:
            chars.append(char)  # mistaken
            mistakes.append(True)
            chars.append(char)  # correction
            mistakes.append(False)
        else:
            chars.append(char)
            mistakes.append(False)

    return "".join(chars), mistakes


def _draw_crossout(tile: Image.Image, ink: tuple[int, int, int], config: RenderConfig, rng: random.Random) -> Image.Image:
    draw = ImageDraw.Draw(tile)
    w, h = tile.size
    ink_alpha = (*ink, 255)
    cx, cy = w // 2, h // 2
    num_blobs = max(config.mistake_crossout_lines, 12)
    stroke_w = max(1, int(config.font_size * config.mistake_crossout_thickness / 100))

    max_r = int(min(w, h) * 0.24)
    min_r = int(max_r * 0.25)

    for _ in range(num_blobs):
        rx = rng.randint(min_r, max_r)
        ry = rng.randint(min_r, max_r)
        jx = rng.randint(-w // 10, w // 10)
        jy = rng.randint(-h // 10, h // 10)
        bbox = (cx + jx - rx, cy + jy - ry, cx + jx + rx, cy + jy + ry)
        draw.ellipse(bbox, outline=ink_alpha, width=stroke_w)

    return tile


def _split_mistake_line(text: str, mistakes: list[bool], font: ImageFont.FreeTypeFont, max_width: int, config: RenderConfig) -> list[tuple[str, list[bool]]]:
    result: list[tuple[str, list[bool]]] = []
    current_chars: list[str] = []
    current_mistakes: list[bool] = []
    current_width = 0.0

    for ch, is_mistake in zip(text, mistakes):
        ch_width = _effective_char_width(font, ch, config) if not ch.isspace() else config.font_size * 0.5
        if current_chars and current_width + ch_width > max_width:
            result.append(("".join(current_chars), current_mistakes))
            current_chars = []
            current_mistakes = []
            current_width = 0.0
        current_chars.append(ch)
        current_mistakes.append(is_mistake)
        current_width += ch_width

    if current_chars:
        result.append(("".join(current_chars), current_mistakes))
    return result


# ── page layout ────────────────────────────────────────────────────────
def _layout_pages(text: str, font: ImageFont.FreeTypeFont, config: RenderConfig, rng: random.Random) -> list[LaidOutPage]:
    max_width = A4_WIDTH_PX - config.margin * 2 - config.wrap_safety
    max_y = A4_HEIGHT_PX - config.margin
    pages: list[LaidOutPage] = []
    current = LaidOutPage()
    y = config.margin
    baseline_drift = 0.0
    baseline_shock_cooldown = 0
    line_slope = 0.0

    def finish_page() -> None:
        nonlocal current, y, baseline_drift, baseline_shock_cooldown, line_slope
        pages.append(current)
        current = LaidOutPage()
        y = config.margin
        baseline_drift = 0.0
        baseline_shock_cooldown = 0
        line_slope = 0.0

    for wrapped_line in _iter_wrapped_lines(text, font, max_width, config):
        if wrapped_line is None:
            finish_page()
            continue
        line_step = config.line_height + rng.uniform(-LINE_STEP_JITTER_PX, LINE_STEP_JITTER_PX)
        if y + line_step > max_y and current.lines:
            finish_page()

        injected_text, mistake_flags = _inject_mistakes(wrapped_line, config, rng)
        mistake_chunks = [(injected_text, mistake_flags)]
        total_width = sum(
            _effective_char_width(font, ch, config) if not ch.isspace() else config.font_size * 0.5
            for ch in injected_text
        )
        if total_width > max_width:
            mistake_chunks = _split_mistake_line(injected_text, mistake_flags, font, max_width - 1, config)

        for chunk_text, chunk_flags in mistake_chunks:
            if y + line_step > max_y and current.lines:
                finish_page()
            current.lines.append((chunk_text, y, baseline_drift, line_slope, chunk_flags))
            y += line_step

        baseline_drift *= config.baseline_drift_recovery
        baseline_drift += rng.uniform(config.baseline_drift_min, config.baseline_drift_max)
        if baseline_shock_cooldown > 0:
            baseline_shock_cooldown -= 1
        elif rng.random() < config.baseline_shock_chance:
            direction = -1 if rng.random() < 0.5 else 1
            baseline_drift += direction * rng.uniform(config.baseline_shock_min, config.baseline_shock_max)
            baseline_shock_cooldown = config.baseline_shock_cooldown
        baseline_drift = max(-config.baseline_drift_limit, min(config.baseline_drift_limit, baseline_drift))
        line_slope *= config.line_slope_recovery
        if rng.random() < config.line_slope_chance:
            line_slope += rng.uniform(config.line_slope_min, config.line_slope_max)
        line_slope = max(-config.line_slope_limit, min(config.line_slope_limit, line_slope))

    if current.lines or not pages:
        pages.append(current)
    return pages


# ── paper background ───────────────────────────────────────────────────
def _add_noise(base: Image.Image, rng: random.Random) -> Image.Image:
    if np is None:
        draw = ImageDraw.Draw(base, "RGBA")
        for _ in range(5000):
            x = rng.randrange(A4_WIDTH_PX)
            y = rng.randrange(A4_HEIGHT_PX)
            shade = rng.randrange(220, 255)
            alpha = rng.randrange(8, 20)
            draw.point((x, y), fill=(shade, shade, shade, alpha))
        return base

    arr = np.asarray(base).astype(np.int16)
    rng.normalvariate(0, 1)  # keep RNG stream tied to seed
    np_rng = np.random.default_rng(rng.randrange(0, 2**32 - 1))
    arr += np_rng.normal(0, 3.2, arr.shape).astype(np.int16)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _apply_vignette(image: Image.Image) -> Image.Image:
    if np is None:
        return image
    yy, xx = np.mgrid[0:A4_HEIGHT_PX, 0:A4_WIDTH_PX]
    cx = A4_WIDTH_PX / 2
    cy = A4_HEIGHT_PX / 2
    distance = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
    shade = np.clip((distance - 0.45) * 18, 0, 18).astype(np.uint8)
    arr = np.asarray(image).astype(np.int16)
    arr -= shade[:, :, None]
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _draw_paper_pattern(image: Image.Image, config: RenderConfig, rng: random.Random) -> None:
    draw = ImageDraw.Draw(image, "RGBA")

    if config.paper == "lined":
        line_color = (125, 157, 176, 58)
        y = config.margin + int(config.line_height * 0.72)
        while y < A4_HEIGHT_PX - config.margin // 2:
            wobble = rng.randint(-2, 2)
            draw.line((config.margin - 60, y + wobble, A4_WIDTH_PX - config.margin + 60, y + wobble), fill=line_color, width=2)
            y += config.line_height
        draw.line((config.margin - 46, config.margin // 2, config.margin - 46, A4_HEIGHT_PX - config.margin // 2), fill=(196, 78, 82, 42), width=2)

    elif config.paper == "grid":
        grid_color = (125, 157, 176, 34)
        step = max(32, config.line_height // 2)
        for x in range(config.margin // 2, A4_WIDTH_PX - config.margin // 2 + 1, step):
            draw.line((x, config.margin // 2, x, A4_HEIGHT_PX - config.margin // 2), fill=grid_color, width=1)
        for y in range(config.margin // 2, A4_HEIGHT_PX - config.margin // 2 + 1, step):
            draw.line((config.margin // 2, y, A4_WIDTH_PX - config.margin // 2, y), fill=grid_color, width=1)


def _create_paper(config: RenderConfig, rng: random.Random) -> Image.Image:
    if config.paper == "white":
        return Image.new("RGB", (A4_WIDTH_PX, A4_HEIGHT_PX), (255, 255, 255))

    image = Image.new("RGB", (A4_WIDTH_PX, A4_HEIGHT_PX), (247, 245, 237))
    image = _add_noise(image, rng)
    draw = ImageDraw.Draw(image, "RGBA")

    for _ in range(420):
        x = rng.randrange(0, A4_WIDTH_PX)
        y = rng.randrange(0, A4_HEIGHT_PX)
        length = rng.randrange(18, 95)
        color = rng.choice(((219, 211, 188, 22), (255, 255, 255, 18), (202, 196, 176, 16)))
        draw.line((x, y, min(A4_WIDTH_PX, x + length), y + rng.randint(-1, 1)), fill=color, width=1)

    _draw_paper_pattern(image, config, rng)
    return _apply_vignette(image)


# ── character rendering ────────────────────────────────────────────────
def _render_character(char: str, font: ImageFont.FreeTypeFont, ink: tuple[int, int, int], rng: random.Random, line_height: int) -> Image.Image:
    bbox = font.getbbox(char)
    width = max(1, int(max(_text_width(font, char), bbox[2] - bbox[0])))
    ascent, descent = font.getmetrics()
    line_box_height = max(line_height, ascent + descent + 20)
    vertical_pad = max(4, (line_box_height - ascent - descent) // 2)
    baseline = vertical_pad + ascent
    left_pad = CHAR_SIDE_PAD_PX
    tile = Image.new("RGBA", (width + left_pad * 2 + 8, line_box_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    try:
        draw.text((left_pad, baseline), char, font=font, fill=(*ink, 255), anchor="ls")
    except TypeError:
        draw.text((left_pad - bbox[0], vertical_pad - bbox[1]), char, font=font, fill=(*ink, 255))
    angle = rng.uniform(-CHAR_ROTATION_DEGREES, CHAR_ROTATION_DEGREES)
    return tile.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)


# ── line rendering ─────────────────────────────────────────────────────
def _render_text_line(image: Image.Image, line: str, y: float, baseline_drift: float, line_slope: float, mistakes: list[bool], config: RenderConfig, font_cache: dict[int, ImageFont.FreeTypeFont], rng: random.Random) -> None:
    if not line:
        return

    x = config.margin + rng.uniform(-LINE_START_JITTER_PX, LINE_START_JITTER_PX)
    line_y = y + baseline_drift + rng.uniform(-config.line_y_jitter, config.line_y_jitter)
    max_line_width = max(1, A4_WIDTH_PX - config.margin * 2 - config.wrap_safety)

    for idx, char in enumerate(line):
        if char.isspace():
            x += config.font_size * rng.uniform(0.42, 0.62)
            continue

        local_size = max(8, int(round(config.font_size * rng.uniform(1 - config.font_size_jitter, 1 + config.font_size_jitter))))
        if local_size not in font_cache:
            font_cache[local_size] = _load_font(config.font_path, local_size)
        font = font_cache[local_size]

        tile = _render_character(char, font, config.ink, rng, config.line_height)

        is_mistake = idx < len(mistakes) and mistakes[idx]
        if is_mistake:
            tile = _draw_crossout(tile, config.ink, config, rng)

        x_jitter = config.latin_x_jitter if _is_latin_or_digit(char) else config.char_x_jitter
        y_jitter = config.latin_y_jitter if _is_latin_or_digit(char) else config.char_y_jitter
        progress = max(0.0, min(1.0, (x - config.margin) / max_line_width))
        slope_y = line_slope * progress
        paste_x = int(round(x - CHAR_SIDE_PAD_PX + rng.uniform(-x_jitter, x_jitter)))
        paste_y = int(round(line_y + slope_y + rng.uniform(-y_jitter, y_jitter)))
        image.paste(tile, (paste_x, paste_y), tile)
        x += _effective_char_width(font, char, config, rng)


# ── page rendering ─────────────────────────────────────────────────────
def _render_page(page: LaidOutPage, page_number: int, config: RenderConfig) -> Image.Image:
    rng_seed = (config.seed if config.seed is not None else random.randrange(0, 2**31)) + page_number * 7919
    rng = random.Random(rng_seed)
    image = _create_paper(config, rng)
    font_cache = {config.font_size: _load_font(config.font_path, config.font_size)}

    for line, y, baseline_drift, line_slope, mistakes in page.lines:
        _render_text_line(image, line, y, baseline_drift, line_slope, mistakes, config, font_cache, rng)

    return image


# ── PDF ────────────────────────────────────────────────────────────────
def _save_pdf(page_paths: list[Path], pdf_path: Path) -> None:
    pdf_width, pdf_height = A4
    doc = canvas.Canvas(str(pdf_path), pagesize=A4, invariant=1)
    for page_path in page_paths:
        doc.drawImage(ImageReader(str(page_path)), 0, 0, width=pdf_width, height=pdf_height)
        doc.showPage()
    doc.save()


# ── public API ─────────────────────────────────────────────────────────
def render_document(config: RenderConfig) -> tuple[list[Path], Path]:
    """Render text to PNG pages and a PDF. Returns (png_paths, pdf_path)."""
    config.output_dir.mkdir(parents=True, exist_ok=True)

    layout_font = _load_font(config.font_path, config.font_size)
    layout_seed = config.seed if config.seed is not None else random.randrange(0, 2**31)
    pages = _layout_pages(config.text, layout_font, config, random.Random(layout_seed))

    page_paths: list[Path] = []
    for index, page in enumerate(pages, start=1):
        image = _render_page(page, index, config)
        page_path = config.output_dir / f"page-{index:03d}.png"
        image.save(page_path, dpi=(DEFAULT_DPI, DEFAULT_DPI))
        page_paths.append(page_path)

    pdf_path = config.output_dir / "handwriting.pdf"
    _save_pdf(page_paths, pdf_path)
    return page_paths, pdf_path


def render_document_from_string(text: str, config: RenderConfig) -> tuple[list[Path], Path]:
    """Render a string to PNG pages and PDF. Convenience wrapper."""
    config.text = text
    return render_document(config)


# ── default font ───────────────────────────────────────────────────────
def default_font_path() -> Path:
    """Return the path to the bundled default handwriting font."""
    return Path(__file__).resolve().parent / "fonts" / "青叶手写体.ttf"
