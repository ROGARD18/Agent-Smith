import os
import re
import json
import time
import requests
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List

class TokenManager:
    """Manages rotation of multiple API keys to bypass free-tier rate limits."""
    def __init__(self, api_url: str):
        self.api_url = api_url
        self.keys = []
        # Chargement dynamique des clés d'API (sans préfixe hardcodé)
        for key, value in os.environ.items():
            if "API_KEY" in key and value:
                self.keys.append(value)
        
        if not self.keys:
            raise ValueError("No API keys found in environment variables.")
        
        self.current_index = 0

    def get_current_key(self) -> str:
        return self.keys[self.current_index]

    def rotate_key(self):
        self.current_index = (self.current_index + 1) % len(self.keys)
        print(f"[*] Switching to API key index: {self.current_index}")


def generate_chat_response(
    messages: List[Dict[str, str]],
    token_manager: TokenManager,
    model: str = "gemini-3.5-flash",
    max_retries: int = 5
) -> Dict[str, Any]:
    start_time = time.perf_counter()
    retries_used = 0
    
    for attempt in range(max_retries):
        api_key = token_manager.get_current_key()
        
        try:
            response = requests.post(
                url=f"{token_manager.api_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 1500, # Limite stricte pour éviter l'explosion
                    "stop": ["```\n", "<end_code>", "</tool_call>"] # Force l'arrêt après le code
                },
                timeout=120
            )
            
            if response.status_code in [429, 402]:
                token_manager.rotate_key()
                # Pause uniquement après avoir grillé TOUTES les clés du pool lors de cette salve
                if (attempt + 1) % len(token_manager.keys) == 0:
                    time.sleep(15)
                retries_used += 1
                continue
                
            if response.status_code in [500, 502, 503, 504]:
                sleep_time = 2 ** attempt
                print(f"[!] API Error {response.status_code}. Server unavailable. Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
                retries_used += 1
                continue
                
            if response.status_code != 200:
                print(f"\n[!] API Error Details: {response.text}\n")
                response.raise_for_status()
            
            data = response.json()
            usage = data.get('usage', {})
            
            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError) as e:
                raise Exception(f"Unexpected API response structure: {data}") from e
            
            raw_text = message.get("content") or ""
            if not raw_text and "tool_calls" in message:
                raw_text = str(message["tool_calls"])
                
            request_time_ms = (time.perf_counter() - start_time) * 1000
            
            return {
                "content": raw_text,
                "input_tokens": usage.get('prompt_tokens', 0),
                "output_tokens": usage.get('completion_tokens', 0),
                "request_time_ms": request_time_ms,
                "api_url": token_manager.api_url,
                "model_name": model,
                "retries": retries_used
            }
            
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            sleep_time = 2 ** attempt
            print(f"[!] Network error: {e}. Retrying in {sleep_time}s...")
            time.sleep(sleep_time)
            retries_used += 1

    raise Exception("Max retries exceeded across all available API keys.")

def extract_python_code(llm_response: str) -> Optional[str]:
    python_match = re.search(r"```python\n(.*?)\n```", llm_response, re.DOTALL)
    if python_match:
        return python_match.group(1).strip()

    json_match = re.search(r"<tool_call>(.*?)</tool_call>", llm_response, re.DOTALL)
    if json_match:
        try:
            tool_data = json.loads(json_match.group(1).strip())
            name = tool_data.get("name")
            args = tool_data.get("arguments", {})
            kwargs_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            return f"result = {name}({kwargs_str})\nprint(result)"
        except json.JSONDecodeError:
            pass

    xml_match = re.search(r"<invoke\s+name=[\"'](.*?)[\"']>(.*?)</invoke>", llm_response, re.DOTALL)
    if xml_match:
        try:
            name = xml_match.group(1).strip()
            params_raw = xml_match.group(2)
            root = ET.fromstring(f"<root>{params_raw}</root>")
            
            kwargs = []
            for param in root.findall('parameter'):
                p_name = param.get('name')
                p_value = param.text
                if p_name and p_value is not None:
                    try:
                        parsed_val = json.loads(p_value)
                        kwargs.append(f"{p_name}={repr(parsed_val)}")
                    except json.JSONDecodeError:
                        kwargs.append(f"{p_name}={repr(p_value)}")
            
            kwargs_str = ", ".join(kwargs)
            return f"result = {name}({kwargs_str})\nprint(result)"
        except ET.ParseError:
            pass

    react_match = re.search(r"Action:\s*(.*?)\n.*?Action Input:\s*(\{.*?\})", llm_response, re.DOTALL)
    if react_match:
        try:
            name = react_match.group(1).strip()
            args = json.loads(react_match.group(2).strip())
            kwargs_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            return f"result = {name}({kwargs_str})\nprint(result)"
        except json.JSONDecodeError:
            pass

    return None