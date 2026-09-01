import os
from src.llm import TokenManager, generate_chat_response, extract_python_code
from src.sandbox import Sandbox, SandboxConfig
from dotenv import load_dotenv

def main():
    load_dotenv()
    # Fallback for testing if the environment variable is not set locally
    if not any(k.startswith("OPENROUTER_API_KEY") for k in os.environ):
        os.environ["OPENROUTER_API_KEY_1"] = "dummy_key_for_testing"

    tools_documentation = "{}"
    system_prompt = (
        "You are an autonomous software coder agent.\n"
        "Your goal is to solve the given problem by writing and executing Python code.\n"
        f"You have access to the following tools: {tools_documentation}\n"
        "You must use the `final_answer(solution)` function to submit your final solution.\n"
        "CRITICAL INSTRUCTION:\n"
        "You MUST structure EVERY response exactly like the example below.\n"
        "You must ALWAYS output a 'Thought:' section explaining your logic "
        "before writing the 'Code:' block.\n\n"
        "--- EXAMPLE OF INTERACTION ---\n"
        "Task: Write a function that returns the square of a number.\n"
        "Thought: I need to write a simple function that multiplies a number by itself, "
        "then test it to make sure it works before submitting the final answer.\n"
        "Code:\n"
        "```python\n"
        "def square(x):\n"
        "    return x * x\n"
        "final_answer(square(4))\n"
        "```\n"
        "--- END OF EXAMPLE ---\n"
        "Begin!"
    )

    query = (
        "Write a function that returns the sum of a two number. "
        "And call this function in the file without __name__ == '__main__'"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query}
    ]

    try:
        token_manager = TokenManager(provider_prefix="OPENROUTER_API_KEY")
    except ValueError as e:
        print(f"Error initializing TokenManager: {e}")
        return
        
    print("[*] Calling LLM...")
    try:
        llm_output = generate_chat_response(messages, token_manager)
    except Exception as e:
        print(f"Error calling LLM API: {e}")
        return

    raw_response = llm_output["content"]
    print("\n--- LLM Output ---")
    print(raw_response)

    code_string = extract_python_code(raw_response)
    if not code_string:
        print("\nFailed to extract Python code from the response.")
        return

    print("\n--- Extracted Code ---")
    print(code_string)

    config = SandboxConfig()
    sandbox = Sandbox(config=config)
    
    print("\n--- Sandbox Execution ---")
    result = sandbox.execute(code_string)
    print(result)

if __name__ == "__main__":
    main()