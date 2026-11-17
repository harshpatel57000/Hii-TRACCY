import os


class PermissionManager:
    HIGH_RISK_KEYWORDS = {
        "delete", "remove", "drop table", "truncate", "rm -rf",
        "del ", "format", "deploy", "shutdown", "restart server",
        "alter table", "production", ".env", "password", "secret"
    }

    def __init__(self, approved_project_dir: str = ""):
        self.approved_project_dir = os.path.abspath(approved_project_dir) if approved_project_dir else ""
        self.active_plan_approved = False

    def set_approved_project(self, folder_path: str):
        self.approved_project_dir = os.path.abspath(folder_path)

    def is_within_approved_folder(self, target_path: str) -> bool:
        if not self.approved_project_dir:
            return False
        abs_target = os.path.abspath(target_path)
        return abs_target.startswith(self.approved_project_dir)

    def check_action(self, action_type: str, target: str = "") -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        action_type: 'READ', 'ANALYZE', 'WRITE_FILE', 'BUILD_TEST', 'DESTRUCTIVE'
        """
        lower_target = target.lower()
        if any(word in lower_target for word in self.HIGH_RISK_KEYWORDS):
            return False, f"Blocked: '{target}' involves high-risk or sensitive data. Explicit confirmation required."

        if action_type in {"READ", "ANALYZE"}:
            return True, "Automatic read/analysis allowed."

        if action_type in {"WRITE_FILE", "BUILD_TEST"}:
            if not self.active_plan_approved:
                return False, "Plan not approved yet. Discuss and approve the plan first."
            if target and not self.is_within_approved_folder(target):
                return False, f"Target '{target}' is outside the approved project folder."
            return True, "Allowed within approved plan scope."

        return False, "Explicit user approval required for high-impact operations."