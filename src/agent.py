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

            # CORRIGÉ : Vérification des limites AVANT d'effectuer la requête LLM
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
            
            # CORRIGÉ : Tronquer l'historique pour éviter la croissance exponentielle (O(n²))
            if len(history) > 8:
                history = history[:2] + history[-6:]
            
            try:
                llm_response = generate_chat_response(
                    messages=history,
                    token_manager=self.token_manager,
                    model=self.model_name,
                    max_retries=20
                )
                total_requests += (llm_response["retries"] + 1)
            except Exception as e:
                error_msg = f"LLM API Error: {str(e)}"
                break

            raw_text = llm_response.get("content") or ""
            
            # CORRIGÉ : Détection stricte des réponses vides (reasoning tokens) pour ne pas casser la boucle
            if not raw_text.strip():
                observation = "Error: LLM returned an empty response. Ensure code blocks are correctly formatted."
                history.append({"role": "user", "content": f"Observation:\n{observation}"})
                continue

            history.append({"role": "assistant", "content": raw_text})

            code = extract_python_code(raw_text)
            
            if not code:
                observation = (
                    "Error: No valid tool call or Python code block found in your response. "
                    "You must output executable Python code inside a ```python block, or use "
                    "the authorized tool call format. Please try again."
                )
                sandbox_output = observation
                code = ""
            else:
                print("[*] Executing generated code...")
                sandbox_result = self.sandbox.execute(code)
                
                if sandbox_result["status"] == "final_answer":
                    success = True
                    final_solution = sandbox_result["data"]
                    sandbox_output = f"Task completed. Solution submitted: {final_solution}"
                    observation = sandbox_output
                elif sandbox_result["status"] == "error":
                    sandbox_output = sandbox_result["data"]
                    observation = f"Execution Error:\n{sandbox_output}\nPlease fix the error and try again."
                else:
                    sandbox_output = sandbox_result["data"]
                    observation = sandbox_output
                    
                    if len(observation) > self.max_observation_length:
                        observation = observation[:self.max_observation_length] + "\n... [Output Truncated]"

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
                retries=llm_response.get("retries", 0)
            )
            steps.append(step_metric)

            if success:
                break
            
                
            history.append({"role": "user", "content": f"Observation:\n{observation}"})
            # CORRIGÉ : Suppression du time.sleep(5) qui grillait le budget timeout bêtement

        if not success and not error_msg:
            error_msg = f"Failed: Max iterations reached ({max_iterations})."
            print(f"[!] {error_msg}")

        total_time = time.time() - start_time
        total_in_tokens = sum(s.input_tokens for s in steps)
        total_out_tokens = sum(s.output_tokens for s in steps)

        if not success:
            if not error_msg:
                error_msg = f"Failed: Max iterations reached ({max_iterations})."
            print(f"[!] {error_msg}")
            
            # On tente de récupérer ce qui a été produit si l'outil existe
            if "get_patch" in self.sandbox.mcp_tools:
                print("[*] Attempting to salvage partial patch...")
                try:
                    fallback_patch = self.sandbox.mcp_tools["get_patch"]()
                    # On s'assure que ce n'est pas un message d'erreur ou vide
                    if fallback_patch and fallback_patch.strip() and "No changes made yet" not in fallback_patch:
                        final_solution = fallback_patch
                        success = True # Optionnel: on le passe à True si on considère qu'avoir un patch est un succès
                        print("[*] Successfully salvaged a partial patch!")
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
            error=error_msg
        )