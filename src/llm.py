import os
import re
import json
import time
import requests  # type: ignore[import-untyped]
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List


class TokenManager:
    """Manages rotation of multiple API keys and providers.

    Supports multiple API keys per provider (for rate-limit rotation)
    and multiple provider URLs (for fallback).
    Keys are discovered from environment variables matching the given prefix.
    """

    def __init__(self, api_url: str = "https://openrouter.ai/api/v1",
                 provider_prefix: Optional[str] = None):
        self.api_url = api_url.rstrip('/')
        self.keys: List[str] = []

        # Determine prefixes to search
        prefixes = []
        if provider_prefix:
            prefixes.append(provider_prefix)
        else:
            # Auto-detect from api_url
            url_lower = self.api_url.lower()
            if "requesty" in url_lower:
                prefixes.extend(["REQUESTY_API_KEY", "OPENROUTER_API_KEY"])
            elif "googleapis" in url_lower or "gemini" in url_lower:
                prefixes.extend(["GEMINI_API_KEY", "GOOGLE_API_KEY",
                                 "OPENROUTER_API_KEY"])
            elif "openai" in url_lower:
                prefixes.extend(["OPENAI_API_KEY", "OPENROUTER_API_KEY"])
            else:
                prefixes.append("OPENROUTER_API_KEY")
            # Always allow general prefixes as fallback
            prefixes.extend(["OPENROUTER_API_KEY", "API_KEY", "LLM_API_KEY"])

        # Scan environment for keys matching any candidate prefixes
        seen_keys = set()
        for prefix in prefixes:
            for key, value in sorted(os.environ.items()):
                if key.startswith(prefix) and value and value not in seen_keys:
                    self.keys.append(value)
                    seen_keys.add(value)

        # Fallback: find ANY environment variable containing API_KEY
        if not self.keys:
            for key, value in sorted(os.environ.items()):
                if "API_KEY" in key and value and value not in seen_keys:
                    self.keys.append(value)
                    seen_keys.add(value)

        if not self.keys:
            raise ValueError(
                f"No API keys found for '{self.api_url}'. "
                f"Set environment variables like OPENROUTER_API_KEY, "
                f"REQUESTY_API_KEY, or GEMINI_API_KEY."
            )

        self.provider_prefix = prefixes[0] if prefixes else "API_KEY"

        self.current_index = 0
        print(f"[*] TokenManager: {len(self.keys)} key(s) loaded "
              f"for {provider_prefix} at {self.api_url}")

    def get_current_key(self) -> str:
        return self.keys[self.current_index]

    def rotate_key(self) -> None:
        """Moves to the next key in the pool."""
        self.current_index = (self.current_index + 1) % len(self.keys)
        print(f"[*] Switching to API key index: {self.current_index}")


def generate_chat_response(
    messages: List[Dict[str, str]],
    token_manager: TokenManager,
    model: str,
    max_retries: int = 20,
    max_tokens: int = 1500,
    stop_sequences: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Sends a full chat history to the LLM API with token rotation,
    exponential backoff (capped), and safe extraction."""

    if stop_sequences is None:
        stop_sequences = ["Observation:", "<end_code>"]

    api_url = token_manager.api_url
    start_time = time.perf_counter()
    retries_used = 0

    for attempt in range(max_retries):
        api_key = token_manager.get_current_key()

        try:
            response = requests.post(
                url=f"{api_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "stop": stop_sequences
                },
                timeout=120
            )

            # 402: Payment Required — do NOT retry, fail immediately
            if response.status_code == 402:
                raise Exception(
                    f"HTTP 402 Payment Required. Ensure your model ends "
                    f"with ':free' or your account has credits. "
                    f"Details: {response.text}"
                )

            # 429: Rate limited — rotate key and retry
            if response.status_code == 429:
                sleep_time = min(2 ** attempt, 8)
                print(
                    f"Attempt {attempt + 1}: Rate limited (HTTP 429). "
                    f"Rotating key and waiting {sleep_time}s...")
                token_manager.rotate_key()
                time.sleep(sleep_time)
                retries_used += 1
                continue

            # 5xx: Server errors — retry with capped backoff
            if response.status_code in [500, 502, 503, 504]:
                sleep_time = min(2 ** attempt, 8)
                print(
                    f"[!] API Error {response.status_code}. Server "
                    f"unavailable. Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
                retries_used += 1
                continue

            if response.status_code != 200:
                print(f"\n[!] API Error Details: {response.text}\n")
                response.raise_for_status()

            data = response.json()

            # Handle OpenRouter error-in-200 responses
            if "error" in data and data["error"]:
                error_msg = data["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                print(f"[!] API returned error in 200: {error_msg}")
                sleep_time = min(2 ** attempt, 8)
                time.sleep(sleep_time)
                retries_used += 1
                continue

            usage = data.get('usage', {})

            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError) as e:
                raise Exception(
                    f"Unexpected API response structure: {data}") from e

            raw_text = message.get("content") or ""
            if not raw_text and message.get("reasoning_content"):
                raw_text = message.get("reasoning_content") or ""
            if not raw_text and "tool_calls" in message:
                raw_text = str(message["tool_calls"])

            request_time_ms = (time.perf_counter() - start_time) * 1000

            return {
                "content": raw_text,
                "input_tokens": usage.get('prompt_tokens', 0),
                "output_tokens": usage.get('completion_tokens', 0),
                "request_time_ms": request_time_ms,
                "api_url": api_url,
                "model_name": model,
                "retries": retries_used
            }

        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            sleep_time = min(2 ** attempt, 8)
            print(f"[!] Network error: {e}. Retrying in {sleep_time}s...")
            time.sleep(sleep_time)
            retries_used += 1

    raise Exception(
        "Max retries exceeded across all available API keys "
        "or due to persistent network issues.")


def extract_python_code(llm_response: str) -> Optional[str]:
    """
    Extracts Python code or translates non-Python tool formats
    (JSON, XML, ReAct) into executable Python code for the sandbox.
    """
    if not llm_response:
        return None

    # 1. Primary Format: Standard Python markdown block (closed)
    python_match = re.search(r"```python\s*(.*?)\s*```",
                             llm_response, re.DOTALL | re.IGNORECASE)
    if python_match:
        return python_match.group(1).strip()

    # 2. Fallback: Unclosed Python markdown block
    python_unclosed_match = re.search(
        r"```python\s*(.*)", llm_response, re.DOTALL | re.IGNORECASE)
    if python_unclosed_match:
        code = python_unclosed_match.group(1).strip()
        if code:
            return code

    # 3. Generic markdown block without 'python' specified
    generic_match = re.search(r"```(.*?)```", llm_response, re.DOTALL)
    if generic_match:
        code = generic_match.group(1).strip()
        if code.lower().startswith("python"):
            code = code[6:].strip()
        if code:
            return code

    # JSON Object format: {"Thought": "...", "Code": "print(...)"}
    if '"Code"' in llm_response or '"code"' in llm_response:
        try:
            code_json_match = re.search(
                r'"[Cc]ode"\s*:\s*"((?:[^"\\]|\\.)*)"', llm_response)
            if code_json_match:
                extracted = json.loads(f'"{code_json_match.group(1)}"')
                if extracted and extracted.strip():
                    return str(extracted).strip()
        except Exception:
            pass

    # JSON / Hermes Format: <tool_call>{...}</tool_call>
    json_match = re.search(
        r"<tool_call>(.*?)</tool_call>", llm_response, re.DOTALL)
    if json_match:
        try:
            tool_data = json.loads(json_match.group(1).strip())
            name = tool_data.get("name")
            args = tool_data.get("arguments", {})
            kwargs_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            return f"result = {name}({kwargs_str})\nprint(result)"
        except json.JSONDecodeError:
            pass

    # XML Format (Anthropic style): <invoke name="func">...</invoke>
    xml_match = re.search(
        r"<invoke\s+name=[\"'](.*?)[\"']>(.*?)</invoke>",
        llm_response, re.DOTALL)
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

    # ReAct Format: Action: func \n Action Input: {"a": 1}
    react_pattern = r"Action:\s*(.*?)\n.*?Action Input:\s*(\{.*?\})"
    react_match = re.search(react_pattern, llm_response, re.DOTALL)
    if react_match:
        try:
            name = react_match.group(1).strip()
            args = json.loads(react_match.group(2).strip())
            kwargs_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            return f"result = {name}({kwargs_str})\nprint(result)"
        except json.JSONDecodeError:
            pass

    # 4. EXTREME FALLBACK: Raw tool call without any markdown formatting
    tool_prefixes = (
        'print(', 'run_tests(', 'run_command(', 'read_file(',
        'edit_file(', 'list_files(', 'search_code(',
        'search_function_or_class_definition_in_code(', 'find_references(',
        'get_patch(', 'final_answer(', 'check_syntax(',
        'result =', 'result=',
    )
    if any(prefix in llm_response for prefix in tool_prefixes[:6]):
        lines = llm_response.split('\n')
        code_lines = [
            line for line in lines
            if line.strip().startswith(tool_prefixes)
        ]
        if code_lines:
            return "\n".join(code_lines)

    return None
