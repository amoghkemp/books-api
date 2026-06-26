from abc import ABC, abstractmethod

class LLM(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        """
        Generate text from a prompt
        Every provider must implement this.
        """
        pass
    
