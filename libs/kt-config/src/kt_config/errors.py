class KnowledgeTreeError(Exception):
    """Base exception for Knowledge Tree."""


class NodeNotFoundError(KnowledgeTreeError):
    def __init__(self, node_id: str):
        self.node_id = node_id
        super().__init__(f"Node not found: {node_id}")


class BudgetExhaustedError(KnowledgeTreeError):
    def __init__(self, budget_type: str):
        self.budget_type = budget_type
        super().__init__(f"Budget exhausted: {budget_type}")


class ProviderError(KnowledgeTreeError):
    def __init__(self, provider_id: str, message: str):
        self.provider_id = provider_id
        super().__init__(f"Provider '{provider_id}' error: {message}")


class ModelError(KnowledgeTreeError):
    def __init__(self, model_id: str, message: str):
        self.model_id = model_id
        super().__init__(f"Model '{model_id}' error: {message}")


class DuplicateNodeError(KnowledgeTreeError):
    def __init__(self, concept: str):
        self.concept = concept
        super().__init__(f"Duplicate node for concept: {concept}")


class EmbeddingError(KnowledgeTreeError):
    def __init__(self, message: str):
        super().__init__(f"Embedding error: {message}")


class ConfigurationError(KnowledgeTreeError):
    def __init__(self, message: str):
        super().__init__(f"Configuration error: {message}")
