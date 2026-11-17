import os

SUPPORTED_EXTENSIONS = (".java", ".py", ".dart", ".sql", ".yml", ".yaml", ".properties", ".json")


def list_project_files(project_path: str, max_files: int = 40) -> list[str]:
    """Finds source files inside a project directory, skipping build/git folders."""
    found = []
    ignore_dirs = {".git", "target", "build", ".gradle", ".idea", "venv", "__pycache__", "node_modules"}

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            if file.endswith(SUPPORTED_EXTENSIONS):
                found.append(os.path.join(root, file))
                if len(found) >= max_files:
                    return found
    return found


def read_code_file(file_path: str, max_chars: int = 12000) -> str:
    """Safely reads a source file for AI inspection."""
    if not os.path.exists(file_path):
        return f"[ERROR]: File not found -> {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(max_chars)
        return content
    except Exception as e:
        return f"[ERROR]: Could not read file -> {str(e)}"