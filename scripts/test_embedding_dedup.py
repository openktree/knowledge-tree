"""Redirect — moved to scripts/seed_dedup/run_embedding_sim.py"""
# This script has been moved to the organized experiment suite.
# Run: uv run --project libs/kt-models python scripts/seed_dedup/run_embedding_sim.py

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "seed_dedup"))
from run_embedding_sim import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main())
