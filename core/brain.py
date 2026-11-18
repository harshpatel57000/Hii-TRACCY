import ollama
from memory.db import TraccyMemory


class TraccyBrain:
    def __init__(
        self,
        model: str = "llama3.2:1b",
        memory: TraccyMemory | None = None,
    ):
        self.model = model
        self.memory = memory or TraccyMemory()
        self.client = ollama.Client(host="http://127.0.0.1:11434", timeout=60.0)
        self.system_prompt = (
            "You are TRACCY, a sharp personal AI assistant like J.A.R.V.I.S. "
            "Reply in 2 to 3 short, natural sentences suitable for voice. "
            "NEVER write code blocks unless the user explicitly approves a plan."
        )
        self.history = [{"role": "system", "content": self.system_prompt}]

    def clear_memory(self):
        self.history = [{"role": "system", "content": self.system_prompt}]

    def _prepare_messages(self, user_prompt: str, stateless: bool, save_to_memory: bool) -> list[dict]:
        if stateless:
            return [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt[:2500]},
            ]
        self.history.append({"role": "user", "content": user_prompt})
        if save_to_memory:
            self.memory.save_message("user", user_prompt)
        if len(self.history) > 5:
            self.history = [self.history[0]] + self.history[-4:]
        return self.history

    def ask(
        self,
        user_prompt: str,
        stateless: bool = False,
        save_to_memory: bool = True,
        max_tokens: int = 110,
    ) -> str:
        messages = self._prepare_messages(user_prompt, stateless, save_to_memory)
        try:
            response = self.client.chat(
                model=self.model,
                messages=messages,
                keep_alive="30m",
                options={"num_predict": max_tokens, "num_ctx": 1024, "temperature": 0.3},
            )
            reply = response["message"]["content"]
        except Exception as err:
            return f"[OLLAMA ERROR]: {err}"

        if not stateless:
            self.history.append({"role": "assistant", "content": reply})
            if save_to_memory:
                self.memory.save_message("assistant", reply)
        return reply

    def ask_stream(
        self,
        user_prompt: str,
        stateless: bool = False,
        save_to_memory: bool = True,
        max_tokens: int = 110,
    ):
        """Yields word-by-word tokens live as Ollama generates them (like an Iterator<String>)."""
        messages = self._prepare_messages(user_prompt, stateless, save_to_memory)
        full_chunks = []
        try:
            stream = self.client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                keep_alive="30m",
                options={"num_predict": max_tokens, "num_ctx": 1024, "temperature": 0.3},
            )
            for chunk in stream:
                token = chunk.get("message", {}).get("content", "")
                if token:
                    full_chunks.append(token)
                    yield token
        except Exception as err:
            err_msg = f"[OLLAMA ERROR]: {err}"
            full_chunks.append(err_msg)
            yield err_msg

        full_reply = "".join(full_chunks)
        if not stateless and full_reply:
            self.history.append({"role": "assistant", "content": full_reply})
            if save_to_memory:
                self.memory.save_message("assistant", full_reply)