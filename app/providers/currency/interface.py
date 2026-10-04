"""Currency provider abstraction (Master Blueprint §27-28)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ExchangeRate:
    base: str
    target: str
    rate: float
    fetched_at: datetime
    provider: str


class CurrencyProvider(ABC):
    @abstractmethod
    async def get_latest_rate(self, base: str, target: str) -> ExchangeRate:
        raise NotImplementedError
