# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅ Yes     |
| < 0.2   | ❌ No      |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in AgenticMeter, email us at:

**security@agenticmeter.ai**

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (optional)

You will receive a response within **48 hours**. We aim to release a patch within **7 days** for confirmed vulnerabilities.

## Scope

In scope:
- The `agenticmeter` Python package (tracer, sinks, instrumentation)
- The local web UI (`agenticmeter ui`)
- The CLI (`agenticmeter runs`, `agenticmeter show`)

Out of scope:
- Third-party libraries AgenticMeter depends on (report those upstream)
- Issues in demo scripts (`research_agent.py`, `react_agent.py`)

## Notes

AgenticMeter is **local-first** — traces are stored in a local SQLite database and never sent to any external server. There is no cloud backend, no authentication layer, and no network calls except the optional GitHub star count fetch on the landing page.
