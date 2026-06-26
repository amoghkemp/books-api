import os
from groq import Groq
from .base import LLM

class GroqLLM(LLM):
    def __init__(self):
        self.client = Groq(
            api_key=os.getenv("groq_api_key")
        )

        self.model = "qwen/qwen3-32b"

    def generate(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages = [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=1,
            reasoning_effort="none"
        )

        return response.choices[0].message.content