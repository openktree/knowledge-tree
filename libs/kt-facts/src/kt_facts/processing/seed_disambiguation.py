"""Seed disambiguation — superseded by pending-first pipeline in seed_dedup.py.

Genesis disambiguation (suggest_disambig + route_facts_to_paths) now runs
inside `_promote_and_genesis_disambig()` in seed_dedup.py.

Multiplex-triggered disambiguation (`new_disambig_path` action) runs inside
`_apply_disambig_path()` in seed_dedup.py.

This module is kept as a namespace stub; all logic has moved.
"""
