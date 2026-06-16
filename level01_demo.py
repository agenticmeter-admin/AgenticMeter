"""Level 0 + 1 demo.

Run a messy @meter-wrapped agent against the SQLite sink. Watch:
  - the behavior summary print automatically when the run closes (Level 0)
  - the run persist so the CLI can replay it afterwards (Level 1)

Then from the terminal:
    python -m agenticmeter runs
    python -m agenticmeter show
"""
import time
import agenticmeter as am

# fresh db so the demo is reproducible
DB = "/tmp/agenticmeter_demo.db"
import os
if os.path.exists(DB):
    os.remove(DB)

from agenticmeter.sinks.sqlite import SQLiteSink
am.configure(sink=SQLiteSink(DB), summary="auto", auto=False)


@am.tool
def search(query):
    time.sleep(0.01)
    return f"results for {query}"


@am.meter
def research_agent(topic):
    # main reasoning thread with growing context
    with am.span("plan", am.SpanType.LLM) as s:
        s.model = "gpt-4o"; s.prompt_tokens = 5000; s.completion_tokens = 200
        s.cost_usd = am.cost.cost_usd("gpt-4o", 5000, 200)

    # same tool, same args, 4x (loop)
    for _ in range(4):
        search("flights to tokyo")

    # the expensive call
    with am.span("reason", am.SpanType.LLM) as s:
        s.model = "gpt-4o"; s.prompt_tokens = 12000; s.completion_tokens = 1800
        s.cost_usd = am.cost.cost_usd("gpt-4o", 12000, 1800)

    # identical llm call twice (waste)
    for _ in range(2):
        with am.span("summarize", am.SpanType.LLM, input=[{"role": "user", "content": "sum"}]) as s:
            s.model = "gpt-4o-mini"; s.prompt_tokens = 800; s.completion_tokens = 50
            s.cost_usd = am.cost.cost_usd("gpt-4o-mini", 800, 50)

    # a silent tool failure
    try:
        with am.span("fetch_pricing", am.SpanType.TOOL):
            raise TimeoutError("upstream slow")
    except TimeoutError:
        pass  # agent recovers, nothing surfaces... except AgenticMeter

    # retrieval dominates time + peak context
    with am.span("vector_search", am.SpanType.RETRIEVAL):
        time.sleep(0.05)
    with am.span("final", am.SpanType.LLM) as s:
        s.model = "gpt-4o"; s.prompt_tokens = 45000; s.completion_tokens = 250
        s.cost_usd = am.cost.cost_usd("gpt-4o", 45000, 250)

    return "done"


print(">>> running research_agent (summary should print automatically below)\n")
research_agent("tokyo trip")
print("\n>>> run finished and persisted to", DB)
