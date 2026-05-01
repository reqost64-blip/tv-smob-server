from .models import WebhookPayload

QTY_TOLERANCE = 0.2


def validate_signal(payload: WebhookPayload) -> str | None:
    """Return error string on failure, None on success."""
    tc = payload.tp_count

    required_fields: list[tuple[str, float | None]] = []
    if tc >= 1:
        required_fields += [("tp1", payload.tp1), ("tp1_qty", payload.tp1_qty)]
    if tc >= 2:
        required_fields += [("tp2", payload.tp2), ("tp2_qty", payload.tp2_qty)]
    if tc >= 3:
        required_fields += [("tp3", payload.tp3), ("tp3_qty", payload.tp3_qty)]

    for name, val in required_fields:
        if val is None:
            return f"Field '{name}' is required when tp_count={tc}"

    tps: list[float] = []
    qty_sum = 0.0
    for i in range(1, tc + 1):
        tp_val = getattr(payload, f"tp{i}")
        qty_val = getattr(payload, f"tp{i}_qty")
        tps.append(tp_val)
        qty_sum += qty_val

    if abs(qty_sum - 100.0) > QTY_TOLERANCE:
        return f"Sum of tp qty ({qty_sum}) must be approximately 100 (tolerance ±{QTY_TOLERANCE})"

    entry = payload.entry
    sl = payload.sl
    side = payload.side

    if side == "buy":
        if sl >= entry:
            return f"For buy: sl ({sl}) must be less than entry ({entry})"
        for i, tp in enumerate(tps, 1):
            if tp <= entry:
                return f"For buy: tp{i} ({tp}) must be greater than entry ({entry})"
    else:
        if sl <= entry:
            return f"For sell: sl ({sl}) must be greater than entry ({entry})"
        for i, tp in enumerate(tps, 1):
            if tp >= entry:
                return f"For sell: tp{i} ({tp}) must be less than entry ({entry})"

    return None
