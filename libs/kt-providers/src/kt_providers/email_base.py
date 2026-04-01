"""Abstract base class for email providers."""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class EmailMessage(BaseModel):
    """A single outbound email."""

    to: str
    subject: str
    html: str
    from_address: str | None = None  # override default sender


class EmailProvider(ABC):
    """Abstract base class for email providers."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider."""
        ...

    @abstractmethod
    async def send_email(self, message: EmailMessage) -> None:
        """Send a single email. Raises on failure."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the provider is configured and reachable."""
        ...
