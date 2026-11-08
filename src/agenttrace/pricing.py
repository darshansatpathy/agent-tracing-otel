"""Model prices, per million tokens, with the date each price took effect.

Cost is computed where the call happens, from the tokens the provider reported, so a trace carries its
own cost and dashboards never join against a price table. A model with no price is reported as
unpriced rather than as free.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="Model name or glob, e.g. small-model or vendor/large-*")
    input: float = Field(ge=0, description="USD per million input tokens")
    output: float = Field(ge=0)
    cache_read: float | None = Field(None, ge=0, description="USD per million cached input tokens read; defaults to input")
    cache_write: float | None = Field(None, ge=0)
    effective_from: dt.date = dt.date(2000, 1, 1)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, o: Usage) -> Usage:
        return Usage(
            self.input_tokens + o.input_tokens,
            self.output_tokens + o.output_tokens,
            self.cache_read_tokens + o.cache_read_tokens,
            self.cache_write_tokens + o.cache_write_tokens,
        )


class PriceTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    prices: list[Price]

    @classmethod
    def load(cls, path: Path) -> PriceTable:
        return cls.model_validate(yaml.safe_load(path.read_text()))

    def find(self, model: str, on: dt.date | None = None) -> Price | None:
        """The latest price in effect on the date, preferring an exact name over a glob."""
        on = on or dt.date.today()
        live = [p for p in self.prices if p.effective_from <= on and fnmatch.fnmatchcase(model, p.model)]
        if not live:
            return None
        return max(live, key=lambda p: (p.model == model, p.effective_from))

    def cost(self, model: str, u: Usage, on: dt.date | None = None) -> float | None:
        p = self.find(model, on)
        if p is None:
            return None
        # Providers report cached tokens as part of input tokens; bill them at the cache rate instead.
        fresh = max(0, u.input_tokens - u.cache_read_tokens - u.cache_write_tokens)
        total = (
            fresh * p.input
            + u.cache_read_tokens * (p.cache_read if p.cache_read is not None else p.input)
            + u.cache_write_tokens * (p.cache_write if p.cache_write is not None else p.input)
            + u.output_tokens * p.output
        )
        return total / 1_000_000
