"""AI itinerary planning (Master Prompt §13-16, §26, §81).

Layout
  domain.py     plain dataclasses for a planned trip (no framework imports)
  rules.py      pure validation + deterministic repair rules
  llm_io.py     prompts and strict parsing of the model's JSON output
  workflow.py   graph spec + LangGraph builder (and a local runner for tests)
  graph.py      the itinerary-generation workflow (nodes + routing)
  deps.py       real ports (geocoding, Amadeus, weather, currency, LLM, DB)
  persistence.py  writes a validated plan + version snapshot atomically
  service.py    PlanningService (auth'd entry point, jobs, idempotency)
"""
