from src.llm import generate_response, extract_python_code
from src.sandbox import Sandbox, SandboxConfig


def main():

    tools_documentation = {}
    prompt = (
        "You are an autonomous software coder agent."
        "Your goal is to solve the given problem by writing and executing "
        "Python code. You have access to the following tools:"
        f"{tools_documentation}"
        "You must use the `final_answer(solution)` function to submit your "
        "final solution. CRITICAL INSTRUCTION:"
        "You MUST structure EVERY response exactly like the example below."
        " You must ALWAYS output a 'Thought:' section explaining your logic """
        "before writing the 'Code:' block."
        "--- EXAMPLE OF INTERACTION ---"
        "Task: Write a function that returns the square of a number."
        "Thought: I need to write a simple function that multiplies a"
        " number by itself, then test it to make sure it works before"
        " submitting the final answer."
        "Code:"
        "```python"
        "def square(x): return x * x"
        "print(square(4))"
        "Observation: 16"
        "Thought: The function works correctly. I will now submit "
        "the final answer."
        "Code: Python final_answer(square)"
        "--- END OF EXAMPLE ---"
        "Begin!"
    )

    query = ("Write a function that returns the sum of a two number."
             "And call this function in the file without "
             "__name__ == '__main__'")
    code = generate_response(prompt + query)
    code_string = extract_python_code(code)
    print(code)
    config = SandboxConfig()
    sandbox = Sandbox(config=config)
    print(sandbox.execute(code_string))


if __name__ == "__main__":
    main()
