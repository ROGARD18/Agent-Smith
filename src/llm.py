import os
import requests
import re
from dotenv import load_dotenv

load_dotenv()


def generate_response(prompt: str, model: str = "nvidia/nemotron-3.5-lightning:free") -> str:
    """Give a prompt to the LLM and return the answer."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("The OPENROUTER_API_KEY key not found in environement.")

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']


def extract_python_code(llm_response: str) -> str | None:
    """Extract python code from markdown format llm answer"""
    match = re.search(r"```python\n(.*?)\n```", llm_response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None
