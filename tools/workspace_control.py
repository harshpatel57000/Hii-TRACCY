import os
import re
import shutil
import subprocess
from core.permissions import PermissionManager


class WorkspaceController:
    """
    Autonomous file writer, VS Code launcher, and safe build/test runner
    guarded strictly by PermissionManager.
    """

    ALLOWED_CMD_PREFIXES = (
        "python ",
        "pytest",
        "mvn ",
        "mvnw ",
        "gradle ",
        "gradlew ",
        "flutter ",
        "dart ",
        "npm ",
        "npx ",
        "git status",
        "git diff",
        "git log",
    )

    def __init__(self, permissions: PermissionManager):
        self.permissions = permissions

    def extract_files_from_markdown(self, markdown_text: str) -> list[tuple[str, str]]:
        """
        Extracts file blocks formatted like:
        ```java src/main/java/com/app/AuthController.java
        ...code...
        ```
        or preceded by a filename header such as `### path/to/file.py` or `**path/to/file.py**`.
        """
        extracted: list[tuple[str, str]] = []
        pattern = re.compile(
            r"(?:(?:^|\n)\s*(?:#{1,4}|\*\*|File:)\s*`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)`?\**\s*\r?\n)?"
            r"```[A-Za-z0-9_-]*[ \t]+([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)[ \t]*\r?\n"
            r"([\s\S]*?)```"
            r"|(?:^|\n)\s*(?:#{1,4}|\*\*|File:)\s*`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)`?\**\s*\r?\n"
            r"```[A-Za-z0-9_-]*[ \t]*\r?\n"
            r"([\s\S]*?)```",
            re.MULTILINE,
        )

        for m in pattern.finditer(markdown_text):
            rel_path = (m.group(2) or m.group(1) or m.group(4) or "").strip().lstrip("/\\")
            code = (m.group(3) if m.group(3) is not None else m.group(5)) or ""
            if rel_path and ".." not in rel_path.split("/") and ".." not in rel_path.split("\\"):
                extracted.append((rel_path, code.strip() + "\n"))

        return extracted

    def write_project_file(self, relative_path: str, content: str) -> tuple[bool, str]:
        """Creates parent directories and writes a file inside the approved workspace."""
        base_dir = self.permissions.approved_project_dir
        target_path = os.path.abspath(os.path.join(base_dir, relative_path))

        allowed, reason = self.permissions.check_action("WRITE_FILE", target_path)
        if not allowed:
            return False, f"[BLOCKED]: {reason}"

        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True, target_path
        except Exception as e:
            return False, f"[WRITE ERROR]: {e}"

    def write_all_from_response(self, markdown_text: str) -> list[tuple[bool, str]]:
        """Parses all file blocks from an approved AI execution response and writes them to disk."""
        files = self.extract_files_from_markdown(markdown_text)
        results = []
        for rel_path, code in files:
            results.append(self.write_project_file(rel_path, code))
        return results

    def open_in_vscode(self, file_path: str | None = None) -> tuple[bool, str]:
        """Launches VS Code on the approved project folder (and optional target file)."""
        workspace = self.permissions.approved_project_dir
        if not os.path.isdir(workspace):
            return False, f"Workspace directory does not exist: {workspace}"

        code_bin = shutil.which("code") or shutil.which("code.cmd")
        if not code_bin:
            return False, "VS Code CLI ('code') was not found in Windows PATH."

        cmd = [code_bin, workspace]
        if file_path and os.path.isfile(file_path):
            cmd.extend(["-g", file_path])

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return True, f"Opened in VS Code: {file_path or workspace}"
        except Exception as e:
            return False, f"Failed to launch VS Code: {e}"

    def run_safe_command(self, command: str, timeout_sec: int = 45) -> tuple[bool, str]:
        """Runs a whitelisted build/test command inside the approved project directory."""
        clean_cmd = command.strip()
        allowed, reason = self.permissions.check_action("RUN_COMMAND", clean_cmd)
        if not allowed:
            return False, f"[BLOCKED]: {reason}"

        lower_cmd = clean_cmd.lower()
        if not any(lower_cmd.startswith(prefix) for prefix in self.ALLOWED_CMD_PREFIXES):
            return False, f"[SAFETY]: Command '{clean_cmd}' is not in the allowed build/test whitelist."

        try:
            proc = subprocess.run(
                clean_cmd,
                cwd=self.permissions.approved_project_dir,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            combined = (proc.stdout + "\n" + proc.stderr).strip()
            if len(combined) > 3500:
                combined = combined[-3500:]
            return (proc.returncode == 0), f"Exit Code: {proc.returncode}\n{combined}"
        except subprocess.TimeoutExpired:
            return False, f"[TIMEOUT]: Command exceeded {timeout_sec}s limit."
        except Exception as e:
            return False, f"[EXECUTION ERROR]: {e}"