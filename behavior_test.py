"""Regression test for the behavior layer."""
from agenticmeter.span import Span, SpanType
from agenticmeter.analysis.insights import analyze, Config

def mk(type, name, trace, start, end, parent="root", **kw):
    s = Span(type=type, name=name, trace_id=trace, parent_id=parent, start=start, end=end, **kw)
    return s

# ---- messy run: should trip all six ------------------------------------
def messy():
    T = "t1"; spans = []
    root = mk(SpanType.AGENT, "agent", T, 0, 10, parent=None); root.status = "ok"
    spans.append(root)
    spans.append(mk(SpanType.LLM, "openai.chat", T, 0.1, 0.3, model="gpt-4o",
                    prompt_tokens=5000, completion_tokens=200, cost_usd=0.0005, input=[{"q":"a"}]))
    spans.append(mk(SpanType.LLM, "openai.chat", T, 0.85, 1.0, model="gpt-4o",
                    prompt_tokens=12000, completion_tokens=1800, cost_usd=0.012, input=[{"q":"b"}]))
    spans.append(mk(SpanType.LLM, "openai.chat", T, 9.8, 9.95, model="gpt-4o",
                    prompt_tokens=45000, completion_tokens=250, cost_usd=0.003, input=[{"q":"c"}]))
    for t0 in [0.30, 0.40, 0.50, 0.60]:
        spans.append(mk(SpanType.TOOL, "search", T, t0, t0+0.05, input={"query":"x"}))
    for t0 in [0.70, 0.75]:
        spans.append(mk(SpanType.LLM, "openai.chat", T, t0, t0+0.03, model="gpt-4o-mini",
                        prompt_tokens=800, completion_tokens=50, cost_usd=0.001,
                        input=[{"role":"user","content":"summarize"}]))
    f = mk(SpanType.TOOL, "fetch", T, 0.80, 0.84, input={"u":"."}); f.status="error"; f.error="Timeout"
    spans.append(f)
    spans.append(mk(SpanType.RETRIEVAL, "vector_search", T, 1.0, 9.8, input={"k":20}))
    return spans

ins = analyze(messy())
codes = {i.code for i in ins}
expected = {"cost_concentration", "repeated_tool_calls", "time_concentration",
            "context_growth", "repeated_llm_calls", "silent_tool_failures"}
print("fired:", sorted(codes))
assert expected <= codes, f"missing: {expected - codes}"
# every insight carries evidence and an action
for i in ins:
    assert i.evidence and i.action and i.title
    assert i.tier in ("fact", "pattern")
# the loop heir is the only pattern; the rest are facts
assert {i.code for i in ins if i.tier == "pattern"} == {"repeated_tool_calls"}
# ranking: highest score first
assert all(ins[k].score >= ins[k+1].score for k in range(len(ins)-1))
print("messy run: all six fired, all carry evidence + action ✅")

# ---- clean run: should trip NOTHING ------------------------------------
def clean():
    T = "t2"
    root = mk(SpanType.AGENT, "agent", T, 0, 1.0, parent=None); root.status="ok"
    a = mk(SpanType.LLM, "openai.chat", T, 0.0, 0.3, model="gpt-4o",
           prompt_tokens=1000, completion_tokens=200, cost_usd=0.004, input=[{"q":"1"}])
    b = mk(SpanType.TOOL, "search", T, 0.3, 0.5, input={"query":"alpha"})
    c = mk(SpanType.LLM, "openai.chat", T, 0.5, 0.8, model="gpt-4o",
           prompt_tokens=1400, completion_tokens=180, cost_usd=0.005, input=[{"q":"2"}])
    return [root, a, b, c]

clean_ins = analyze(clean())
print("clean run insights:", [i.code for i in clean_ins])
assert clean_ins == [], "clean run should produce no warnings"
print("clean run: zero false alarms ✅")

print("\nALL BEHAVIOR CHECKS PASSED ✅")
