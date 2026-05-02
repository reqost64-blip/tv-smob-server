from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class WebhookPayload(BaseModel):
    version: Optional[str] = None
    secret: str
    source: Optional[str] = None
    signal_id: str
    parent_signal_id: Optional[str] = None
    symbol: Optional[str] = None
    mt5_symbol: Optional[str] = None
    timeframe: str
    time: str
    action: Literal["open", "close"]
    side: Literal["buy", "sell"]
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp_count: Optional[Literal[1, 2, 3]] = None
    tp1: Optional[float] = None
    tp1_qty: Optional[float] = None
    tp2: Optional[float] = None
    tp2_qty: Optional[float] = None
    tp3: Optional[float] = None
    tp3_qty: Optional[float] = None
    move_to_be_after_first_tp: bool = False
    be_trigger_tp_id: Optional[str] = None
    lot: Optional[float] = Field(default=None, gt=0)
    magic_number: Optional[int] = None
    reason: Optional[str] = None
    close_price: Optional[float] = None


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


class SettingsChangeRequest(BaseModel):
    key: str
    value: Any
    secret: Optional[str] = None


class PendingApproval(BaseModel):
    approval_id: str
    chat_id: str
    command_text: str
    parsed_action: str
    old_value: Optional[str] = None
    new_value: str
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    created_at: Optional[str] = None
    expires_at: str


class NaturalLanguageCommandResult(BaseModel):
    intent: Literal[
        "change_setting",
        "pause_trading",
        "resume_trading",
        "show_settings",
        "show_status",
        "show_last_trade",
        "unknown",
    ]
    symbol: Optional[Literal["XAUUSD", "NAS100", "DJ30", "US500", "BTCUSD"]] = None
    setting_key: Optional[str] = None
    operation: Optional[Literal["set", "increase_percent", "decrease_percent", "enable", "disable"]] = None
    value: Optional[float | bool | str] = None
    requires_confirmation: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
