"""A real ReAct tool-loop agent, instrumented by AgenticMeter.

Runs on any OpenAI-compatible endpoint — including local Ollama:
    AGENT_BASE_URL=http://localhost:11434/v1  AGENT_MODEL=llama3.2  python react_agent.py "..."

Wiring (as chosen):
  - LLM calls: AUTO-instrumented (am.configure(auto=True) patches the openai SDK)
  - Tools:     @am.meter.tool  (raw ReAct loop has no framework to hook)
  - The whole run: @am.meter
"""
import os
import re
import sys

import agenticmeter as am
from openai import OpenAI

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:11434/v1")
API_KEY  = os.getenv("AGENT_API_KEY", "ollama")          # Ollama ignores it
MODEL = os.getenv("AGENT_MODEL", "mistral")   # was "llama3.2"
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "8"))
client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

SYSTEM = """You are a helpful agent that answers using tools.
Use EXACTLY this format, one step at a time:

Thought: <your reasoning>
Action: <one of: search, calculator>
Action Input: <input to the tool>

After you see an Observation, continue. When you know the answer, reply:

Final Answer: <answer>
"""

# ---- tools (captured as tool spans) ------------------------------------
_KB = {
    "population of tokyo": "Tokyo's metro population is about 37 million people.",
    "capital of japan": "Tokyo is the capital of Japan.",
}

@am.meter.tool
def search(query: str) -> str:
    q = query.strip().lower()
    for k, v in _KB.items():
        if k in q:
            return v
    return "No results found."

@am.meter.tool
def calculator(expression: str) -> str:
    if not re.fullmatch(r"[\d\s\.\+\-\*\/\(\)]+", expression or ""):
        return "Invalid expression."
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"Error: {e}"

TOOLS = {"search": search, "calculator": calculator}

# ---- ReAct loop --------------------------------------------------------
_ACT = re.compile(r"Action:\s*(\w+)", re.I)
_INP = re.compile(r"Action Input:\s*(.+)", re.I)
_FIN = re.compile(r"Final Answer:\s*(.+)", re.I | re.S)

@am.meter
def run_agent(question: str) -> str:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": question}]
    for step in range(MAX_STEPS):
        resp = client.chat.completions.create(model=MODEL, messages=messages,
                                              temperature=0)
        text = resp.choices[0].message.content or ""
        messages.append({"role": "assistant", "content": text})

        fin = _FIN.search(text)
        if fin:
            return fin.group(1).strip()

        act, inp = _ACT.search(text), _INP.search(text)
        if not act:
            messages.append({"role": "user",
                             "content": "Please use the Action / Action Input format, "
                                        "or give a Final Answer."})
            continue
        tool = act.group(1).lower()
        arg = inp.group(1).strip() if inp else ""
        result = TOOLS[tool](arg) if tool in TOOLS else f"Unknown tool '{tool}'."
        messages.append({"role": "user", "content": f"Observation: {result}"})
    return "Stopped: hit max steps."

# ---- main --------------------------------------------------------------
if __name__ == "__main__":
    sink = os.getenv("AGENT_SINK", "sqlite")
    am.configure(sink=sink, summary="auto", auto=True)   # auto=True patches openai

    # confirm the SDK patch actually attached
    from openai.resources.chat import completions
    patched = getattr(completions.Completions.create, "_am_patched", False)
    print(f"[agent] model={MODEL} via {BASE_URL} | openai patch active: {patched}\n")

    question = " ".join(sys.argv[1:]) or "What is the population of Tokyo, in millions?"
    answer = run_agent(question)
    print(f"\n[agent] FINAL: {answer}")
