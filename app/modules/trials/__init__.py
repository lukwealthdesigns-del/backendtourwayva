"""Trials module - IMPLEMENTED (Phase 6).
See schemas.py and service.py (TrialService: admin-configurable
global TrialConfig - enabled/disabled, duration, included features -
plus per-user one-time UserTrial grants, snapshotted from the config
at start time so later config edits don't retroactively change a
trial already in progress)."""
