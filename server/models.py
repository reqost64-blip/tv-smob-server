from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class WebhookPayload(BaseModel):
    secret: str
    signal_id: str
    symbol: str
    mt5_symbol: str
    timeframe: str
    time: str
    action: Literal["open"]
    side: Literal["buy", "sell"]
    entry: float
    sl: float
    tp_count: Literal[1, 2, 3]
    tp1: Optional[float] = None
    tp1_qty: Optional[float] = None
    tp2: Optional[float] = None
    tp2_qty: Optional[float] = None
    tp3: Optional[float] = None
    tp3_qty: Optional[float] = None
    move_to_be_after_first_tp: bool = False
    be_trigger_tp_id: Optional[str] = None
    lot: float = Field(gt=0)
    magic_number: int


class AckRequest(BaseModel):
    signal_id: str


class ExecutionReport(BaseModel):
    signal_id: str
    ticket: Optional[int] = None
    status: str
    message: Optional[str] = None
    executed_price: Optional[float] = None
    executed_at: Optional[str] = None


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str


class OkResponse(BaseModel):
    ok: bool = True
