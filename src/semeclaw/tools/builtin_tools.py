"""Built-in tools for SemeClaw."""

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
    """Execute a shell command and return output.

    Args:
        command: Shell command to execute

    Returns:
        Command stdout and stderr combined
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds"
    except Exception as e:
        return f"Error executing command: {e}"
