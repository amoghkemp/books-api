import os
from .gemini import geminiLLM
from .groq import GroqLLM

providers = {
    "gemini": geminiLLM,
    "groq": GroqLLM,
}

def create_llm():
    provider = os.getenv("LLM_provider")

    if provider not in providers:
        raise Exception(f"Unsupported provider: {provider}")
    
    return providers[provider]()