#!/usr/bin/env python3
"""MCP Server for Handwriting Batch Renderer.

Exposes text-to-handwriting rendering as an MCP tool.
Supports both stdio (local) and SSE/HTTP (deployment) transports.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .renderer import (
    RenderConfig,
    default_font_path,
    render_document,
)

# ── tools ──────────────────────────────────────────────────────────────
def handwriting_render(
    text: str,
    font_path: str | None = None,
    paper: str = "white",
    ink: str = "#000000",
    seed: int | None = None,
    font_size: int = 90,
    line_height: int = 100,
    margin: int = 220,
    wrap_safety: int = 80,
    font_size_jitter: float = 0.025,
    line_y_jitter: float = 3.0,
    char_y_jitter: float = 5.0,
    char_x_jitter: float = 0.8,
    latin_y_jitter: float = 1.6,
    latin_x_jitter: float = 0.35,
    char_spacing_min: float = -10.5,
    char_spacing_max: float = -4.0,
    punct_spacing_min: float = -24.0,
    punct_spacing_max: float = -15.0,
    colon_spacing_min: float = -8.0,
    colon_spacing_max: float = 4.0,
    baseline_drift_min: float = -0.8,
    baseline_drift_max: float = 0.8,
    baseline_drift_limit: float = 14.0,
    baseline_drift_recovery: float = 0.35,
    baseline_shock_chance: float = 0.16,
    baseline_shock_min: float = 5.0,
    baseline_shock_max: float = 11.0,
    baseline_shock_cooldown: int = 2,
    line_slope_chance: float = 0.5,
    line_slope_min: float = -35.0,
    line_slope_max: float = 35.0,
    line_slope_recovery: float = 0.85,
    line_slope_limit: float = 40.0,
    mistake_chance: float = 0.01,
    mistake_crossout_lines: int = 15,
    mistake_crossout_thickness: float = 4.0,
    preset: str | None = None,
) -> dict[str, Any]:
    """Render text content as handwriting-style A4 pages.

    Takes plain text and returns PNG images + PDF of the text rendered
    as realistic handwriting with configurable jitter, drift, and mistakes.

    Parameters:
        text: The text content to render as handwriting.
        font_path: Path to a .ttf/.otf font file. Uses built-in handwriting
                   font if omitted.
        paper: Background style — "white", "plain", "lined", or "grid".
        ink: Ink color in #RRGGBB format.
        seed: Fixed random seed for reproducible output.
        font_size: Base font size in pixels.
        line_height: Line height in pixels.
        margin: Page margin in pixels.
        wrap_safety: Right-side wrapping safety margin in pixels.
        font_size_jitter: Per-character font size jitter ratio.
        line_y_jitter: Per-line vertical jitter in pixels.
        char_y_jitter: Per-character vertical jitter in pixels.
        char_x_jitter: Per-character horizontal jitter (CJK + punctuation).
        latin_y_jitter: Per-character vertical jitter for Latin chars.
        latin_x_jitter: Per-character horizontal jitter for Latin chars.
        char_spacing_min: Tightest normal char spacing.
        char_spacing_max: Loosest normal char spacing.
        punct_spacing_min: Tightest punctuation spacing.
        punct_spacing_max: Loosest punctuation spacing.
        colon_spacing_min: Tightest colon spacing.
        colon_spacing_max: Loosest colon spacing.
        baseline_drift_min: Minimum small baseline drift per line.
        baseline_drift_max: Maximum small baseline drift per line.
        baseline_drift_limit: Maximum cumulative baseline drift.
        baseline_drift_recovery: Drift retained on next line (0–1).
        baseline_shock_chance: Chance per line of a large baseline shift.
        baseline_shock_min: Minimum large baseline shift.
        baseline_shock_max: Maximum large baseline shift.
        baseline_shock_cooldown: Lines to wait before another large shift.
        line_slope_chance: Chance per line of a baseline slope.
        line_slope_min: Minimum right-edge slope offset.
        line_slope_max: Maximum right-edge slope offset.
        line_slope_recovery: Slope retained on next line (0–1).
        line_slope_limit: Maximum slope offset.
        mistake_chance: Probability per character of a cross-out mistake.
        mistake_crossout_lines: Number of scribble strokes over a mistake.
        mistake_crossout_thickness: Scribble thickness as % of font size.
        preset: Name of a JSON preset file to load defaults from.

    Returns:
        A dict with:
        - page_count: number of pages
        - pages: list of base64-encoded PNG images
        - pdf_base64: base64-encoded PDF
        - output_dir: local temp directory path
    """
    # Resolve font
    if font_path:
        fp = Path(font_path)
    else:
        fp = default_font_path()

    if not fp.is_file():
        # Try relative to preset dir
        alt = Path(__file__).resolve().parent.parent / "presets" / str(font_path or "")
        if alt.is_file():
            fp = alt
        else:
            raise FileNotFoundError(f"Font file not found: {font_path or 'bundled default'}")

    # Parse ink color
    raw_ink = ink.strip()
    if raw_ink.startswith("#"):
        raw_ink = raw_ink[1:]
    ink_rgb = tuple(int(raw_ink[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]

    # Load preset
    preset_data: dict[str, Any] = {}
    if preset:
        preset_dir = Path(__file__).resolve().parent.parent / "presets"
        preset_path = preset_dir / preset if not preset.endswith(".json") else Path(preset)
        if not preset_path.is_file():
            preset_path = preset_dir / f"{preset}.json"
        if preset_path.is_file():
            preset_data = json.loads(preset_path.read_text(encoding="utf-8"))

    # Output dir
    output_dir = Path(tempfile.mkdtemp(prefix="handwriting-mcp-"))

    config = RenderConfig(
        font_path=fp,
        output_dir=output_dir,
        text="",  # set via render_document_from_string
        paper=preset_data.get("paper", paper),
        ink=ink_rgb,
        seed=seed,
        margin=preset_data.get("margin", margin),
        font_size=preset_data.get("font_size", font_size),
        line_height=preset_data.get("line_height", line_height),
        wrap_safety=preset_data.get("wrap_safety", wrap_safety),
        font_size_jitter=preset_data.get("font_size_jitter", font_size_jitter),
        line_y_jitter=preset_data.get("line_y_jitter", line_y_jitter),
        char_y_jitter=preset_data.get("char_y_jitter", char_y_jitter),
        char_x_jitter=preset_data.get("char_x_jitter", char_x_jitter),
        latin_y_jitter=preset_data.get("latin_y_jitter", latin_y_jitter),
        latin_x_jitter=preset_data.get("latin_x_jitter", latin_x_jitter),
        char_spacing_min=preset_data.get("char_spacing_min", char_spacing_min),
        char_spacing_max=preset_data.get("char_spacing_max", char_spacing_max),
        punct_spacing_min=preset_data.get("punct_spacing_min", punct_spacing_min),
        punct_spacing_max=preset_data.get("punct_spacing_max", punct_spacing_max),
        colon_spacing_min=preset_data.get("colon_spacing_min", colon_spacing_min),
        colon_spacing_max=preset_data.get("colon_spacing_max", colon_spacing_max),
        baseline_drift_min=preset_data.get("baseline_drift_min", baseline_drift_min),
        baseline_drift_max=preset_data.get("baseline_drift_max", baseline_drift_max),
        baseline_drift_limit=preset_data.get("baseline_drift_limit", baseline_drift_limit),
        baseline_drift_recovery=preset_data.get("baseline_drift_recovery", baseline_drift_recovery),
        baseline_shock_chance=preset_data.get("baseline_shock_chance", baseline_shock_chance),
        baseline_shock_min=preset_data.get("baseline_shock_min", baseline_shock_min),
        baseline_shock_max=preset_data.get("baseline_shock_max", baseline_shock_max),
        baseline_shock_cooldown=preset_data.get("baseline_shock_cooldown", baseline_shock_cooldown),
        line_slope_chance=preset_data.get("line_slope_chance", line_slope_chance),
        line_slope_min=preset_data.get("line_slope_min", line_slope_min),
        line_slope_max=preset_data.get("line_slope_max", line_slope_max),
        line_slope_recovery=preset_data.get("line_slope_recovery", line_slope_recovery),
        line_slope_limit=preset_data.get("line_slope_limit", line_slope_limit),
        mistake_chance=preset_data.get("mistake_chance", mistake_chance),
        mistake_crossout_lines=preset_data.get("mistake_crossout_lines", mistake_crossout_lines),
        mistake_crossout_thickness=preset_data.get("mistake_crossout_thickness", mistake_crossout_thickness),
    )

    # Render
    config.text = text
    page_paths, pdf_path = render_document(config)

    # Encode results
    pages_b64 = []
    for pp in sorted(page_paths):
        pages_b64.append(base64.b64encode(pp.read_bytes()).decode("ascii"))

    pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")

    return {
        "page_count": len(page_paths),
        "pages": pages_b64,
        "pdf_base64": pdf_b64,
        "output_dir": str(output_dir),
    }


def handwriting_list_presets() -> list[dict[str, str]]:
    """List available handwriting rendering presets.

    Returns a list of preset names and descriptions.
    """
    preset_dir = Path(__file__).resolve().parent.parent / "presets"
    if not preset_dir.exists():
        return []

    results: list[dict[str, str]] = []
    for preset_file in sorted(preset_dir.glob("*.json")):
        try:
            data = json.loads(preset_file.read_text(encoding="utf-8"))
            results.append({
                "name": preset_file.stem,
                "description": data.get("description", f"Preset: {preset_file.stem}"),
                "paper": data.get("paper", "white"),
                "font_size": str(data.get("font_size", 90)),
            })
        except (json.JSONDecodeError, OSError):
            results.append({
                "name": preset_file.stem,
                "description": f"Preset: {preset_file.stem} (unreadable)",
            })
    return results


# ── entry points ───────────────────────────────────────────────────────
def _create_server(transport: str = "stdio") -> FastMCP:
    """Create a FastMCP server instance configured for the given transport."""
    kwargs: dict[str, Any] = {
        "instructions": "Render text as realistic handwritten A4 pages (PNG + PDF).",
    }
    if transport in ("sse", "streamable-http"):
        kwargs["host"] = os.environ.get("HOST", "0.0.0.0")
        kwargs["port"] = int(os.environ.get("PORT", "8080"))
    srv = FastMCP("handwriting-mcp", **kwargs)
    srv.add_tool(handwriting_render)
    srv.add_tool(handwriting_list_presets)
    return srv


def main() -> None:
    """Entry point for stdio MCP server."""
    server = _create_server("stdio")
    server.run(transport="stdio")


def main_sse() -> None:
    """Entry point for SSE/HTTP MCP server (deployment)."""
    server = _create_server("sse")
    server.run(transport="sse")


if __name__ == "__main__":
    main()
