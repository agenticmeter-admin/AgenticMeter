"""`agenticmeter demo` — a simulated travel agent with the bugs real agents ship
with. No API keys, no network: LLM calls are faked, everything else runs through
the real capture path (@meter, @tool, span) so the output is what you'd see on
your own agent.
"""
from __future__ import annotations

import time

from . import context as ctx
from .cost import cost_usd
from .decorators import meter, span, tool
from .span import SpanType

TRIP = {"from": "SFO", "to": "NRT", "date": "2026-12-10"}


def _llm(model, prompt_tokens, completion_tokens, messages):
    with span("openai.chat", SpanType.LLM, input=messages, model=model,
              prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
              cost_usd=cost_usd(model, prompt_tokens, completion_tokens)):
        pass


@tool
def search_flights(origin, dest, date):
    time.sleep(0.02)
    return [{"flight": "NH7", "price": 912}]


@tool
def fetch_hotel_prices(city):
    time.sleep(0.03)
    raise TimeoutError("hotel API did not respond in 30s")


@meter(name="travel_agent")
def travel_agent(task):
    _llm("gpt-4o", 1_200, 150, [{"role": "user", "content": task}])

    # bug: the "no results yet" check never sees the cached result, so it loops
    for _ in range(4):
        search_flights(TRIP["from"], TRIP["to"], TRIP["date"])

    # bug: failure is swallowed and the agent quietly uses stale prices
    try:
        fetch_hotel_prices("Tokyo")
    except TimeoutError:
        pass

    with span("vector_search", SpanType.RETRIEVAL, input={"q": "tokyo itinerary", "k": 50}):
        time.sleep(0.6)

    _llm("gpt-4o", 6_000, 300, [{"role": "user", "content": "rank options"}])

    # bug: same summarization requested twice
    for _ in range(2):
        _llm("gpt-4o-mini", 900, 80, [{"role": "user", "content": "summarize flights"}])

    # bug: full history re-sent on the final call
    _llm("gpt-4o", 18_000, 600, [{"role": "user", "content": "write itinerary"}])
    return ctx.current_trace.get()


def run(sink):
    from .tracer import tracer
    tracer.configure(sink=sink, summary=False)
    trace_id = travel_agent("Plan a 5-day trip to Tokyo in December under $3,000")
    return sink.get_trace(trace_id)
