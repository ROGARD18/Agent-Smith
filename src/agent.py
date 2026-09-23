import time
from typing import List

from src.sandbox import Sandbox
from src.llm import TokenManager, extract_python_code, generate_chat_response
from src.models import StepMetrics, SolutionOutput


class AgentOrchestrator:
    """Central agent loop implementing Thought -> Code -> Observation.

    Calls the LLM, extracts code, feeds it to the sandbox, reads
    observations, and repeats until final_answer() or limits are reached.
    """

    def __init__(self, sandbox: Sandbox,
                 token_manager: TokenManager, model_name: str):
        self.sandbox = sandbox
        self.token_manager = token_manager
        self.model_name = model_name
        self.max_observation_length = 8000

    def run(
        self,
        task_id: str,
        benchmark: str,
        system_prompt: str,
        task_prompt: str,
        max_iterations: int = 30,
        max_input_tokens: int = 300000,
        max_output_tokens: int = 10000,
        max_time_seconds: int = 880,
    ) -> SolutionOutput:
        """Run the agent loop until completion or limits exceeded."""

        start_time = time.time()

        history = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_prompt},
        ]

        steps: List[StepMetrics] = []
        success = False
        final_solution = ""
        error_msg = None
        total_requests = 0

        print(f"[*] Starting agent loop for task {task_id} ({benchmark})")

        for iteration in range(1, max_iterations + 1):
            print(f"\n--- Iteration {iteration}/{max_iterations} ---")

            # Check time budget
            elapsed = time.time() - start_time
            if elapsed > max_time_seconds:
                error_msg = f"Failed: Timeout exceeded ({max_time_seconds}s)."
                print(f"[!] {error_msg}")
                break

            # Check token budgets BEFORE making the request
            current_total_input = sum(s.input_tokens for s in steps)
            current_total_output = sum(s.output_tokens for s in steps)

            if current_total_input > max_input_tokens:
                error_msg = (
                    f"Failed: Max input tokens exceeded "
                    f"({current_total_input}/{max_input_tokens})."
                )
                print(f"[!] {error_msg}")
                break

            if current_total_output > max_output_tokens:
                error_msg = (
                    f"Failed: Max output tokens exceeded "
                    f"({current_total_output}/{max_output_tokens})."
                )
                print(f"[!] {error_msg}")
                break

            # Sliding window history to avoid O(n²) token growth
            # Keep: system prompt + first user message + last N messages
            if len(history) > 12:
                history = history[:2] + history[-10:]

            # Call LLM
            try:
                # Compute remaining output token budget for max_tokens
                remaining_output = max(100,
                                       max_output_tokens -
                                       current_total_output)
                llm_response = generate_chat_response(
                    messages=history,
                    token_manager=self.token_manager,
                    model=self.model_name,
                    max_retries=20,
                    max_tokens=min(1500, remaining_output),
                )
                total_requests += llm_response["retries"] + 1
            except Exception as e:
                error_msg = f"LLM API Error: {str(e)}"
                print(f"[!] {error_msg}")
                # Still count the failed requests
                total_requests += 1
                break

            raw_text = llm_response.get("content") or ""

            # Handle empty responses — still record metrics
            if not raw_text.strip():
                print("[!] LLM returned empty response")
                observation = (
                    "Error: LLM returned an empty response. "
                    "You must output a Thought section followed by a "
                    "```python code block. Please try again."
                )
                step_metric = StepMetrics(
                    step=iteration,
                    input_tokens=llm_response.get("input_tokens", 0),
                    output_tokens=llm_response.get("output_tokens", 0),
                    request_time_ms=llm_response.get("request_time_ms", 0),
                    api_url=llm_response.get("api_url", ""),
                    model_name=self.model_name,
                    llm_output="",
                    sandbox_input="",
                    sandbox_output=observation,
                    retries=llm_response.get("retries", 0),
                )
                steps.append(step_metric)
                history.append({"role": "assistant",
                                "content": "(empty response)"})
                history.append(
                    {"role": "user",
                     "content": f"Observation:\n{observation}"}
                )
                continue

            history.append({"role": "assistant", "content": raw_text})

            code = extract_python_code(raw_text)

            if not code:
                observation = (
                    "Error: No valid Python code block found in your "
                    "response. You MUST output executable Python code "
                    "inside a ```python block. Please structure your "
                    "response as:\n"
                    "Thought: <your reasoning>\n"
                    "Code:\n```python\n<your code>\n```"
                )
                sandbox_output = observation
                code = ""
            else:
                print(f"[*] Executing code ({len(code)} chars)...")
                sandbox_result = self.sandbox.execute(code)

                if sandbox_result["status"] == "final_answer":
                    success = True
                    final_solution = sandbox_result["data"]
                    sandbox_output = "Task completed. Solution submitted."
                    observation = sandbox_output
                elif sandbox_result["status"] == "error":
                    sandbox_output = sandbox_result["data"]
                    observation = (
                        f"Execution Error:\n{sandbox_output}\n"
                        f"Please fix the error and try again."
                    )
                else:
                    sandbox_output = sandbox_result["data"]
                    observation = sandbox_output

                    # Truncate long outputs (keep end for test results)
                    if len(observation) > self.max_observation_length:
                        keep_end = self.max_observation_length * 2 // 3
                        keep_start =\
                            self.max_observation_length - keep_end - 50
                        trunc_len = len(observation) - keep_start - keep_end
                        observation = (
                            observation[:keep_start]
                            + f"\n... [{trunc_len} chars truncated] ...\n"
                            + observation[-keep_end:]
                        )

            step_metric = StepMetrics(
                step=iteration,
                input_tokens=llm_response.get("input_tokens", 0),
                output_tokens=llm_response.get("output_tokens", 0),
                request_time_ms=llm_response.get("request_time_ms", 0),
                api_url=llm_response.get("api_url", ""),
                model_name=self.model_name,
                llm_output=raw_text,
                sandbox_input=code,
                sandbox_output=sandbox_output,
                retries=llm_response.get("retries", 0),
            )
            steps.append(step_metric)

            if success:
                break

            history.append({"role": "user",
                            "content": f"Observation:\n{observation}"})

        if not success and not error_msg:
            error_msg = f"Failed: Max iterations reached ({max_iterations})."
            print(f"[!] {error_msg}")

        total_time = time.time() - start_time
        total_in_tokens = sum(s.input_tokens for s in steps)
        total_out_tokens = sum(s.output_tokens for s in steps)

        # Attempt to salvage partial patch for SWE-bench (don't claim success)
        if not success and "get_patch" in self.sandbox.mcp_tools:
            print("[*] Attempting to salvage partial patch...")
            try:
                fallback_patch = self.sandbox.mcp_tools["get_patch"]()
                if (
                    fallback_patch
                    and fallback_patch.strip()
                    and "No changes made yet" not in fallback_patch
                ):
                    final_solution = fallback_patch
                    print("[*] Salvaged a partial patch "
                          "(not claiming success)")
            except Exception as e:
                print(f"[!] Failed to salvage patch: {e}")

        total_time = time.time() - start_time

        return SolutionOutput(
            task_id=task_id,
            benchmark=benchmark,
            success=success,
            solution=final_solution,
            iterations=len(steps),
            total_requests=total_requests,
            total_input_tokens=total_in_tokens,
            total_output_tokens=total_out_tokens,
            total_time_seconds=total_time,
            steps=steps,
            system_prompt=system_prompt,
            error=error_msg,
        )
