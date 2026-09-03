import time
from datetime import datetime
from typing import List, Dict, Any, Optional

from src.sandbox import Sandbox
from src.llm import TokenManager, extract_python_code, generate_chat_response
from src.models import StepMetrics, SolutionOutput

class AgentOrchestrator:
    def __init__(self, sandbox: Sandbox, token_manager: TokenManager, model_name: str):
        self.sandbox = sandbox
        self.token_manager = token_manager
        self.model_name = model_name
        # Max length for tool output to prevent token explosion
        self.max_observation_length = 5000 

    def run(
        self, 
        task_id: str, 
        benchmark: str, 
        system_prompt: str, 
        task_prompt: str, 
        max_iterations: int = 30,
        max_input_tokens: int = 300000,
        max_output_tokens: int = 10000,
        max_time_seconds: int = 880
    ) -> SolutionOutput:
        """Executes the autonomous Thought -> Code -> Observation loop."""
        
        start_time = time.time()
        
        history = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_prompt}
        ]
        
        steps: List[StepMetrics] = []
        success = False
        final_solution = ""
        error_msg = None
        total_requests = 0

        print(f"[*] Starting agent loop for task {task_id} ({benchmark})")

        for iteration in range(1, max_iterations + 1):
            print(f"\n--- Iteration {iteration}/{max_iterations} ---")
            
            if time.time() - start_time > max_time_seconds:
                error_msg = f"Failed: Timeout exceeded ({max_time_seconds}s)."
                print(f"[!] {error_msg}")
                break
            
            try:
                llm_response = generate_chat_response(
                    messages=history,
                    token_manager=self.token_manager,
                    model=self.model_name
                )
                total_requests += (llm_response["retries"] + 1)
            except Exception as e:
                error_msg = f"LLM API Error: {str(e)}"
                break

            raw_text = llm_response["content"]
            history.append({"role": "assistant", "content": raw_text})

            # Extract Code
            code = extract_python_code(raw_text)
            
            if not code:
                # Explicit feedback: No valid code block found
                observation = (
                    "Error: No valid tool call or Python code block found in your response. "
                    "You must output executable Python code inside a ```python block, or use "
                    "the authorized tool call format. Please try again."
                )
                sandbox_output = observation
                code = ""
            else:
                # Execute Code in Sandbox
                print("[*] Executing generated code...")
                sandbox_result = self.sandbox.execute(code)
                
                if sandbox_result["status"] == "final_answer":
                    success = True
                    final_solution = sandbox_result["data"]
                    sandbox_output = f"Task completed. Solution submitted: {final_solution}"
                    observation = sandbox_output
                elif sandbox_result["status"] == "error":
                    # Explicit feedback: Syntax error or timeout
                    sandbox_output = sandbox_result["data"]
                    observation = f"Execution Error:\n{sandbox_output}\nPlease fix the error and try again."
                else:
                    # Standard observation
                    sandbox_output = sandbox_result["data"]
                    observation = sandbox_output
                    
                    # Explicit feedback: Tool output truncated
                    if len(observation) > self.max_observation_length:
                        observation = observation[:self.max_observation_length] + "\n... [Output Truncated]"

            # Record Step Metrics
            step_metric = StepMetrics(
                step=iteration,
                input_tokens=llm_response["input_tokens"],
                output_tokens=llm_response["output_tokens"],
                request_time_ms=llm_response["request_time_ms"],
                api_url=llm_response["api_url"],
                model_name=self.model_name,
                llm_output=raw_text,
                sandbox_input=code,
                sandbox_output=sandbox_output,
                retries=llm_response["retries"]
            )
            steps.append(step_metric)

            if success:
                break
                
            current_total_input = sum(s.input_tokens for s in steps)
            current_total_output = sum(s.output_tokens for s in steps)
            
            if current_total_input > max_input_tokens:
                error_msg = f"Failed: Max input tokens exceeded ({max_input_tokens})."
                print(f"[!] {error_msg}")
                break
                
            if current_total_output > max_output_tokens:
                error_msg = f"Failed: Max output tokens exceeded ({max_output_tokens})."
                print(f"[!] {error_msg}")
                break
                
            # Feed the observation back to the LLM for the next thought cycle
            history.append({"role": "user", "content": f"Observation:\n{observation}"})

        if not success and not error_msg:
            error_msg = f"Failed: Max iterations reached ({max_iterations})."
            print(f"[!] {error_msg}")

        # Compile total metrics
        total_time = time.time() - start_time
        total_in_tokens = sum(s.input_tokens for s in steps)
        total_out_tokens = sum(s.output_tokens for s in steps)

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
            error=error_msg
        )