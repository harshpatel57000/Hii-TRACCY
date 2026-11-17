import ollama
from memory.db import TraccyMemory


class TraccyBrain:
    def __init__(self, model: str = "qwen2.5-coder:7b", memory: TraccyMemory | None = None):
        self.model = model
        self.memory = memory or TraccyMemory()
        self.system_prompt = (
            "You are TRACCY, a friendly, brilliant personal AI engineering partner inspired by J.A.R.V.I.S. "
            "You converse naturally like a friend, ask necessary clarifying questions, propose clear step-by-step plans "
            "for user approval before creating/editing files or running high-impact actions, and deliver crisp engineering results."
        )
        self.history = [{"role": "system", "content": self.system_prompt}]
        # Restore recent memory across restarts
        self.history.extend(self.memory.get_recent_messages(limit=16))

    def ask(self, user_prompt: str, stateless: bool = False, save_to_memory: bool = True) -> str:
        if stateless:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        else:
            self.history.append({"role": "user", "content": user_prompt})
            if save_to_memory:
                self.memory.save_message("user", user_prompt)
            messages = self.history

        response = ollama.chat(model=self.model, messages=messages)
        reply = response["message"]["content"]

        if not stateless:
            self.history.append({"role": "assistant", "content": reply})
            if save_to_memory:
                self.memory.save_message("assistant", reply)
        return reply