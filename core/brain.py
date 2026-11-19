import os
import urllib.request
import json
import ollama
from core.conversation import ConversationContext
from memory.db import TraccyMemory


class TraccyBrain:
    def __init__(
        self,
        model: str = "qwen2.5:3b",
        fallback_model: str = "llama3.2:1b",
        memory: TraccyMemory | None = None,
    ):
        self.model = model
        self.fallback_model = fallback_model
        self.memory = memory or TraccyMemory()
        self.context = ConversationContext()
        self.client = ollama.Client(host="http://127.0.0.1:11434", timeout=60.0)

        # Optional Hybrid Cloud mode (set GROQ_API_KEY env var for <0.3s 70B cloud intelligence)
        self.hybrid_cloud_enabled = False
        self.groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()

        self.system_prompt = (
            "You are TRACCY, a warm, intelligent conversational AI companion similar to ChatGPT Voice. "
            "The user may speak in Indian English, imperfect grammar, incomplete thoughts, or Gujarati. "
            "Always infer their true intent, resolve pronouns (it, that, this) using conversation history, "
            "and respond naturally in 2 to 4 clear, spoken-friendly sentences. "
            "Ask a brief clarifying question if something is ambiguous. "
            "NEVER output code blocks unless the user explicitly asks for code or approves a plan."
        )
        self.history = [{"role": "system", "content": self.system_prompt}]

    def clear_memory(self):
        self.context.reset()
        self.history = [{"role": "system", "content": self.system_prompt}]

    def _prepare_messages(
        self, user_prompt: str, stateless: bool, save_to_memory: bool, detected_lang: str = "en"
    ) -> list[dict]:
        if stateless:
            return [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt[:3000]},
            ]

        enriched_prompt = self.context.update_from_user_turn(user_prompt, detected_lang)
        self.history.append({"role": "user", "content": enriched_prompt})
        if save_to_memory:
            self.memory.save_message("user", user_prompt)

        # Sliding window: system prompt + last 6 turns (3 exchanges) for fast CPU speed + context
        if len(self.history) > 7:
            self.history = [self.history[0]] + self.history[-6:]
        return self.history

    def _stream_groq_cloud(self, messages: list[dict], max_tokens: int):
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.4,
            "stream": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.groq_api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                data = json.loads(line[6:])
                delta = data["choices"][0]["delta"].get("content", "")
                if delta:
                    yield delta

    def ask_stream(
        self,
        user_prompt: str,
        stateless: bool = False,
        save_to_memory: bool = True,
        max_tokens: int = 160,
        detected_lang: str = "en",
    ):
        """Streams tokens live from Hybrid Cloud (if enabled) or local Ollama (qwen2.5:3b -> llama3.2:1b)."""
        messages = self._prepare_messages(user_prompt, stateless, save_to_memory, detected_lang)
        full_chunks = []

        if self.hybrid_cloud_enabled and self.groq_api_key:
            try:
                for token in self._stream_groq_cloud(messages, max_tokens):
                    full_chunks.append(token)
                    yield token
                self._commit_assistant_reply("".join(full_chunks), stateless, save_to_memory)
                return
            except Exception:
                full_chunks.clear()  # Fall back seamlessly to local Ollama

        for candidate_model in (self.model, self.fallback_model):
            try:
                stream = self.client.chat(
                    model=candidate_model,
                    messages=messages,
                    stream=True,
                    keep_alive="30m",
                    options={
                        "num_predict": max_tokens,
                        "num_ctx": 2048,
                        "temperature": 0.4,
                    },
                )
                for chunk in stream:
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_chunks.append(token)
                        yield token
                break
            except Exception as err:
                if candidate_model == self.fallback_model:
                    err_msg = f"[OLLAMA ERROR]: {err}"
                    full_chunks.append(err_msg)
                    yield err_msg

        self._commit_assistant_reply("".join(full_chunks), stateless, save_to_memory)

    def _commit_assistant_reply(self, full_reply: str, stateless: bool, save_to_memory: bool):
        if not stateless and full_reply:
            self.history.append({"role": "assistant", "content": full_reply})
            if save_to_memory:
                self.memory.save_message("assistant", full_reply)