"""Demo of the behavior layer: build one realistic-but-messy agent run as a span
tree, then run analyze() over it. No SDKs, no persistence — pure functions over
the in-memory tree. This is the thing worth showing.
"""
from agenticmeter.span import Span, SpanType
from agenticmeter.analysis.insights import analyze, format_insights

T = "trace_demo"
spans = []

def add(type, name, start, end, parent="root", **kw):
    s = Span(type=type, name=name, trace_id=T, parent_id=(None if parent is None else parent),
             start=start, end=end, **kw)
    s.span_id = (name[:6] + str(int(start*1000)))[:12]   # readable ids for the demo
    spans.append(s)
    return s

# the run wrapper (succeeds)
root = add(SpanType.AGENT, "research_agent", 0.0, 10.0, parent=None)
root.span_id = "root"; root.status = "ok"

# main reasoning thread — context grows 5k -> 45k
add(SpanType.LLM, "openai.chat", 0.10, 0.30, model="gpt-4o",
    prompt_tokens=5000, completion_tokens=200, cost_usd=0.0005, input=[{"q": "plan"}])
# the one expensive call (cost concentration)
add(SpanType.LLM, "openai.chat", 0.85, 1.00, model="gpt-4o",
    prompt_tokens=12000, completion_tokens=1800, cost_usd=0.012, input=[{"q": "reason"}])
# peak-context call
add(SpanType.LLM, "openai.chat", 9.80, 9.95, model="gpt-4o",
    prompt_tokens=45000, completion_tokens=250, cost_usd=0.003, input=[{"q": "final"}])

# same tool, same args, 4x (repeated tool / loop heir)
for i, t0 in enumerate([0.30, 0.40, 0.50, 0.60]):
    add(SpanType.TOOL, "search", t0, t0 + 0.05, input={"query": "flights to tokyo"})

# identical LLM call twice (pure waste)
for t0 in [0.70, 0.75]:
    add(SpanType.LLM, "openai.chat", t0, t0 + 0.03, model="gpt-4o-mini",
        prompt_tokens=800, completion_tokens=50, cost_usd=0.001,
        input=[{"role": "user", "content": "summarize the results"}])

# a tool that failed, but the run carried on (silent failure)
f = add(SpanType.TOOL, "fetch_pricing", 0.80, 0.84, input={"url": "..."} )
f.status = "error"; f.error = "TimeoutError"

# retrieval dominates wall time (time concentration)
add(SpanType.RETRIEVAL, "vector_search", 1.00, 9.80, input={"k": 20})

print(f"Run: {root.name}  ({len(spans)} spans)\n")
print(format_insights(analyze(spans)))
