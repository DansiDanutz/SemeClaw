"""Document-to-Markdown conversion tool using Microsoft markitdown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from semeclaw.tools.base import BaseTool


_TOOL_NAME = "convert_document"
_TOOL_DESC = (
    "Convert a file to Markdown text. Supports PDF, DOCX, PPTX, XLSX, "
    "HTML, EPUB, images (EXIF/OCR), audio metadata, and more. "
    "Returns the extracted text content as Markdown."
)
_TOOL_PARAMS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to convert",
        },
    },
    "required": ["path"],
}


class MarkitdownTool(BaseTool):
    """Convert files (PDF, DOCX, PPTX, XLSX, HTML, images, etc.) to Markdown."""

    def __init__(self) -> None:
        super().__init__(_TOOL_NAME, _TOOL_DESC, _TOOL_PARAMS)

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        path = args.get("path", "")
        file_path = Path(path).expanduser().resolve()

        if not file_path.exists():
            return f"Error: File not found: {path}"
        if not file_path.is_file():
            return f"Error: Not a file: {path}"

        try:
            from markitdown import MarkItDown

            md = MarkItDown()
            result = md.convert(str(file_path))
            text = result.text_content or ""

            if not text.strip():
                return f"(No extractable content from {file_path.name})"

            # Truncate very large outputs to keep context manageable
            max_chars = 50_000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"

            return text
        except ImportError:
            return "Error: markitdown not installed. Run: pip install markitdown"
        except Exception as e:
            return f"Error converting {file_path.name}: {e}"


def create_markitdown_tool() -> MarkitdownTool:
    """Create a markitdown tool instance."""
    return MarkitdownTool()
