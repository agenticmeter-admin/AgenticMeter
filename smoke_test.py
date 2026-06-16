import sys, types, asyncio
import agenticmeter as am
from agenticmeter import SpanType
from agenticmeter import context as ctx
from agenticmeter.tracer import tracer

def banner(t): print("\n=== " + t + " ===")

# ---------------------------------------------------------------- setup
sink = am.configure(redact="scrub", auto=True)

# ---------------------------------------------------------------- 1. manual run + tool + nested span tree
banner("1. manual run / tool / nested tree")

@am.tool
def search(q):
    return f"results for {q}"

@am.meter
def agent(task):
    with am.span("plan", SpanType.LLM) as s:
        s.model = "gpt-4o"; s.prompt_tokens = 100; s.completion_tokens = 50
        s.cost_usd = am.cost.cost_usd("gpt-4o", 100, 50)
    r = search(task)
    with am.span("retrieve", SpanType.RETRIEVAL):
        pass
    return r

out = agent("flights to tokyo")
assert out == "results for flights to tokyo"
trace_id = sink.spans[0].trace_id
root, children = sink.tree(trace_id)
print("root:", root.name, root.type.value)
def show(node, d=1):
    for c in sorted(children[node.span_id], key=lambda s: s.start):
        extra = f"  {c.total_tokens}tok ${c.cost_usd}" if c.type==SpanType.LLM else ""
        print("  "*d + f"- {c.name} [{c.type.value}] {c.status}{extra}")
        show(c, d+1)
show(root)
assert root.type == SpanType.AGENT and root.parent_id is None
names = {s.name for s in sink.spans}
assert {"agent","plan","search","retrieve"} <= names, names
plan = next(s for s in sink.spans if s.name=="plan")
assert plan.total_tokens == 150 and plan.cost_usd and plan.cost_usd > 0
print("cost computed:", plan.cost_usd)

# ---------------------------------------------------------------- 2. redaction
banner("2. redaction (api key + email scrubbed)")
sink2 = am.configure(redact="scrub", auto=False)
@am.meter
def leaky(secret):
    with am.span("call", input={"prompt": secret}):
        pass
leaky("my key is sk-ABCDEFGHIJKLMNOP1234 and mail bob@acme.com")
call = next(s for s in sink2.spans if s.name=="call")
print("stored input:", call.input)
assert "sk-ABCDEFGHIJKLMNOP1234" not in str(call.input)
assert "bob@acme.com" not in str(call.input)
assert "<openai_key>" in str(call.input) and "<email>" in str(call.input)

# ---------------------------------------------------------------- 3. error span re-raises but is recorded
banner("3. error span")
sink3 = am.configure(auto=False)
@am.meter
def boom():
    with am.span("bad", SpanType.TOOL):
        raise ValueError("nope")
try:
    boom(); assert False, "should have raised"
except ValueError:
    pass
bad = next(s for s in sink3.spans if s.name=="bad")
print("bad span:", bad.status, bad.error)
assert bad.status == "error" and "nope" in bad.error

# ---------------------------------------------------------------- 4. no-op outside a run
banner("4. span outside a run is a no-op")
sink4 = am.configure(auto=False)
with am.span("orphan"):
    pass
print("spans recorded outside run:", len(sink4.spans))
assert len(sink4.spans) == 0

# ---------------------------------------------------------------- 5. double-count merge
banner("5. SDK enriches framework span instead of duplicating")
sink5 = am.configure(auto=False)
from agenticmeter.instrument import openai as oa
@am.meter
def merged():
    owner = tracer.start_span(SpanType.LLM, name="llm")     # pretend LangChain opened it
    tok = ctx.in_framework_llm.set(True)                    # framework now owns the llm span
    fake_resp = types.SimpleNamespace(
        model="gpt-4o",
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=20),
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi"))])
    # SDK patch sees in_framework_llm -> enriches the owner, creates no new span
    oa._enrich_owner(owner.span_id, fake_resp)
    ctx.in_framework_llm.reset(tok)
    tracer.end_span(owner)
merged()
llm_spans = [s for s in sink5.spans if s.type==SpanType.LLM]
print("llm spans recorded:", len(llm_spans), "| tokens on owner:", llm_spans[0].total_tokens,
      "$"+str(llm_spans[0].cost_usd))
assert len(llm_spans) == 1, "should be ONE merged span, not two"
assert llm_spans[0].total_tokens == 30 and llm_spans[0].cost_usd is not None

# ---------------------------------------------------------------- 6. OpenAI patch against a fake module
banner("6. openai patch records a real llm span")
# build a fake openai.resources.chat.completions
m_root = types.ModuleType("openai")
m_res  = types.ModuleType("openai.resources")
m_chat = types.ModuleType("openai.resources.chat")
m_comp = types.ModuleType("openai.resources.chat.completions")
class Completions:
    def create(self, **kw):
        return types.SimpleNamespace(
            model=kw.get("model","gpt-4o-mini"),
            usage=types.SimpleNamespace(prompt_tokens=8, completion_tokens=4),
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="pong"))])
class AsyncCompletions:
    async def create(self, **kw):
        return types.SimpleNamespace(
            model=kw.get("model","gpt-4o-mini"),
            usage=types.SimpleNamespace(prompt_tokens=2, completion_tokens=2),
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="apong"))])
m_comp.Completions = Completions
m_comp.AsyncCompletions = AsyncCompletions
sys.modules.update({"openai":m_root,"openai.resources":m_res,
                    "openai.resources.chat":m_chat,
                    "openai.resources.chat.completions":m_comp})

sink6 = am.configure(auto=True)   # patches our fake openai
@am.meter
def call_llm():
    return Completions().create(model="gpt-4o-mini", messages=[{"role":"user","content":"ping"}])
call_llm()
llm = next(s for s in sink6.spans if s.name=="openai.chat")
print("openai span:", llm.model, llm.total_tokens, "$"+str(llm.cost_usd), "out=", llm.output)
assert llm.total_tokens == 12 and llm.output == "pong" and llm.cost_usd is not None

# async path
async def go():
    return await AsyncCompletions().create(model="gpt-4o-mini", messages=[])
@am.meter
def call_async():
    return asyncio.get_event_loop().run_until_complete(go())
# run async call inside a fresh loop
async def amain():
    run = tracer.start_run("call_async")
    await AsyncCompletions().create(model="gpt-4o-mini", messages=[])
    tracer.end_span(run)
asyncio.run(amain())
aspan = [s for s in sink6.spans if s.name=="openai.chat" and s.total_tokens==4]
print("async openai spans:", len(aspan))
assert aspan

print("\nALL CHECKS PASSED ✅")
