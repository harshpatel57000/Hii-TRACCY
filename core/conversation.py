import re


class ConversationContext:
    """
    Tracks active subjects/entities and conversational intent across turns
    (similar to a Spring @SessionScope bean for dialogue state).
    """

    TECH_PATTERNS = [
        r"\b(flutter(?:\s+app(?:lication)?)?)\b",
        r"\b(spring\s*boot(?:\s+(?:api|backend|server|app))?)\b",
        r"\b(mysql|postgresql|sqlite|database|db)\b",
        r"\b(rest\s*api|api|endpoint|backend|frontend)\b",
        r"\b(jwt|authentication|security|token)\b",
        r"\b(aws|ec2|docker|ubuntu|linux)\b",
        r"\b(python|java|dart|pyside6|ollama|traccy)\b",
    ]

    PRONOUNS = {"it", "that", "this", "they", "them", "there"}

    EXPLICIT_PLAN_TRIGGERS = (
        "create a plan",
        "make a plan",
        "step-by-step plan",
        "build a complete",
        "generate project",
        "scaffold project",
    )

    def __init__(self):
        self.active_entities: list[str] = []
        self.last_language: str = "en"
        self.turn_count: int = 0

    def reset(self):
        self.active_entities.clear()
        self.turn_count = 0

    def update_from_user_turn(self, text: str, detected_lang: str = "en") -> str:
        """
        Extracts subjects from user speech and appends subtle reference hints
        when pronouns ('it', 'that', 'this') appear in follow-up questions.
        """
        self.turn_count += 1
        if detected_lang:
            self.last_language = detected_lang

        lower = text.lower()
        for pattern in self.TECH_PATTERNS:
            for match in re.findall(pattern, lower):
                clean = match.strip()
                if clean and clean not in self.active_entities:
                    self.active_entities.append(clean)
                    if len(self.active_entities) > 4:
                        self.active_entities.pop(0)

        words = set(re.findall(r"\b[a-z]+\b", lower))
        has_pronoun = bool(words & self.PRONOUNS)

        if has_pronoun and self.active_entities:
            context_hint = ", ".join(self.active_entities[-2:])
            return f"{text}\n[Context Note: User's 'it/that/this' likely refers to: {context_hint}]"

        return text

    def should_trigger_formal_plan(self, text: str) -> bool:
        """
        Prevents casual chat ('can you change this UI?') from being hijacked
        by the heavy project-scaffolding planner.
        """
        lower = text.lower().strip()
        return any(trigger in lower for trigger in self.EXPLICIT_PLAN_TRIGGERS)