"""Built-in tools for SemeClaw."""

import shlex
import shutil
import subprocess
from pathlib import Path

from semeclaw.tools.base import tool


@tool
def read_file(path: str) -> str:
    """Read a file and return its contents.

    Args:
        path: Path to the file to read

    Returns:
        File contents as string
    """
    try:
        content = Path(path).read_text()
        return content
    except Exception as e:
        return f"Error reading file: {e}"


@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file.

    Args:
        path: Path to the file to write
        content: Content to write to the file

    Returns:
        Success or error message
    """
    try:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


@tool
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace text in a file.

    Args:
        path: Path to the file to edit
        old_text: Text to find and replace
        new_text: Text to replace with

    Returns:
        Success or error message
    """
    try:
        file_path = Path(path)
        content = file_path.read_text()

        if old_text not in content:
            return f"Error: Text not found in {path}"

        new_content = content.replace(old_text, new_text)
        file_path.write_text(new_content)
        return f"Successfully edited {path}"
    except Exception as e:
        return f"Error editing file: {e}"


@tool
def bash(command: str) -> str:
    """Execute a simple command safely and return output.

    Args:
        command: Command to execute. Shell operators like ;, |, &&, > are rejected.

    Returns:
        Command stdout and stderr combined
    """
    if any(token in command for token in ("\n", "\r", "&&", "||", ";", "|", ">", "<", "`", "$()")):
        return (
            "Error: unsafe shell syntax detected. "
            "Use a single command with explicit arguments instead of shell operators."
        )

    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"Error parsing command: {e}"

    if not argv:
        return "Error: Command is empty"

    try:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = result.stdout + result.stderr
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds"
    except FileNotFoundError:
        return f"Error: Command not found: {argv[0]}"
    except Exception as e:
        return f"Error executing command: {e}"


@tool
def markitdown_convert(input_path: str, output_path: str = "") -> str:
    """Convert a local file to Markdown using MarkItDown.

    Args:
        input_path: Path to the source file to convert
        output_path: Optional path for the generated markdown file

    Returns:
        Converted markdown or a success/error message
    """
    source = Path(input_path).expanduser()
    if not source.exists():
        return f"Error: File not found: {source}"
    if not source.is_file():
        return f"Error: Not a file: {source}"

    markitdown_bin = shutil.which("markitdown")
    if not markitdown_bin:
        return "Error: markitdown is not installed or not available on PATH"

    try:
        command = [markitdown_bin, str(source)]

        if output_path:
            destination = Path(output_path).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            command.extend(["-o", str(destination)])
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                return (
                    f"Error converting file: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
                )
            return f"Successfully converted {source} to {destination}"

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            return (
                f"Error converting file: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
            )
        return result.stdout.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: MarkItDown conversion timed out after 120 seconds"
    except Exception as e:
        return f"Error converting file: {e}"
