"""Memory module - IMPLEMENTED (Phase 4, complete).
See schemas.py and service.py (MemoryService: create/create_extracted/
view/edit/delete/disable, all scoped to the owning user).

Two sources now populate memories: user_stated (POST /memory,
by the person themselves) and companion_extracted (automatic,
proposed after a Companion turn by app/modules/companion/
memory_extraction.py, deduped against existing memories, with a
confidence score attached per Blueprint section 37). Both are subject
to the exact same view/edit/delete/disable controls."""
