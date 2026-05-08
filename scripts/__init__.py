"""Operator scripts (CLI tools).

Marked as a package so the LangGraph post-fire pipeline (Phase 4) and
unit tests can import the pure translation helpers directly:

    from scripts.translate_brief_vulgarization import translate_brief
    from scripts.translate_brief_panel import translate_panel

The scripts continue to work standalone (``python scripts/foo.py``)
because their top-level ``sys.path.insert`` line is a no-op when
imported from package context (the project root is already on the
import path).
"""
