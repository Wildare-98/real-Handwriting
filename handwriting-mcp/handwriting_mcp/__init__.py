"""Handwriting MCP Server — text-to-handwriting rendering via MCP protocol."""

from .renderer import RenderConfig, render_document, render_document_from_string

__all__ = ["RenderConfig", "render_document", "render_document_from_string"]
