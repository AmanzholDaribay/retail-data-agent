# Report and multi-step questions: parallel or sequential SQL?

Research note for step 5 of [the plan](../plan.md). Written 2026-10-31.

**Question.** When a user asks for a report ("create a report for Q1 with insights and action items for Q2") or a multi-part question ("why are users in state X underspending, and how do they compare with state Y?"), should the agent send several SQL queries at once, or one at a time? The setting is a budget of 5 LLM calls per question, Gemini on a free AI Studio key, LangChain `create_agent`, and one `run_sql` tool.

**Sources.** Primary sources only: official docs, installed library source code, specs, first-party engineering posts and the original papers. Code references are relative to `.venv/lib/python3.12/site-packages/`. The installed versions are langchain 1.4.7, langgraph 1.2.18, langchain-core 1.7.3, langchain-google-genai 4.4.2 (the latest on PyPI as of 2026-10-31), google-genai 2.34.0 and google-cloud-bigquery 3.47.0. Some claims come from experiments I ran on 2026-10-31; those are marked **Observed**.

**Words used in this note**

- **LLM call**: one request to the model and one reply. The budget counts these.
- **Tool call**: the model asks to run `run_sql` with some SQL. One reply can hold several tool calls.
- **Parallel tool calls**: several tool calls in one model reply. The code can run them at the same time.
- **Round**: one model reply with tool calls, plus running those tools.
- **Sequential**: one query per round, so each query can use the result of the one before.
- **CTE (common table expression)**: a named sub-query in a `WITH` clause. "One big CTE query" means one SQL statement that computes everything the report needs.
- **Superstep**: one step of the LangGraph engine. All tasks in a superstep run together.
- **RPM / TPM / RPD**: requests per minute, tokens per minute and requests per day, the units of Gemini's rate limits.
- **Dry run**: BigQuery checks a query and estimates its bytes without running it.

---

## 1. Recommendation for this project

**Pattern: "batch first, follow up only if needed".** Keep the normal `create_agent` loop. Through the prompt, make the first round a parallel batch. In call 1 the model works out every number the report needs and sends all the independent queries together, as several `run_sql` calls in one reply (usually 2–6). The tools run at the same time. In call 2 the model writes the report, or, only if a result changes what it must ask (a drill-down, an empty result, a failed query), it sends one more batch. Call 3 then writes the report. Calls 4–5 stay in reserve for fixing errors. The last allowed call is forced to answer without tools, so the user always gets a report and never a "limit reached" message. A typical report then costs 2–3 LLM calls, not 5 or more.

**Why this pattern.** Gemini supports parallel function calls on the Flash models we use ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)). LangChain's `create_agent` already runs all tool calls from one reply concurrently (**Observed**: 3 calls of 1.0 s, 0.2 s and 0.6 s finished in 1.01 s in total; see [Q4](#q4-langchain--langgraph-specifics)). A batch is one Gemini request no matter how many queries it holds. Each extra round sends the whole conversation again, and Gemini's TPM limit counts input tokens ([Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)). The research agrees. Planning tool calls up front and running them in parallel beat the step-by-step ReAct loop on latency and cost, with equal or better accuracy (LLMCompiler: up to 3.7x faster, 6.7x cheaper, ~9% more accurate ([Kim et al. 2024](https://arxiv.org/abs/2312.04511)); ReWOO: 5x fewer tokens, +4% accuracy on HotpotQA ([Xu et al. 2023](https://arxiv.org/abs/2305.18323))). The same paper shows that plain native parallel function calling gets almost the same accuracy as LLMCompiler; LLMCompiler is at most about 35% faster ([LLMCompiler, Sec. 5](https://arxiv.org/html/2312.04511)). So we get most of the benefit from a prompt change and don't need a planner of our own.

**What to put in the system prompt** (add to `prompts/system.md`; wording to tune in evals):

```text
Reports and multi-part questions:
- Before you call a tool, work out every number the answer needs. Do not write this plan as text before the tool calls.
- Write one small query per number or table. Compare groups inside one query (GROUP BY, or WHERE state IN (...)), not one query per group.
- Send all queries that do not depend on each other in the same reply, as separate run_sql calls (usually 2 to 6).
- Send a query in a later reply only if it needs a result you don't have yet, or to fix a query that failed.
- Use at most 2 rounds of queries. Then write the answer with what you have, and say what is missing.
- If a result starts with "Error:", fix only that query. Do not run the successful queries again.
- For a named period such as "Q1 2026", use fixed dates ('2026-01-01' to '2026-04-01').
- Report format: Summary (2-3 lines). Findings (numbers, with period, filters and revenue definition). Insights (what the findings mean, each tied to a number). Action items for the next period (specific, each tied to a finding). Caveats (failed or missing data).
```

Notes on these lines:
- "Only batch calls that don't depend on each other" comes from Anthropic's parallel tool docs ([Anthropic parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)).
- "Prefer several specific calls over one complex call" is Google ADK's advice for parallel tools ([ADK tool performance](https://raw.githubusercontent.com/google/adk-docs/refs/heads/main/docs/tools-custom/performance.md)).
- "Don't write the plan as text first" matters on Gemini 3.x. Requiring text right before a tool call can cause a `Malformed_Function_Call` error ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)). The model can plan in its built-in thinking instead (Gemini 3.8 Flash thinks at "medium" by default ([Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking))).
- "Fixed dates" helps caching: BigQuery doesn't cache queries that use `CURRENT_DATE` ([BigQuery cached results](https://docs.cloud.google.com/bigquery/docs/cached-results)). Keep `CURRENT_DATE()` for relative periods such as "last month".
- Also add one line to the `run_sql` docstring, as ADK suggests ([ADK tool performance](https://raw.githubusercontent.com/google/adk-docs/refs/heads/main/docs/tools-custom/performance.md)): "Call it several times in one reply for independent queries."

**A failing query among parallel ones.** *Update, same day: ticket 03 (commit `850ff09`) now makes `run_sql` return `Error: <message>` for rejected SQL, so a bad query no longer kills the batch. Infrastructure errors (outage, rate limit, access, quota) still raise and end the turn, on purpose. Its `CallBudget` ends the turn with a fixed message, so the "answer on the last call" idea under Budget guards still applies.* What follows describes the code before that commit. Before ticket 03, `run_sql` let gateway exceptions escape. By default, `create_agent`'s ToolNode turns only argument errors into messages and re-raises everything else (`langgraph/prebuilt/tool_node.py:391-399`, `:698-702`; `langchain/agents/factory.py:1140-1148`). **Observed**: when one of three parallel calls raised, the whole `invoke` failed and the two good results were thrown away. So one bad query kills the whole report. The fix: `run_sql` returns `Error: <short message>` instead of raising. This is already the plan's tool contract (step 1, decision 8, ticket 03). `ToolErrorMiddleware` is an alternative ([LangChain built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in); `langchain/agents/middleware/tool_error.py:37-80`). **Observed**: with error-as-result, the model got two results plus one error message and could carry on. Return the exception type and a short message, not a stack trace; both LangChain and Anthropic advise this ([LangChain built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in); [Anthropic, writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)). Also say "0 rows" plainly for an empty result. In the next round the model fixes only the failed query.

**Budget guards** (middleware, no new graph):

```python
@wrap_model_call
def answer_on_last_call(request, handler):
    if llm_calls_this_turn(request.state["messages"]) >= MAX_LLM_CALLS - 1:
        request = request.override(tool_choice="none")   # Gemini maps "none" to mode NONE
    return handler(request)

middleware = [
    answer_on_last_call,
    ToolCallLimitMiddleware(tool_name="run_sql", run_limit=10, exit_behavior="continue"),  # no runaway batches
    ModelCallLimitMiddleware(run_limit=MAX_LLM_CALLS),  # backstop only
]
```

**Observed**: the override reaches the model's `bind_tools` as `tool_choice="none"` on the last call, and the run ends with an answer. On Gemini, `"none"` becomes function-calling mode `NONE` (`langchain_google_genai/_function_utils.py:737-738`). Don't rely on `ModelCallLimitMiddleware` alone. When the limit is hit with `exit_behavior="end"`, it ends the run with a canned "limit exceeded" AI message instead of an answer (`langchain/agents/middleware/model_call_limit.py:165-208`). With `exit_behavior="continue"`, `ToolCallLimitMiddleware` blocks extra calls with error messages and lets the model carry on (`langchain/agents/middleware/tool_call_limit.py:23-33`). Optionally pass `config={"max_concurrency": 4}` to cap threads ([LangGraph use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api); `langchain_core/runnables/config.py:660-675`).

**When to go sequential (the second round).** Use a follow-up round when the next query needs a value from a result:
- A "why" question that needs a first look before choosing the drill-down. Example: find the weakest category in state X, then look at that category's prices and returns.
- A lookup of real values before filtering (how a state name is spelled, which status values exist).
- Fixing a failed or empty query.

ReAct's strength is exactly this: it can update its plan from what it sees ([Yao et al. 2023](https://arxiv.org/abs/2210.03629)). This needs no separate "sequential mode": the second round of the same loop is the sequential step. A single-number question stays a one-query, two-call turn.

**What not to build now.**
- A separate planner node (plan-then-execute): it would cost an extra LLM call, and native parallel calls already give most of the gain.
- Sub-agents: multi-agent systems use about 15x the tokens of a chat ([Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)).
- One giant CTE that computes the whole report: it is the hardest SQL to get right, and one error loses everything (see [Q2](#q2-recognised-patterns-and-what-primary-sources-say)).

**Verify first.** Run one live test on `gemini-3.8-flash` with a real key before relying on this (see [Open questions](#5-open-questions--not-verified)). langchain-google-genai 4.4.2 sends tool results to Gemini without call ids and relies on their order (`langchain_google_genai/chat_models.py:1089-1096`). Google's Gemini 3.8 migration notes ask for `call_id` on every `FunctionResponse` when you use generateContent ([What's new in Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.8)).

---

## 2. Comparison

Example: a Q1 report that needs 4 independent numbers (monthly revenue, top categories, traffic sources, return rate). There are no errors. Latency uses the project's own log from 2026-10-30: LLM calls took 1.5–4.4 s and SQL took 1.0–3.3 s (`logs/retail-agent.log`). Token counts are my arithmetic, with a 1.5k-token system prompt plus schema and 0.7k tokens per SQL-and-result pair. They are estimates, not measurements.

| | **Parallel batch** (recommended) | **Sequential** (ReAct, one query per step) | **One big CTE query** | **Plan-then-execute** (separate planner, e.g. ReWOO / LLMCompiler) |
|---|---|---|---|---|
| How it works | One reply holds all 4 `run_sql` calls; they run together; next call writes the report | Query, look, next query, ... then the report | The model writes one SQL statement that returns all 4 results (CTEs, `UNION ALL`) | A planner call writes the plan or all SQL; code runs it; a solver call writes the report |
| Accuracy | Good when the parts don't depend on each other. LLMCompiler matched or beat ReAct ([paper](https://arxiv.org/abs/2312.04511)). Risk: can't use one result to shape another (fixed by round 2) | Best when steps depend on each other ([ReAct](https://arxiv.org/abs/2210.03629)). Known risks: repeated calls and stopping too early ([LLMCompiler](https://arxiv.org/html/2312.04511)) | Weakest. Long nested SQL is the hard case in text-to-SQL ([DIN-SQL](https://ar5iv.labs.arxiv.org/html/2304.11015), [Spider 2.0](https://arxiv.org/abs/2411.07763)); one error loses every number | Like parallel. ReWOO +4%, LLMCompiler up to +9% vs ReAct; same accuracy as native parallel calls ([LLMCompiler](https://arxiv.org/html/2312.04511)) |
| Latency (4 queries) | ~2 LLM calls + 1 SQL wave: roughly 8–12 s | ~5 LLM calls + 4 SQL: roughly 20–25 s | ~2 LLM calls + 1 larger SQL: roughly 8–12 s | Like parallel; +1 call if planning and SQL writing are separate |
| LLM calls | **2** (3 with a follow-up round) | **5**: the whole budget, nothing left for a fix | **2** (each fix re-writes the big query) | 2–3, plus 2 per re-plan |
| Input tokens (TPM) | ~5.8k (history sent twice) | ~14.5k (history sent 5 times) | ~4k | ~6k |
| BigQuery cost | ≥ 80 MB billed (≥ 10 MB per table per query, 2 tables each): about $0.0005 | Same queries, same cost | ≥ 30 MB billed (3 tables): about $0.0002 | Like parallel |
| If one part fails | Only that query is lost (once errors become results) | Only that step is lost | The whole report is lost | Depends on the executor |
| Build effort | Prompt plus error-as-result (already planned) | None (default behaviour) | Prompt only | A custom `StateGraph` with planner, executor and solver, plus re-plan logic |
| Use it for | Reports, KPI packs, "compare X and Y" | Drill-downs and exploration | One consistent table (shares of total, a side-by-side comparison) | Not needed at this size |

BigQuery figures come from the pricing rules ($6.25 per TiB on demand, first 1 TiB per month free, at least 10 MB per table referenced; [BigQuery pricing](https://cloud.google.com/bigquery/pricing)) and my dry runs ([Q5](#q5-bigquery-side)). On BigQuery's free tier, about 13,000 such reports a month cost nothing.

---

## 3. Findings

### Q1. Parallel or sequential: when is each better?

**Parallel is better when the parts are independent and known up front.** Most of a report is like this: revenue, top categories, return rate and new customers don't depend on each other.
- *LLM calls and rate limits.* A batch of N queries is one model reply, so it costs one request. Gemini counts RPM per request and TPM as input tokens ([Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)). With generateContent, the full conversation is sent again on every turn ([Gemini text generation](https://ai.google.dev/gemini-api/docs/generate-content/text-generation)). So each sequential round adds a request *and* re-sends the system prompt, schema and all earlier results. Anthropic notes that each tool call in the classic loop needs a full model inference pass ([Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).
- *Latency.* Concurrent queries take as long as the slowest one, not the sum. **Observed** in `create_agent`: 3 calls of 1.0 s, 0.2 s and 0.6 s took 1.01 s in total. Anthropic reports that parallel tool calling cut research time by up to 90% on complex queries ([Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)).
- *Accuracy.* Planning the calls first doesn't cost accuracy, and often helps. LLMCompiler beat ReAct by up to ~9% ([Kim et al. 2024](https://arxiv.org/abs/2312.04511)), and ReWOO gained 4% on HotpotQA with 5x fewer tokens ([Xu et al. 2023](https://arxiv.org/abs/2305.18323)). LLMCompiler blames two ReAct failures: repeating earlier calls, and stopping early on incomplete results ([LLMCompiler](https://arxiv.org/html/2312.04511)).
- *Cost.* On Google's free tier, input and output tokens for Gemini 3.8 Flash are free; the cost is quota ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), 2026-10-30). Paid prices through 2026-12-31 are $0.75 per 1M input tokens and $3.75 per 1M output tokens (thinking included), doubling on 2027-01-01. Fewer calls also mean fewer thinking passes; Gemini bills thinking tokens as output ([Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)). BigQuery cost is tiny either way ([Q5](#q5-bigquery-side)).

**Sequential is better when the next query depends on the last result.** Examples: drill into the worst segment found by the first query; check real values before filtering; fix an error. ReAct's design reason is that it can track, update and fix its plan from what it observes ([Yao et al. 2023](https://arxiv.org/abs/2210.03629)). Anthropic's guidance matches: prompt chaining trades latency for accuracy when a task splits into fixed, dependent steps; agents fit when the number of steps can't be predicted ([Anthropic, building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Real enterprise SQL work often needs data exploration and several queries ([Spider 2.0](https://arxiv.org/abs/2411.07763)).

**Best: parallel within a round, sequential across rounds.** Anthropic tells developers to run independent, read-only calls in parallel and dependent or side-effecting ones in order. It suggests a prompt line telling the model to batch only independent calls ([Anthropic parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)). All our queries are read-only SELECTs, so batching them is safe.

### Q2. Recognised patterns, and what primary sources say

**ReAct (reason + act loop).** The model alternates reasoning and actions, so it can gather facts and change its plan as it goes. It beat imitation and RL baselines by 34% (ALFWorld) and 10% (WebShop) in success rate ([Yao et al., ICLR 2023](https://arxiv.org/abs/2210.03629)). `create_agent` is this loop: the model calls tools until it stops ([LangChain agents](https://docs.langchain.com/oss/python/langchain/agents)).

**Plan-and-Solve.** A zero-shot prompt: first make a plan that splits the task into subtasks, then carry them out. It targets "missing step" errors and beat zero-shot chain-of-thought on all ten datasets tested ([Wang et al., ACL 2023](https://arxiv.org/abs/2305.04091)). This is a prompting idea, not a tool pattern. For us it means "work out every number first", in the model's thinking.

**ReWOO (reasoning without observation).** Plan all tool calls up front, without waiting for results, then run them and write the answer. It avoids re-sending prompts on every step: 5x token efficiency and +4% accuracy on HotpotQA ([Xu et al. 2023](https://arxiv.org/abs/2305.18323)).

**LLMCompiler (parallel function calling).** It has three parts: a planner that builds a graph of tasks with their dependencies, a task-fetching unit, and an executor that runs independent tasks in parallel ([Kim et al., ICML 2024](https://arxiv.org/abs/2312.04511)). It can re-plan when the flow can't be fixed at the start ([LLMCompiler](https://arxiv.org/html/2312.04511)). Against OpenAI's native parallel function calling, accuracy was about the same (e.g. 62.00% vs 62.05% on HotpotQA). The gain was mostly latency, up to 35% ([same](https://arxiv.org/html/2312.04511)). **Takeaway:** native parallel tool calls give us most of LLMCompiler's benefit without building a planner.

**Anthropic's workflow patterns.** *Parallelization (sectioning)* runs independent subtasks at once. *Orchestrator-workers* has a lead model split a task it can't predict in advance. Both sit beside prompt chaining and agents, and the advice is to start with the simplest approach and add complexity only when it clearly helps ([Anthropic, building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

**Decomposition in text-to-SQL research.** In the leading papers, splitting a question into parts happens *inside the reasoning*, and the output is still **one** final SQL query per question. Running several queries is used for fixing errors, not for splitting the question.
- **DIN-SQL** has four modules: schema linking; classification (easy / non-nested complex / nested complex) and decomposition; SQL generation; self-correction. For nested queries it solves the sub-queries first, then writes the final SQL. With GPT-4 on Spider it cost about $0.5 and took about 60 s per question ([Pourreza & Rafiei, NeurIPS 2023](https://ar5iv.labs.arxiv.org/html/2304.11015)). That shows the price of many-call pipelines.
- **MAC-SQL** has three agents. A Selector shrinks the schema. A Decomposer writes sub-questions and their sub-SQL in a single pass, and the last sub-SQL is the answer. A Refiner runs the SQL and fixes syntax errors, unknown columns and empty results, for up to 3 rounds. On BIRD dev, removing the Decomposer cost 3.85 points and removing the Refiner cost 4.63 ([Wang et al., COLING 2025](https://arxiv.org/html/2312.11242)).
- **CHASE-SQL** (mostly Google Cloud authors) builds candidate queries in several ways. One is divide-and-conquer chain-of-thought: sub-problems as pseudo-SQL, joined into one final query in one LLM call. It also uses a query fixer (up to 3 attempts) and a fine-tuned selector that compares candidates pairwise. It reached 73.0% on the BIRD test set ([Pourreza et al. 2024](https://arxiv.org/html/2410.01943)).
- **Spider 2.0** is the multi-query case. Its 632 enterprise tasks (BigQuery, Snowflake) often need several queries, data exploration and very long SQL. The best agent solved 21.3%, against 91.2% on Spider 1.0 and 73.0% on BIRD ([Lei et al., ICLR 2025](https://arxiv.org/abs/2411.07763)). Long multi-step SQL work is still where agents fail most.

What this means for us: a report is really several questions, so use **one small query per question**. Inside a question, one well-formed SQL statement (with CTEs if needed) is the norm. A result-driven fix loop (MAC-SQL's Refiner, CHASE-SQL's fixer, both capped at 3) matches our "fix only the failed query" rule.

**One big CTE query vs several small queries.**
- *BigQuery does not save CTE results.* A non-recursive CTE runs once for each place it is referenced ([GoogleSQL query syntax](https://cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax), 2026-10-27). Google says CTEs are for readability, not speed, and may be evaluated several times ([BigQuery compute best practices](https://cloud.google.com/bigquery/docs/best-practices-performance-compute)). So one big query doesn't "scan once and reuse" by design. My dry run did show the same byte estimate for a CTE used twice and once ([Q5](#q5-bigquery-side)).
- *Accuracy and failure isolation.* Long nested SQL is the hard case (DIN-SQL handles it specially; Spider 2.0's long workflows are where agents fail). A single statement either works or doesn't: one wrong column loses the whole report. Google's ADK suggests prompting for several specific calls over one complex call ([ADK tool performance](https://raw.githubusercontent.com/google/adk-docs/refs/heads/main/docs/tools-custom/performance.md)). Anthropic notes agents can be steered toward many small, targeted queries ([Anthropic, writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- *Where one query wins.* When numbers must be consistent in one table, such as a state-X-vs-state-Y comparison or shares of a total, use one `GROUP BY` query, not two queries. That is why the prompt says "compare groups inside one query".

### Q3. Gemini specifics

**Which API LangChain uses.** langchain-google-genai calls `client.models.generate_content` (`langchain_google_genai/chat_models.py:4188`). Google's main function-calling page now shows the newer Interactions API ([Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)). The generateContent version lives under `/generate-content/` ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling), updated 2026-10-15). The rules below follow the generateContent pages.

**Parallel function calling.**
- The model can return several independent function calls in one turn.
- Gemini 3.8 Flash, 3.6 Flash and 3.5 Flash-Lite are all listed as supporting function calling, parallel function calling and compositional function calling ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)).
- I found **no documented limit** on how many calls one reply can hold.
- *Returning results.* All `functionResponse` parts go back together in one turn. Calls and responses should not be interleaved across separate turns. Responses should follow the call order with matching names; Gemini 3+ can also match by `id` ([Vertex AI function calling](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/function-calling), 2026-10-30). The Gemini API page says order doesn't matter because the API maps results by `id`, but its own example says to keep the same order ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)). The Gemini 3.8 migration checklist adds that with generateContent every `FunctionResponse` should include `call_id` and `name` ([What's new in Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.8), 2026-10-13).
- *What LangChain 4.4.2 actually sends.* It puts all tool results for one AI message into a single `Content(role="user")` (`chat_models.py:1101-1125`, `:1543-1546`). That matches the "together in one turn" rule. But it builds both `FunctionCall` and `FunctionResponse` **without ids** (`chat_models.py:1516-1519`, `:1089-1096`), so matching depends on order. Since every call here is `run_sql`, only the position tells the results apart. LangChain keeps tool results in call order (see Q4), so this should work, but it is **not verified live on 3.8 Flash** (see Open questions).
- *Thought signatures.* With parallel calls, only the first `functionCall` part carries the signature. Leaving it out within the current turn gives a 400 error on Gemini 3 ([Vertex AI thought signatures](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/thought-signatures), 2026-10-30). LangChain keeps signatures. If one is missing, it puts Google's documented bypass value on the first function call of each model message (`chat_models.py:1601-1659`).

**Compositional (sequential) function calling.** The model chains calls across turns: call, get the result, then decide the next call. The docs' manual loops handle one call per iteration, and the Python SDK's automatic function calling repeats until a text answer comes ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)). Each step is a separate request, so each counts toward RPM and re-sends the history (TPM).

**No switch to turn parallel calls off.** The function-calling config has only `mode` (AUTO, ANY, NONE, VALIDATED), `allowed_function_names` and `stream_function_call_arguments` (`google/genai/types.py:491-503`, `:6340-6353`). The docs describe no setting to disable parallel calls ([Vertex AI function calling](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/function-calling); [Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)). The only levers are the prompt and how our code runs the calls.

**Other caveats.**
- *Text before tool calls.* Requiring structured text right before a tool call can cause `Malformed_Function_Call`. The fix is to not require such text, or to use a dedicated `update()` function or Markdown headers for notes ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)).
- *Tool count.* Keep the active set to 10–20 tools ([same](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)). We have one.
- *Temperature.* The function-calling page suggests a low temperature. The Gemini 3 guide says to keep the default 1.0, since lower values can cause looping ([Gemini 3 guide](https://ai.google.dev/gemini-api/docs/gemini-3), 2026-10-09, now marked deprecated). The 3.8 guide says to remove `temperature`, `top_p` and `top_k` ([What's new in Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.8)). Our code sets none of them; keep it that way.
- *Thinking.* Gemini 3.8 Flash supports `low`, `medium` (default) and `high`; `minimal` returns an error. `low` suits latency-sensitive chat ([What's new in Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.8); [Gemini 3.8 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)).
- *Declarations cost tokens.* Function descriptions and parameters count toward the input token limit ([Gemini function calling, generateContent](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)).

**Rate limits** ([Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), updated 2026-10-28):
- Measured as RPM, TPM (input tokens) and RPD. Going over any one of them gives an error.
- Limits apply per **project**, not per API key. RPD resets at midnight Pacific time.
- Google says the limits "are not guaranteed and actual capacity may vary".
- **The docs no longer publish free-tier numbers per model.** The page sends readers to AI Studio to see their active limits. I could not find RPM/TPM/RPD figures for Gemini 3.8 Flash or 3.5 Flash-Lite in any public doc.
- The free tier exists: Gemini 3.8 Flash, 3.6 Flash, 3.5 Flash-Lite and 3.1 Flash-Lite list input and output as free of charge. On the free tier, Google uses the content to improve its products ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), 2026-10-30).
- Current stable Flash models: `gemini-3.8-flash` (new), `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` ([Gemini models](https://ai.google.dev/gemini-api/docs/models), 2026-10-28).

### Q4. LangChain / LangGraph specifics

**Does `create_agent` run several tool calls from one AIMessage concurrently? Yes.**
- `create_agent` sends each pending tool call to the `tools` node as its own `Send` (`langchain/agents/factory.py:2020-2032`). All those tasks fall in the same superstep.
- LangGraph's runner submits every task of a superstep to a background executor and waits for the first to finish or fail (`langgraph/pregel/_runner.py:257-285`). For sync runs that executor is a thread pool, sized by the `max_concurrency` config (`langgraph/pregel/_executor.py:48-58`; `langchain_core/runnables/config.py:660-675`). For async runs it is asyncio with an optional semaphore (`langgraph/pregel/_executor.py:143-146`).
- Inside ToolNode, several calls in one task also run through `executor.map` (sync) or `asyncio.gather` (async) (`langgraph/prebuilt/tool_node.py:829-833`, `:865-866`).
- LangGraph's docs say parallel branches run in the same superstep, and `Send` is the tool for map-reduce ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [LangGraph use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api)).
- **Observed** (offline script with a scripted fake model, 2026-10-31): three `run_sql` calls sleeping 1.0 s, 0.2 s and 0.6 s started at the same moment on three threads and finished in 1.01 s in total. The model was called twice.

**Order of results.** Writes from a superstep are applied in a fixed order, sorted by task path (`langgraph/pregel/_algo.py:254-255`). **Observed**: the ToolMessages came back in call order (c0, c1, c2) even though they finished in a different order. LangGraph's docs warn that, in general, updates from parallel branches "may not be ordered consistently" ([LangGraph use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api)). Keep that in mind if this flow is ever replaced by a custom graph, because Gemini matching depends on order here (Q3).

**What happens if one of several parallel calls fails?**
- *By default the whole turn fails.* `create_agent` builds its ToolNode without `handle_tool_errors` (`factory.py:1140-1148`). The default handler only turns argument-validation errors into messages and re-raises all other exceptions (`tool_node.py:391-399`; docstring `:698-702`).
- When one task raises, the runner stops, cancels tasks that haven't started, and re-raises (`_runner.py:618-641`, `:657-703`). The docs call a superstep transactional: if one branch raises, none of the updates are applied ([LangGraph use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api)).
- **Observed**: with calls `q1`, `bad`, `q3`, `invoke` raised `RuntimeError` after 1.0 s. The model was not called again, and the results of `q1` and `q3` were lost. Threads that had already started ran to the end, but their output was dropped.
- *With errors returned as results.* With `ToolErrorMiddleware` (needs langchain ≥ 1.3.14 ([LangChain built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in))), the failing call became a `ToolMessage(status="error")`. The other two succeeded, and the model got all three and answered (**Observed**). A `run_sql` that catches errors itself has the same effect.
- `ToolRetryMiddleware` retries with backoff, and its default `on_failure="continue"` also returns an error message ([LangChain built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)). It is useful for transient BigQuery errors, not for bad SQL, which fails the same way every time.

**Can parallel tool calls be turned off?**
- LangChain's docs say to pass `parallel_tool_calls=False` to `bind_tools`. They name OpenAI and Anthropic as providers that support it ([LangChain models](https://docs.langchain.com/oss/python/langchain/models)).
- **Not for Gemini.** `ChatGoogleGenerativeAI.bind_tools` has only `tool_config` and `tool_choice`. Other keyword arguments go into `bind()` (`langchain_google_genai/chat_models.py:4577-4652`) and later into `GenerateContentConfig`, which rejects unknown fields (`chat_models.py:4076-4150`). **Observed** offline: `bind_tools([run_sql], parallel_tool_calls=False)` fails when the request is built, with a pydantic `ValidationError`: "Extra inputs are not permitted".
- `create_agent` doesn't pass such an option itself. It calls `bind_tools(tools, tool_choice=..., **model_settings)` (`factory.py:1484-1490`).
- Practical options: prompt the model to send one call at a time, or run calls one by one with `config={"max_concurrency": 1}`. The second makes execution sequential but doesn't stop the model from batching.

**Budget-related middleware.**
- `ModelCallLimitMiddleware(run_limit=N)` counts calls per invocation (one question). With `exit_behavior="end"` it ends with an artificial "limit exceeded" AI message (`model_call_limit.py:165-208`; [LangChain built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)).
- `ToolCallLimitMiddleware` has three modes: `continue` (block extra calls with error messages), `error` and `end` (`tool_call_limit.py:23-33`). In the installed 1.4.7 source, `end` also gives the other pending parallel calls an explanatory message. The docs page instead says `end` raises `NotImplementedError` when other tools are pending. Trust the source for the installed version.
- `ModelRequest.override(tool_choice=...)` in a `wrap_model_call` middleware changes the next call (`langchain/agents/middleware/types.py:203`). **Observed**: on the last allowed call the fake model received `tool_choice="none"` and the run ended with its answer.

### Q5. BigQuery side

**Concurrency.**
- BigQuery sets the number of concurrent queries dynamically for each on-demand project, and an on-demand project can't set a maximum. Extra queries wait in a queue: up to 1,000 interactive queries per region. By default a queued interactive query times out after 6 hours ([BigQuery query queues](https://docs.cloud.google.com/bigquery/docs/query-queues), 2026-10-27).
- On-demand projects generally get up to 2,000 concurrent slots, shared by all their queries ([BigQuery pricing](https://cloud.google.com/bigquery/pricing); [BigQuery quotas](https://docs.cloud.google.com/bigquery/quotas)).
- 2–6 parallel queries on tables of a few MB are far below these limits.

**Caching.**
- Results are cached for about 24 hours. Cached queries are not billed but still count toward quota.
- The query text must match exactly; even whitespace or comments matter. The cache is per user and per project.
- Queries with non-deterministic functions such as `CURRENT_DATE` or `CURRENT_TIMESTAMP` are **not** cached. Neither are queries on tables that changed since the result was cached ([BigQuery cached results](https://docs.cloud.google.com/bigquery/docs/cached-results), 2026-10-27).
- Our prompt currently tells the model to compute date ranges from `CURRENT_DATE()`, which turns caching off for those queries.
- **Observed** (table metadata): all four tables were last modified on 2026-10-29, so the dataset does change. Cache hits across days are not reliable either way.

**Dry runs.**
- A dry run validates the query and estimates bytes. It uses no slots and is not charged ([BigQuery running queries](https://cloud.google.com/bigquery/docs/running-queries), 2026-10-30).
- The estimate is an upper bound and can be higher than the bytes billed. On tables protected by row-level security, a dry run always reports 0 bytes ([BigQuery estimate costs](https://cloud.google.com/bigquery/docs/estimate-costs), 2026-10-27).
- Google's own text-to-SQL team uses parsing plus a dry run as a check, and feeds failures back to the model for a second try ([Google Cloud blog, Jacobsson 2025-05-17](https://cloud.google.com/blog/products/databases/techniques-for-improving-text-to-sql)). That matches plan step 2.

**Billing rules** ([BigQuery pricing](https://cloud.google.com/bigquery/pricing)):
- On demand costs $6.25 per TiB, and the first 1 TiB per month is free.
- You pay for the columns read, even with `LIMIT`.
- Queries that return an error or hit the cache are not charged.
- Charges round up to the nearest MB, with **a minimum of 10 MB per table referenced and 10 MB per query**.

**Several small queries vs one CTE query: bytes.** **Observed** with dry runs on `thelook_ecommerce` (2026-10-31):

| Query | Tables | Bytes processed (estimate) |
|---|---|---|
| A: Q1 revenue by state | order_items, users | 8,140,862 |
| B: Q1 revenue by category | order_items, products | 6,783,970 |
| C: A and B as one CTE query (`UNION ALL`) | order_items, users, products | 10,180,991 |
| D: state X vs Y, CTE referenced **twice** | order_items, users | 6,694,894 |
| E: state X vs Y, one `GROUP BY`, no CTE | order_items, users | 6,694,894 |

- The tables are small and have no partitioning or clustering. Sizes: `order_items` 13.6 MB, `users` 19.8 MB, `orders` 6.7 MB, `products` 4.3 MB (**Observed**, table metadata). So the date filter doesn't reduce bytes, and every query is dominated by the **10 MB per table minimum**.
- Two small queries (A + B) are billed at least 2 × 20 MB = 40 MB. The combined query C is billed at least 30 MB. Several small queries *do* bill a bit more, but the difference is fractions of a cent: 80 MB is about $0.0005.
- D and E got the same estimate. In this test, using a CTE twice didn't double the estimated bytes, even though BigQuery evaluates each reference ([GoogleSQL query syntax](https://cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax)). Actual bytes billed on a real run were not measured.
- The existing `maximum_bytes_billed` cap (1 GB per query) applies to each query separately, so a batch of small queries stays far below it.

### Q6. Other first-party guidance

- **Anthropic, building effective agents** (2024-12-19). Start with the simplest thing that works. Use *parallelization* for independent subtasks, *prompt chaining* for fixed dependent steps, and *orchestrator-workers* when subtasks can't be predicted. Agents trade latency and cost for task performance. Understand what a framework does under the hood ([link](https://www.anthropic.com/engineering/building-effective-agents)).
- **Anthropic, parallel tool use docs** (read 2026-10-31).
  - The API doesn't prescribe an execution order. Independent read-only calls are usually safe to run in parallel.
  - Return one result per call, all in the next user message. If you skip or fail a call, return an error result for it.
  - Splitting results across several messages "teaches" the model to stop making parallel calls.
  - The docs give a system-prompt line to encourage parallel calls and one to keep dependent calls apart. Parallel use can be turned off with `disable_parallel_tool_use` ([link](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)).
  - LangChain + Gemini already sends all results in one turn (Q3).
- **Anthropic, multi-agent research system** (2025-06-13). Parallel tool calls cut research time by up to 90%. Token use explains 80% of performance variance. Multi-agent setups use about 15x the tokens of a chat and fit poorly when tasks share context or depend on each other ([link](https://www.anthropic.com/engineering/multi-agent-research-system)).
- **Anthropic, writing tools for agents** (2025-09-11). Give tool responses sensible defaults for truncation and filtering. When cutting results, tell the agent how to narrow its query. Make error messages say what went wrong and how to fix it ([link](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- **Anthropic, advanced tool use** (2025-11-24). Each classic tool call needs a full model pass. "Programmatic tool calling" (the model writes code that calls tools) cut average tokens by 37% on complex research tasks ([link](https://www.anthropic.com/engineering/advanced-tool-use)). It is Claude-only, so not usable here; it confirms that round trips are the cost to cut.
- **OpenAI, function calling** (read 2026-10-31). The model may call several functions in one turn. `parallel_tool_calls: false` ensures zero or one call per turn. If a fine-tuned model makes several calls in one turn, strict mode is off for those calls. Best practices: keep under ~20 functions, merge functions that are always called in sequence, and don't make the model fill in values your code already knows ([link](https://developers.openai.com/api/docs/guides/function-calling)).
- **Google ADK.** Since ADK 1.10.0 (Python), the framework tries to run requested function tools in parallel. Sync tools block the others, so tools should be `async`. The docs suggest prompting for parallel calls and preferring several specific calls over one complex call ([ADK tool performance](https://raw.githubusercontent.com/google/adk-docs/refs/heads/main/docs/tools-custom/performance.md)). ADK's `ParallelAgent` runs sub-agents at once, each on its own branch without shared history ([ADK parallel agents](https://raw.githubusercontent.com/google/adk-docs/refs/heads/main/docs/agents/workflow-agents/parallel-agents.md)).
- **BigQuery conversational analytics** (2026-10-27). Google's own data agents use *verified queries* (formerly "golden queries"), natural-language instructions (default filters, fiscal calendars), and glossary terms. The docs advise spending limits at project, user and query level. Agents can't write or run DML ([link](https://docs.cloud.google.com/bigquery/docs/conversational-analytics)). The Conversational Analytics API combines SQL, Python and charts, in stateful or stateless chat ([link](https://docs.cloud.google.com/gemini/docs/conversational-analytics-api/overview), 2026-10-26). Neither page says how many queries an agent runs per question. Verified queries are the same idea as this project's "Golden Knowledge bucket".
- **Google Cloud, text-to-SQL techniques** (2025-05-17). The post covers retrieval of tables and columns, asking clarifying questions when a question is ambiguous, self-consistency (several candidate queries, pick the best), validation by parsing and dry run with re-prompting, and LLM-as-a-judge evals. It does not describe splitting a question into several executed queries ([link](https://cloud.google.com/blog/products/databases/techniques-for-improving-text-to-sql)).

---

## 4. Sources

**Papers**
- Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models*, ICLR 2023. https://arxiv.org/abs/2210.03629
- Wang et al., *Plan-and-Solve Prompting*, ACL 2023. https://arxiv.org/abs/2305.04091
- Xu et al., *ReWOO: Decoupling Reasoning from Observations*, 2023. https://arxiv.org/abs/2305.18323
- Kim et al., *An LLM Compiler for Parallel Function Calling*, ICML 2024. https://arxiv.org/abs/2312.04511 (body: https://arxiv.org/html/2312.04511)
- Pourreza & Rafiei, *DIN-SQL*, NeurIPS 2023. https://arxiv.org/abs/2304.11015 (body: https://ar5iv.labs.arxiv.org/html/2304.11015)
- Wang et al., *MAC-SQL*, COLING 2025. https://arxiv.org/abs/2312.11242 (body: https://arxiv.org/html/2312.11242)
- Pourreza et al., *CHASE-SQL*, 2024 preprint. https://arxiv.org/abs/2410.01943 (body: https://arxiv.org/html/2410.01943)
- Lei et al., *Spider 2.0*, ICLR 2025. https://arxiv.org/abs/2411.07763

**Gemini (Google AI for Developers, Vertex AI)**
- Function calling, generateContent (updated 2026-10-15): https://ai.google.dev/gemini-api/docs/generate-content/function-calling
- Function calling, Interactions API: https://ai.google.dev/gemini-api/docs/function-calling
- Rate limits (2026-10-28): https://ai.google.dev/gemini-api/docs/rate-limits
- Pricing (2026-10-30): https://ai.google.dev/gemini-api/docs/pricing
- Models (2026-10-28): https://ai.google.dev/gemini-api/docs/models
- Gemini 3.8 Flash model page (2026-10-30): https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- What's new in Gemini 3.8 Flash (2026-10-13): https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.8
- Thinking (2026-10-14): https://ai.google.dev/gemini-api/docs/thinking
- Text generation, multi-turn (2026-10-19): https://ai.google.dev/gemini-api/docs/generate-content/text-generation
- Gemini 3 guide (2026-10-09, marked deprecated): https://ai.google.dev/gemini-api/docs/gemini-3
- Vertex AI function calling (2026-10-30): https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/function-calling
- Vertex AI thought signatures (2026-10-30): https://docs.cloud.google.com/vertex-ai/generative-ai/docs/thought-signatures

**BigQuery and Google Cloud**
- Pricing: https://cloud.google.com/bigquery/pricing
- Quotas and limits: https://docs.cloud.google.com/bigquery/quotas
- Query queues (2026-10-27): https://docs.cloud.google.com/bigquery/docs/query-queues
- Cached results (2026-10-27): https://docs.cloud.google.com/bigquery/docs/cached-results
- Running queries, dry run (2026-10-30): https://cloud.google.com/bigquery/docs/running-queries
- Estimate and control costs (2026-10-27): https://cloud.google.com/bigquery/docs/estimate-costs
- GoogleSQL query syntax, WITH clause (2026-10-27): https://cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax
- Optimize query computation (2026-10-27): https://cloud.google.com/bigquery/docs/best-practices-performance-compute
- Conversational analytics overview (2026-10-27): https://docs.cloud.google.com/bigquery/docs/conversational-analytics
- Conversational Analytics API overview (2026-10-26): https://docs.cloud.google.com/gemini/docs/conversational-analytics-api/overview
- Google Cloud blog, text-to-SQL techniques (2025-05-17): https://cloud.google.com/blog/products/databases/techniques-for-improving-text-to-sql
- ADK tool performance: https://google.github.io/adk-docs/tools/performance (source: https://raw.githubusercontent.com/google/adk-docs/refs/heads/main/docs/tools-custom/performance.md)
- ADK parallel agents: https://google.github.io/adk-docs/agents/workflow-agents/parallel-agents/ (source: https://raw.githubusercontent.com/google/adk-docs/refs/heads/main/docs/agents/workflow-agents/parallel-agents.md)

**LangChain / LangGraph docs**
- Models, parallel tool calls: https://docs.langchain.com/oss/python/langchain/models
- Agents: https://docs.langchain.com/oss/python/langchain/agents
- Tools: https://docs.langchain.com/oss/python/langchain/tools
- Built-in middleware: https://docs.langchain.com/oss/python/langchain/middleware/built-in
- LangGraph graph API: https://docs.langchain.com/oss/python/langgraph/graph-api
- LangGraph use-graph-api (supersteps, transactional, max_concurrency): https://docs.langchain.com/oss/python/langgraph/use-graph-api

**Anthropic and OpenAI**
- Building effective agents (2024-12-19): https://www.anthropic.com/engineering/building-effective-agents
- Multi-agent research system (2025-06-13): https://www.anthropic.com/engineering/multi-agent-research-system
- Writing tools for agents (2025-09-11): https://www.anthropic.com/engineering/writing-tools-for-agents
- Advanced tool use (2025-11-24): https://www.anthropic.com/engineering/advanced-tool-use
- Parallel tool use docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use
- OpenAI function calling guide: https://developers.openai.com/api/docs/guides/function-calling

**Installed source code** (under `.venv/lib/python3.12/site-packages/`)
- `langchain/agents/factory.py:1140-1148`: ToolNode built without `handle_tool_errors`
- `langchain/agents/factory.py:1484-1490`: `bind_tools(final_tools, tool_choice=..., **model_settings)`
- `langchain/agents/factory.py:2020-2032`: one `Send("tools", [tool_call])` per pending tool call
- `langchain/agents/middleware/tool_error.py:37-80`: `ToolErrorMiddleware`
- `langchain/agents/middleware/tool_call_limit.py:23-33`: exit behaviours, parallel calls under `end`
- `langchain/agents/middleware/model_call_limit.py:165-208`: `before_model` limit check, jump to end with canned message
- `langchain/agents/middleware/types.py:203`: `ModelRequest.override`
- `langgraph/prebuilt/tool_node.py:391-399`: default error handler re-raises non-argument errors
- `langgraph/prebuilt/tool_node.py:698-702`: docstring of the default
- `langgraph/prebuilt/tool_node.py:829-833`, `:865-866`: `executor.map` / `asyncio.gather`
- `langgraph/pregel/_runner.py:257-285`: tasks submitted together, wait for first completed
- `langgraph/pregel/_runner.py:618-641`, `:657-703`: stop others on failure, cancel, re-raise
- `langgraph/pregel/_executor.py:48-58`, `:143-146`: thread pool / asyncio semaphore from `max_concurrency`
- `langchain_core/runnables/config.py:660-675`: `ThreadPoolExecutor(max_workers=max_concurrency)`
- `langgraph/pregel/_algo.py:254-255`: writes applied sorted by task path
- `langchain_google_genai/chat_models.py:4188`: uses `generate_content`
- `langchain_google_genai/chat_models.py:4577-4652`: `bind_tools` has no parallel option; extra kwargs go to `bind()`
- `langchain_google_genai/chat_models.py:4076-4150`: kwargs end in `GenerateContentConfig`
- `langchain_google_genai/chat_models.py:1089-1096`: `FunctionResponse(name=..., response=...)` with no `id`
- `langchain_google_genai/chat_models.py:1516-1519`: `FunctionCall(name=..., args=...)` with no `id`
- `langchain_google_genai/chat_models.py:1101-1125`, `:1543-1546`: all tool results of one AI message in one `Content(role="user")`
- `langchain_google_genai/chat_models.py:1601-1659`: thought-signature patch on the first function call
- `langchain_google_genai/chat_models.py:1957-1958`: tool-call id from `function_call.id`, else a UUID
- `langchain_google_genai/_function_utils.py:700-740`: `tool_choice` mapping (`"none"` → `NONE` at `:737-738`)
- `google/genai/types.py:491-503`: function-calling modes
- `google/genai/types.py:6340-6353`: `FunctionCallingConfig` fields (no parallel switch)
- `google/genai/types.py:2058-2061`: `FunctionResponse.id` exists ("populated by the client")

**Experiments run for this note** (2026-10-31; throwaway scripts, not in the repo)
- Offline `create_agent` with a scripted fake model: concurrency, result order, failure with and without `ToolErrorMiddleware`, and `tool_choice="none"` on the last call.
- Offline `ChatGoogleGenerativeAI(...).bind_tools(..., parallel_tool_calls=False)`: `ValidationError` when the request is built.
- BigQuery dry runs (free) of five queries, plus table metadata reads.
- Project log `logs/retail-agent.log` (2026-10-30): LLM and SQL durations.

---

## 5. Open questions / not verified

1. **The Gemini 3.8 Flash round trip with parallel calls is untested.** No Gemini API key was available on this machine, so I could not run a live test. The project log has no completed `gemini-3.8-flash` tool round trip, only a 504. It does show `gemini-3.6-flash` finishing a two-query sequential turn on 2026-10-30. Still unknown:
   - (a) whether 3.8 Flash really sends several `run_sql` calls in one reply with the prompt above;
   - (b) whether Gemini accepts LangChain 4.4.2's results without ids for *parallel* calls, given that the 3.8 checklist asks for `call_id`.

   Test this first. If it fails, use 3.6 Flash or patch the message conversion.
2. **Free-tier limits** (RPM, TPM, RPD) for Gemini 3.8 Flash and 3.5 Flash-Lite are not in the public docs as of 2026-10-28; they show only in AI Studio. The docs also don't say whether thinking tokens or cached tokens count toward TPM.
3. **How many parallel calls one Gemini reply can hold**: not documented.
4. **Accuracy of parallel vs sequential for text-to-SQL reports specifically**: I found no primary study. The evidence comes from general tool-use benchmarks (HotpotQA, WebShop and others) and single-query text-to-SQL benchmarks. Our own eval set (design-only "QA and evals") should measure it.
5. **Is a shared `google.cloud.bigquery.Client` safe across threads?** I found no official statement. `BigQueryDatabase` shares one client and one job config across parallel tool calls. My concurrency test used a fake tool, not BigQuery. If unsure, create a client per thread or guard it.
6. **Bytes billed for a CTE referenced twice**: only the dry-run *estimate* was equal. I didn't run real queries to read `total_bytes_billed`. Whether "MB" in the 10 MB minimum means 10^6 bytes or MiB isn't stated; it doesn't change the conclusion.
7. **Refresh schedule of `thelook_ecommerce`**: not found in the sources read. Observed only that the tables were last modified on 2026-10-29. This affects how often the cache helps.
8. **Doc conflicts noted**:
   - Gemini's page says result order doesn't matter (matching by id), but its example says to keep the order.
   - Gemini's function-calling page suggests a low temperature; the Gemini 3 and 3.8 guides say keep the default or remove it.
   - LangChain's middleware page and the installed 1.4.7 source disagree on `ToolCallLimitMiddleware(exit_behavior="end")` with pending parallel calls.
9. **One live run on a different model.** With `.env` set to the Anthropic provider, I ran one live test with Claude Haiku 5.5 and a fake `run_sql` (no BigQuery). The first reply held 4 parallel queries, and the turn used 3 LLM calls. Input tokens per call were 1,561, then 3,041, then 3,450. This shows the prompt can trigger batching on one model; it says nothing about Gemini.
