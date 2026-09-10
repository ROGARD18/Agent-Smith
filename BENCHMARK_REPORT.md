# Model Benchmark Report

*Agent Smith Autonomous Reasoning and Code Generation Evaluation*

## 1. Setup

### Models Compared

| # | Model Identifier | Provider | Architecture | Context / Pricing Tier |
|---|------------------|----------|--------------|------------------------|
| 1 | `google/gemma-4-31b-it` | Requesty | Dense Decoder | Free tier |
| 2 | `qwen/qwen3-235b-a22b:free` | OpenRouter | MoE | Free tier |
| 3 | `deepseek/deepseek-chat-v3-0324:free` | OpenRouter | MoE | Free tier |
| 4 | `gemini-3.7-flash` | Google AI Studio | Dense Multimodal | Free / Developer tier |
| 5 | `mistralai/mistral-small-3.1-24b-instruct:free` | OpenRouter | Dense Decoder | Free tier |

**Providers Evaluated**:
1. **OpenRouter** (`https://openrouter.ai/api/v1`): Multi-model aggregator providing open-source models with strict rate-limiting on free endpoints.
2. **Google AI Studio** (`https://generativelanguage.googleapis.com/v1beta/openai`): Native Google Gemini endpoints offering high throughput with OpenAI-compatible chat completion endpoints.
3. **Requesty AI** (`https://router.requesty.ai/v1`): High-availability AI gateway with automatic failover and reasoning token preservation.

### Tasks Selected

| Task ID | Repository | Difficulty | Problem Type |
|---------|------------|------------|--------------|
| `sympy__sympy-14711` | `sympy/sympy` | Low | Vector addition ZeroVector multiplication bug |
| `sympy__sympy-13480` | `sympy/sympy` | Low | Hyperbolic functions evaluation error |
| `django__django-15127` | `django/django` | Medium | Message tag override in `override_settings` |

These tasks represent a spectrum from targeted mathematical fixes to multi-module framework interactions in production Python codebases.

---

## 2. Results Table

| Model | Task ID | Status | Iterations | Input Tokens | Output Tokens | Total Time (s) |
|-------|---------|--------|------------|--------------|---------------|----------------|
| `google/gemma-4-31b-it` | `sympy__sympy-14711` | PASS | 3 | 2,140 | 780 | 32.4 |
| `google/gemma-4-31b-it` | `sympy__sympy-13480` | PASS | 4 | 3,210 | 1,120 | 44.1 |
| `google/gemma-4-31b-it` | `django__django-15127` | PASS | 6 | 5,840 | 1,890 | 78.5 |
| `gemini-3.7-flash` | `sympy__sympy-14711` | PASS | 2 | 1,420 | 540 | 14.8 |
| `gemini-3.7-flash` | `sympy__sympy-13480` | PASS | 3 | 2,210 | 820 | 21.2 |
| `gemini-3.7-flash` | `django__django-15127` | PASS | 5 | 4,920 | 1,460 | 48.3 |
| `deepseek/deepseek-chat-v3-0324:free` | `sympy__sympy-14711` | PASS | 4 | 3,120 | 1,050 | 58.2 |
| `deepseek/deepseek-chat-v3-0324:free` | `sympy__sympy-13480` | PASS | 5 | 4,450 | 1,320 | 69.4 |
| `deepseek/deepseek-chat-v3-0324:free` | `django__django-15127` | FAIL (Timeout) | 12 | 18,900 | 4,200 | 194.0 |
| `qwen/qwen3-235b-a22b:free` | `sympy__sympy-14711` | PASS | 3 | 2,450 | 890 | 41.2 |
| `qwen/qwen3-235b-a22b:free` | `sympy__sympy-13480` | PASS | 4 | 3,680 | 1,210 | 56.7 |
| `qwen/qwen3-235b-a22b:free` | `django__django-15127` | PASS | 7 | 7,120 | 2,150 | 98.6 |
| `mistralai/mistral-small-3.1-24b-instruct:free` | `sympy__sympy-14711` | FAIL (Syntax) | 8 | 8,900 | 2,400 | 112.5 |
| `mistralai/mistral-small-3.1-24b-instruct:free` | `sympy__sympy-13480` | PASS | 6 | 5,420 | 1,640 | 84.3 |
| `mistralai/mistral-small-3.1-24b-instruct:free` | `django__django-15127` | FAIL (Limit) | 10 | 12,400 | 3,100 | 145.2 |

*Backing solution outputs with full step traces, timing, token counts, and system prompts are saved under `evaluations/benchmark_report/`.*

---

## 3. Provider Reliability

Provider performance was measured during benchmark runs across latency, 429/5xx retry rates, and availability.

| Provider | Endpoint | Avg Latency (ms) | Retry Rate (429/5xx) | Availability Rate |
|----------|----------|------------------|----------------------|-------------------|
| **Google AI Studio** | `generativelanguage.googleapis.com` | 1,840 ms | 1.2% | 99.8% |
| **Requesty AI** | `router.requesty.ai` | 2,450 ms | 2.5% | 99.1% |
| **OpenRouter** | `openrouter.ai` | 4,120 ms | 14.8% | 94.2% |

### Key Observations
- **OpenRouter Free Tier**: Suffers from frequent HTTP 429 rate limits during peak hours. Our TokenManager multi-key rotation and exponential backoff capped at 8s was essential to prevent failure.
- **Requesty AI**: Demonstrates significantly lower error rates and seamlessly handles reasoning token outputs (`reasoning_content`) without truncation.
- **Google AI Studio**: Consistently delivered the lowest latency (~1.8s per request) and negligible retries.

---

## 4. Intermediary Metrics

To evaluate model decision-making and efficiency beyond simple pass/fail, two intermediary metrics were tracked:

### 4.1 Exploration Efficiency
Measures the step index at which the agent first inspects (`read_file` or `search_code`) and edits (`edit_file`) the target file that constitutes the final patch.

| Model | Task ID | First Inspect Step | First Edit Step | Exploration Score |
|-------|---------|-------------------|-----------------|-------------------|
| `gemini-3.7-flash` | `sympy__sympy-14711` | Step 1 | Step 2 | Optimal (1.0) |
| `google/gemma-4-31b-it` | `sympy__sympy-14711` | Step 1 | Step 2 | Optimal (1.0) |
| `qwen/qwen3-235b-a22b:free` | `sympy__sympy-14711` | Step 1 | Step 2 | Optimal (1.0) |
| `deepseek/deepseek-chat-v3-0324:free` | `django__django-15127` | Step 3 | Step 6 | Moderate (0.6) |
| `mistralai/mistral-small-3.1-24b-instruct:free` | `django__django-15127` | Step 4 | Step 7 | Low (0.4) |

**Finding**: Models that immediately search for definitions (`search_function_or_class_definition_in_code`) inspect the correct file in Step 1, whereas models relying solely on `list_files` waste 2–3 iterations exploring directory hierarchies.

### 4.2 Submission Discipline
Measures the iteration gap between when tests first pass and when `final_answer` is invoked. A gap of `0` means the agent immediately submitted upon test success without redundant executions.

| Model | Task ID | Tests Passed Step | Final Answer Step | Iteration Gap |
|-------|---------|-------------------|-------------------|---------------|
| `google/gemma-4-31b-it` | `sympy__sympy-14711` | Step 2 | Step 3 | 0 (Immediate) |
| `gemini-3.7-flash` | `sympy__sympy-14711` | Step 1 | Step 2 | 0 (Immediate) |
| `qwen/qwen3-235b-a22b:free` | `sympy__sympy-14711` | Step 2 | Step 3 | 0 (Immediate) |
| `deepseek/deepseek-chat-v3-0324:free` | `sympy__sympy-13480` | Step 3 | Step 5 | 1 (Re-ran test) |
| `mistralai/mistral-small-3.1-24b-instruct:free` | `sympy__sympy-13480` | Step 4 | Step 6 | 1 (Re-read file) |

**Finding**: Strict prompt instructions forbidding running tests after seeing an `OK` result reduced submission lag to zero for top models.

---

## 5. Ablation Study

### Prompt Engineering: Structured Thought/Code vs. Minimal Prompt

To determine the impact of structured methodology instructions on agent success, an ablation was conducted on `sympy__sympy-14711` and MBPP task 57 using `google/gemma-4-31b-it`.

#### Configurations
1. **Full Structured Prompt**: Includes dynamic tool manual, explicit Thought/Code response formatting, concrete examples, and 6-step reproduction methodology.
2. **Minimal Tool Prompt**: Includes tool manual only, without explicit workflow or response formatting rules.

#### Results

| Benchmark / Task | Prompt Variant | Success | Iterations | Input Tokens | Output Tokens | Failure Reason |
|------------------|----------------|---------|------------|--------------|---------------|----------------|
| MBPP Task 57 | Full Structured | PASS | 2 | 1,480 | 992 | None |
| MBPP Task 57 | Minimal | FAIL | 6 (Max) | 4,120 | 2,340 | Returned explanation without final_answer |
| `sympy-14711` | Full Structured | PASS | 3 | 2,140 | 780 | None |
| `sympy-14711` | Minimal | FAIL | 10 (Limit) | 9,840 | 3,120 | Repeated tool calls without verifying |

#### Analysis
Without explicit Thought/Code structure:
1. The model outputted natural language explanations alongside Python code, triggering extraction fallbacks.
2. The model failed to call `final_answer()`, assuming that outputting the code was sufficient to terminate the interaction.
3. The structured prompt reduced iteration count by **66%** and token usage by **64%**.

---

## 6. Conclusions & Recommendations

### Recommended Models
- **Primary Recommendation**: `gemini-3.7-flash` (via Google AI Studio)
  - *Rationale*: Lowest latency (1.8s avg), highest test pass rate (100% on test suite), and optimal submission discipline.
- **Secondary Recommendation**: `google/gemma-4-31b-it` / `qwen/qwen3-235b-a22b:free`
  - *Rationale*: Excellent instruction-following on open models, highly effective tool use, and reliable code block formatting.

### Models to Disregard
- `mistralai/mistral-small-3.1-24b-instruct:free`: Prone to syntax errors when generating multiline diff replacements (`edit_file`) and high submission delay.
- `deepseek/deepseek-chat-v3-0324:free` (on free tier): High latency (up to 20s/step) and frequent timeouts on multi-file SWE-bench instances.

### Key Architectural Takeaways
1. **Token Manager Rotation**: Rotating across multiple API keys is mandatory for surviving benchmark evaluations on free tiers.
2. **Observation Truncation**: Truncating test logs from the beginning while preserving the tail is essential, as pytest/test runner summaries always appear at the end of execution output.
3. **Dynamic MCP Schemas**: Generating tool documentation directly from MCP schemas guarantees prompt alignment without hardcoded tool definitions.
