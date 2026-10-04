"""Companion module - IMPLEMENTED.

A turn is a LangGraph workflow (graph.py): classify_intent -> check_access ->
load_context -> agent (or deny). See:
  intents.py       the intent taxonomy (Master Prompt section 83), rule-based
                   classification, per-intent tool allow-lists, the feature flag
                   each intent needs, and which context each intent loads
  context.py       compact prompt/context assembly (section 36)
  graph.py         the workflow itself, depending only on injected ports
  service.py       CompanionService: persistence, ports, AI-usage metering,
                   summarization, memory extraction (LLM provider injected)
  tools.py         the tool system (sections 39-40): lookups, propose_* changes,
                   propose_itinerary_revision, a non-destructive checkpoint; each
                   tool re-verifies authorization server-side
  change_service.py  PendingChangeService: propose -> confirm/reject -> apply, for
                   single-item edits AND whole-itinerary revisions (Principle 2:
                   "AI proposes, systems verify, users decide")
  summarization.py / memory_extraction.py / voice_service.py  as before (the
                   first two run on the cheap "fast" model tier).
"""
