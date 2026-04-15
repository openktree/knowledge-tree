"""Test configuration for backend-engine-hybrid-extractor."""

import pytest


def _spacy_model_available() -> bool:
    try:
        import spacy

        spacy.load("en_core_web_lg")
        return True
    except Exception:
        try:
            spacy.load("en_core_web_sm")
            return True
        except Exception:
            return False


requires_spacy_model = pytest.mark.skipif(
    not _spacy_model_available(),
    reason="spaCy model (en_core_web_lg or en_core_web_sm) not installed",
)
