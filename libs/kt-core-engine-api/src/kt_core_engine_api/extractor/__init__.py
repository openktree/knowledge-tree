"""Entity-extractor contract: ABC + types + name validator."""

from kt_core_engine_api.extractor.extractor import EntityExtractor, ExtractedEntity
from kt_core_engine_api.extractor.validation import is_valid_entity_name

__all__ = [
    "EntityExtractor",
    "ExtractedEntity",
    "is_valid_entity_name",
]
