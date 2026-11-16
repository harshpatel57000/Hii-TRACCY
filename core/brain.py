import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen2.5-coder:7b"

SYSTEM_PROMPT = """
You are TRACCY (Testing, Refactoring, Analysis & Code Control Yard), an autonomous AI engineering assistant.
Your core mission is finding bugs, refactoring/improving code, testing REST APIs, checking performance, and automating developer tasks.
Keep responses sharp, technical, actionable, and concise.
"""


class TraccyBrain:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]

    def ask(self, user_message: str, stateless: bool = False) -> str:
        if stateless:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ]
        else:
            self.history.append({"role": "user", "content": user_message})
            messages = self.history

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=300.0) as client:
                response = client.post(OLLAMA_URL, json=payload)
                response.raise_for_status()
                reply = response.json()["message"]["content"]
                if not stateless:
                    self.history.append({"role": "assistant", "content": reply})
                return reply
        except httpx.ConnectError:
            return "[ERROR]: Cannot reach Ollama at localhost:11434. Start Ollama first."
        except httpx.ReadTimeout:
            return "[ERROR]: Ollama took longer than 5 minutes. Try a smaller file or run 'ollama run qwen2.5-coder:7b' once in terminal to pre-load it."
        except Exception as e:
            return f"[ERROR]: AI Engine failure -> {str(e)}"