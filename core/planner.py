class CollaborativePlanner:
    PLAN_TRIGGERS = (
        "build ", "create ", "make ", "develop ", "generate app",
        "write code for", "fix ", "refactor ", "check my backend",
        "audit ", "plan "
    )

    def __init__(self):
        self.current_plan_id: int | None = None
        self.current_goal: str = ""
        self.current_plan_text: str = ""
        self.status: str = "IDLE"  # IDLE | DISCUSSING | AWAITING_APPROVAL | EXECUTING | COMPLETED

    def is_build_or_audit_request(self, user_text: str) -> bool:
        lower = user_text.strip().lower()
        return any(trigger in lower for trigger in self.PLAN_TRIGGERS)

    def build_planning_prompt(self, user_text: str, project_dir: str) -> str:
        return (
            f"The user says: \"{user_text}\"\n"
            f"Current active project folder: {project_dir}\n\n"
            "Act like a friendly, sharp software architect partner (J.A.R.V.I.S. style):\n"
            "1. Briefly acknowledge the goal warmly.\n"
            "2. If key requirements are missing (e.g., database, framework, auth, or target folder), ask 1-2 short clarifying questions.\n"
            "3. Propose a clear, numbered Step-by-Step Development / Inspection Plan.\n"
            "4. End by asking: 'Do you want to adjust anything, or should I execute this plan?'\n"
            "Do NOT output huge walls of code yet—agree on the plan first."
        )

    def set_pending_plan(self, plan_id: int, goal: str, plan_text: str):
        self.current_plan_id = plan_id
        self.current_goal = goal
        self.current_plan_text = plan_text
        self.status = "AWAITING_APPROVAL"

    def approve_plan(self):
        self.status = "EXECUTING"

    def reject_plan(self):
        self.status = "IDLE"
        self.current_plan_id = None
        self.current_plan_text = ""