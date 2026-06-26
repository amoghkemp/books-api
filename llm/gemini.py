import os
import google.generativeai as genai
from .base import LLM

class geminiLLM(LLM):
    def __init__(self):
        genai.configure(
            api_key=os.getenv("gemini_api_key")
        )

        self.model = genai.GenerativeModel("gemini-2.5-flash-lite")

    def generate(self, prompt: str) -> str:
        response = self.model.generate_content(prompt)
        return response.text