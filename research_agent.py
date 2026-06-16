"""AI Research Agent — fully instrumented with AgenticMeter v2.

Researches any topic by:
  1. Searching ArXiv for recent papers  (real API, no key needed)
  2. Fetching paper abstracts on demand
  3. Saving findings as structured notes
  4. Synthesising a final research report

LLM: Groq (free tier, cloud-hosted Llama 3)
Get a free API key at: https://console.groq.com

Usage:
    export GROQ_API_KEY=gsk_...
    python research_agent.py "multi-agent LLM systems"
    python research_agent.py "prompt injection attacks"

Then inspect:
    agenticmeter runs
    agenticmeter show
    agenticmeter ui
"""
from __future__ import annotations

import os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

from openai import OpenAI
import agenticmeter as am
from agenticmeter.span import SpanType
from agenticmeter.tracer import tracer
from agenticmeter import cost as am_cost

# ── config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # set via: export GROQ_API_KEY=gsk_...

MODEL     = os.getenv("RESEARCH_MODEL", "llama-3.3-70b-versatile")  # free on Groq
MAX_STEPS = int(os.getenv("MAX_STEPS", "12"))

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)

# Register Groq model pricing (USD per 1M tokens, approx)
am_cost.set_prices({
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant":    (0.05, 0.08),
    "mixtral-8x7b-32768":      (0.24, 0.24),
})

# ── in-memory note store (lives for one run) ──────────────────────────────────
_notes: dict[str, str] = {}

# ── tools ─────────────────────────────────────────────────────────────────────

@am.tool
def arxiv_search(query: str) -> str:
    """Search ArXiv for recent papers. Returns titles + short abstracts."""
    encoded = urllib.parse.quote(query.strip())
    url = (f"http://export.arxiv.org/api/query"
           f"?search_query=all:{encoded}&max_results=4"
           f"&sortBy=submittedDate&sortOrder=descending")
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            xml_data = r.read().decode("utf-8")
    except Exception as e:
        return f"ArXiv request failed: {e}"

    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_data)
    entries = root.findall("a:entry", ns)
    if not entries:
        return "No papers found for that query."

    lines = []
    for e in entries:
        title    = (e.findtext("a:title",   namespaces=ns) or "").strip().replace("\n", " ")
        summary  = (e.findtext("a:summary", namespaces=ns) or "").strip().replace("\n", " ")
        link     = (e.findtext("a:id",      namespaces=ns) or "").strip()
        arxiv_id = link.split("/abs/")[-1] if "/abs/" in link else link
        lines.append(f"ID: {arxiv_id}\nTitle: {title}\nAbstract: {summary[:280]}...")
    return "\n\n---\n\n".join(lines)


@am.tool
def fetch_paper(arxiv_id: str) -> str:
    """Fetch the full abstract of one ArXiv paper by its ID (e.g. 2310.06825)."""
    arxiv_id = arxiv_id.strip().strip("'\"")
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            xml_data = r.read().decode("utf-8")
    except Exception as e:
        return f"Fetch failed: {e}"

    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_data)
    entry = root.find("a:entry", ns)
    if entry is None:
        return f"Paper '{arxiv_id}' not found."

    title   = (entry.findtext("a:title",   namespaces=ns) or "").strip().replace("\n", " ")
    summary = (entry.findtext("a:summary", namespaces=ns) or "").strip().replace("\n", " ")
    authors = [a.findtext("a:name", namespaces=ns) for a in entry.findall("a:author", ns)]
    pub     = (entry.findtext("a:published", namespaces=ns) or "")[:10]
    return (f"Title: {title}\n"
            f"Authors: {', '.join(authors[:4])}\n"
            f"Published: {pub}\n"
            f"Abstract: {summary}")


@am.tool
def save_note(note: str) -> str:
    """Save a research finding. Format: 'Title: content of the finding'."""
    if ":" in note:
        title, content = note.split(":", 1)
        key = title.strip()[:40]
        _notes[key] = content.strip()
    else:
        key = f"finding_{len(_notes) + 1}"
        _notes[key] = note.strip()
    return f"Saved note '{key}'. Total notes: {len(_notes)}."


@am.tool
def get_notes(query: str = "") -> str:
    """Return all saved research notes."""
    if not _notes:
        return "No notes saved yet."
    return "\n\n".join(f"[{k}]\n{v}" for k, v in _notes.items())


TOOLS = {
    "arxiv_search": arxiv_search,
    "fetch_paper":  fetch_paper,
    "save_note":    save_note,
    "get_notes":    get_notes,
}

# ── LLM call with AgenticMeter tracing ───────────────────────────────────────

def llm_call(messages: list[dict], step_name: str = "llm") -> str:
    """Call Groq via OpenAI-compatible API. Manually creates an LLM span for tracing."""
    s = tracer.start_span(SpanType.LLM, name=step_name, model=MODEL, input=messages)
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        pt   = resp.usage.prompt_tokens
        ct   = resp.usage.completion_tokens
        tracer.end_span(s, output=text, prompt_tokens=pt, completion_tokens=ct,
                        cost_usd=am_cost.cost_usd(MODEL, pt, ct))
        return text
    except Exception as e:
        tracer.end_span(s, status="error", error=repr(e))
        raise


# ── system prompt ─────────────────────────────────────────────────────────────

SYSTEM = """You are an expert AI research assistant. Research topics thoroughly using tools.

Available tools:
  arxiv_search(query)   - search ArXiv for recent papers on a topic
  fetch_paper(arxiv_id) - get full abstract of one paper by its ID
  save_note(note)       - save a key finding. Format: "Title: explanation"
  get_notes()           - review all notes saved so far

STRICT output format — one action per reply:

  Thought: <your reasoning>
  Action: <tool_name>
  Action Input: <input string, or empty for get_notes>

When ready to answer, output ONLY:

  Final Answer: <comprehensive research summary>

Strategy:
1. Run 2-3 arxiv_search calls with different queries
2. fetch_paper on the most relevant ones
3. save_note after each important finding
4. get_notes to review before writing Final Answer
"""

# ── ReAct loop ────────────────────────────────────────────────────────────────

_ACT = re.compile(r"Action:\s*(\w+)", re.I)
_INP = re.compile(r"Action Input:\s*(.*?)(?:\n(?:Thought|Action|Final)|$)", re.I | re.S)
_FIN = re.compile(r"Final Answer:\s*(.+)", re.I | re.S)


@am.meter
def research_agent(topic: str) -> str:
    """Root AGENT span — wraps the entire research run."""
    _notes.clear()

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": f"Research this topic thoroughly: {topic}"},
    ]

    for step in range(MAX_STEPS):
        print(f"\n  [step {step + 1}/{MAX_STEPS}] thinking...", end="", flush=True)

        text = llm_call(messages, step_name=f"reason_{step + 1}")
        messages.append({"role": "assistant", "content": text})

        fin = _FIN.search(text)
        if fin:
            print(" → Final Answer")
            return fin.group(1).strip()

        act_m = _ACT.search(text)
        inp_m = _INP.search(text)

        if not act_m:
            print(" → (no action, reprompting)")
            messages.append({
                "role": "user",
                "content": "Please use 'Action:' and 'Action Input:' format, or write 'Final Answer:'."
            })
            continue

        tool_name  = act_m.group(1).strip()
        tool_input = (inp_m.group(1).strip().strip("\"'") if inp_m else "").strip()

        print(f" → {tool_name}({tool_input[:60]}{'…' if len(tool_input) > 60 else ''})")

        if tool_name not in TOOLS:
            obs = f"Unknown tool '{tool_name}'. Available: {', '.join(TOOLS)}"
        else:
            try:
                obs = str(TOOLS[tool_name](tool_input))
            except Exception as e:
                obs = f"Tool error: {e}"

        messages.append({"role": "user", "content": f"Observation:\n{obs[:3000]}"})

    return "Max steps reached.\n\n" + get_notes()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not GROQ_API_KEY:
        print("ERROR: Set your Groq API key first:")
        print("  export GROQ_API_KEY=gsk_...")
        print("  Get a free key at: https://console.groq.com")
        sys.exit(1)

    topic = " ".join(sys.argv[1:]) or "multi-agent LLM systems and coordination"

    print(f"\n{'='*60}")
    print(f"  AgenticMeter Research Agent  (powered by Groq)")
    print(f"  Model : {MODEL}")
    print(f"  Topic : {topic}")
    print(f"{'='*60}")

    sink = am.configure(sink="sqlite", summary="auto", auto=False)

    result = research_agent(topic)

    print(f"\n{'='*60}")
    print("  RESEARCH REPORT")
    print(f"{'='*60}")
    print(result)
    print(f"\n{'='*60}")
    runs = sink.list_runs(limit=1)
    span_count = runs[0]["steps"] if runs else 0
    print(f"  Spans recorded : {span_count}")
    print(f"  View traces    : agenticmeter ui")
    print(f"  CLI summary    : agenticmeter show")
    print(f"{'='*60}\n")
