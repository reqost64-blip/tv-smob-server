from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime
from typing import Any, Optional

from . import account_store as acct
from .database import db


MAX_PREVIEW_ROWS = 5
MT5_FIELD_ALIASES = {
    "time": ("time", "deal_time", "close_time", "closed_at"),
    "position": ("position", "position_id", "position_ticket"),
    "symbol": ("symbol", "mt5_symbol"),
    "type": ("type", "deal_type", "side"),
    "volume": ("volume", "lot", "lots"),
    "price": ("price", "exit_price", "close_price"),
    "s/l": ("s/l", "sl", "stop_loss"),
    "t/p": ("t/p", "tp", "take_profit"),
    "profit": ("profit", "net_profit", "total_profit"),
    "commission": ("commission",),
    "swap": ("swap",),
    "comment": ("comment",),
    "magic": ("magic", "magic_number"),
    "deal": ("deal", "deal_ticket", "ticket"),
    "order": ("order", "order_ticket"),
}
REQUIRED_IMPORT_FIELDS = ("time", "symbol", "type", "price", "profit")


def parse_history_payload(raw_body: bytes, content_type: str = "") -> tuple[list[dict], str]:
    text = raw_body.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return [], "empty"
    if "json" in content_type.lower() or text.startswith(("[", "{")):
        data = json.loads(text)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)], "json"
        if isinstance(data, dict):
            rows = data.get("rows") or data.get("deals") or data.get("data")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)], "json"
            csv_text = data.get("csv") or data.get("csv_text") or data.get("content") or data.get("text")
            if isinstance(csv_text, str):
                return parse_csv_text(csv_text), "csv"
        return [], "json"
    return parse_csv_text(text), "csv"


def parse_csv_text(text: str) -> list[dict]:
    text = text.strip("\ufeff\r\n ")
    if not text:
        return []
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [dict(row) for row in reader if row and any(str(value or "").strip() for value in row.values())]


def build_history_import_preview(
    rows: list[dict],
    *,
    source_name: Optional[str] = None,
    dedupe: bool = True,
    bot_id: Optional[str] = None,
) -> dict:
    normalized_rows: list[dict] = []
    skipped_rows = 0
    missing_fields: dict[str, int] = {}
    for row in rows:
        deal, missing = normalize_history_row(row, source_name=source_name, bot_id=bot_id)
        for field in missing:
            missing_fields[field] = missing_fields.get(field, 0) + 1
        if deal is None:
            skipped_rows += 1
            continue
        normalized_rows.append(deal)

    seen: set[str] = set()
    duplicate_in_upload = 0
    for row in normalized_rows:
        ticket = str(row["deal_ticket"])
        if ticket in seen:
            duplicate_in_upload += 1
        seen.add(ticket)

    existing = existing_deal_tickets([str(row["deal_ticket"]) for row in normalized_rows]) if dedupe else set()
    duplicate_existing = sum(1 for row in normalized_rows if str(row["deal_ticket"]) in existing)
    importable = []
    seen_importable: set[str] = set()
    for row in normalized_rows:
        ticket = str(row["deal_ticket"])
        if dedupe and (ticket in existing or ticket in seen_importable):
            continue
        importable.append(row)
        seen_importable.add(ticket)
    return {
        "total_rows": len(rows),
        "valid_rows": len(normalized_rows),
        "skipped_rows": skipped_rows,
        "symbols": sorted({str(row.get("symbol") or "") for row in normalized_rows if row.get("symbol")}),
        "date_range": date_range(normalized_rows),
        "missing_fields": missing_fields,
        "duplicates_estimate": duplicate_in_upload + duplicate_existing,
        "duplicates_in_upload": duplicate_in_upload,
        "duplicates_existing": duplicate_existing,
        "importable_rows": len(importable),
        "preview_rows": public_preview_rows(importable[:MAX_PREVIEW_ROWS]),
        "_normalized_rows": importable,
    }


def import_history_rows(
    rows: list[dict],
    *,
    dry_run: bool = True,
    source_name: Optional[str] = None,
    dedupe: bool = True,
    bot_id: Optional[str] = None,
) -> dict:
    preview = build_history_import_preview(rows, source_name=source_name, dedupe=dedupe, bot_id=bot_id)
    normalized_rows = preview.pop("_normalized_rows", [])
    saved = 0
    if not dry_run and normalized_rows:
        saved = acct.save_history_deals(bot_id or "", normalized_rows)
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "dedupe": bool(dedupe),
        "source_name": source_name or "history_import",
        "saved_rows": saved,
        **preview,
    }


def normalize_history_row(row: dict, *, source_name: Optional[str], bot_id: Optional[str]) -> tuple[Optional[dict], list[str]]:
    normalized = {_normalize_key(key): value for key, value in (row or {}).items()}
    missing: list[str] = []
    for field in REQUIRED_IMPORT_FIELDS:
        if _field(normalized, field) in (None, ""):
            missing.append(field)
    if missing:
        return None, missing

    symbol = str(_field(normalized, "symbol") or "").strip().upper()
    deal_type = str(_field(normalized, "type") or "").strip().lower()
    side = _side_from_type(deal_type)
    deal_time = normalize_time(_field(normalized, "time"))
    price = parse_number(_field(normalized, "price"))
    profit = parse_number(_field(normalized, "profit"))
    commission = parse_number(_field(normalized, "commission")) or 0.0
    swap = parse_number(_field(normalized, "swap")) or 0.0
    volume = parse_number(_field(normalized, "volume"))
    invalid_fields = []
    if not deal_time:
        invalid_fields.append("time")
    if not symbol:
        invalid_fields.append("symbol")
    if not deal_type:
        invalid_fields.append("type")
    if price is None:
        invalid_fields.append("price")
    if profit is None:
        invalid_fields.append("profit")
    if invalid_fields:
        return None, invalid_fields

    position = clean_id(_field(normalized, "position"))
    order = clean_id(_field(normalized, "order"))
    deal = clean_id(_field(normalized, "deal"))
    ticket = history_ticket(deal=deal, order=order, position=position, symbol=symbol, deal_time=deal_time, deal_type=deal_type, price=price, profit=profit)
    magic = parse_int(_field(normalized, "magic"))
    payload = dict(row)
    payload["import_dedupe_ticket"] = ticket
    return {
        "deal_ticket": ticket,
        "order_ticket": order,
        "position_id": position,
        "symbol": symbol,
        "magic_number": magic,
        "bot_id": bot_id,
        "side": side,
        "entry_type": None,
        "deal_type": deal_type,
        "volume": volume,
        "price": price,
        "profit": profit,
        "commission": commission,
        "swap": swap,
        "net": round(profit + commission + swap, 8),
        "deal_time": deal_time,
        "comment": str(_field(normalized, "comment") or "").strip() or None,
        "source": source_name or "history_import",
        "payload": payload,
    }, []


def history_ticket(*, deal: Optional[str], order: Optional[str], position: Optional[str], symbol: str, deal_time: str, deal_type: str, price: float, profit: float) -> str:
    if deal:
        return str(deal)
    if order or position:
        basis = "|".join(["ids", order or "", position or "", deal_time, deal_type, f"{price:.8f}", f"{profit:.8f}"])
    else:
        basis = "|".join(["fallback", symbol, deal_time, deal_type, f"{price:.8f}", f"{profit:.8f}"])
    return "import_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def existing_deal_tickets(tickets: list[str]) -> set[str]:
    values = sorted({str(ticket) for ticket in tickets if ticket})
    if not values:
        return set()
    found: set[str] = set()
    with db() as conn:
        for i in range(0, len(values), 500):
            chunk = values[i:i + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT deal_ticket FROM history_deals WHERE deal_ticket IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update(str(row["deal_ticket"]) for row in rows)
    return found


def public_preview_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "time": row.get("deal_time"),
            "symbol": row.get("symbol"),
            "type": row.get("deal_type"),
            "volume": row.get("volume"),
            "price": row.get("price"),
            "profit": row.get("profit"),
            "deal_ticket": row.get("deal_ticket"),
        }
        for row in rows
    ]


def date_range(rows: list[dict]) -> dict:
    values = sorted(row.get("deal_time") for row in rows if row.get("deal_time"))
    return {"start": values[0] if values else None, "end": values[-1] if values else None}


def normalize_time(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-")
    candidates = [
        ("%Y.%m.%d %H:%M:%S", text),
        ("%Y.%m.%d %H:%M", text),
        ("%Y-%m-%d %H:%M:%S", text),
        ("%Y-%m-%d %H:%M", text),
        ("%d.%m.%Y %H:%M:%S", text),
        ("%d.%m.%Y %H:%M", text),
    ]
    iso_text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_text)
        return parsed.isoformat() if parsed.tzinfo else parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    for fmt, item in candidates:
        try:
            return datetime.strptime(item, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return text


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^0-9,.\-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> Optional[int]:
    number = parse_number(value)
    return int(number) if number is not None else None


def clean_id(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("\ufeff", "")


def _field(row: dict, canonical: str) -> Any:
    for key in MT5_FIELD_ALIASES[canonical]:
        value = row.get(_normalize_key(key))
        if value is not None and str(value).strip() != "":
            return value
    return None


def _side_from_type(value: str) -> Optional[str]:
    text = str(value or "").lower()
    if "buy" in text:
        return "buy"
    if "sell" in text:
        return "sell"
    return None
