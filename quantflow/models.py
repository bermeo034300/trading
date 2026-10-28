"""Estructuras de datos compartidas."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class Trade:
    symbol: str
    ts: int          # epoch en milisegundos
    price: float
    qty: float
    is_buy: bool     # True = el agresor compró (levantó el ask)


@dataclass(slots=True)
class BookSnapshot:
    symbol: str
    ts: int
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


@dataclass
class TradingViewAlert:
    symbol: str
    side: str        # "long" | "short" | "flat"
    setup: str
    price: float | None
    timeframe: str | None
    ts: int


@dataclass
class Signal:
    symbol: str
    ts: int
    setup: str
    side: str                 # "long" | "short"
    entry: float
    stop: float
    target: float
    rr: float
    probability: float        # probabilidad calibrada
    probability_model: float  # salida del modelo antes de calibrar
    expected_value_r: float
    accepted: bool
    features: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
