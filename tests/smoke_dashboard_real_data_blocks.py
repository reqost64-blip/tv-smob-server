from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


def main():
    required = [
        "account()",
        "accountSnapshots",
        "positions()",
        "trades()",
        "stats()",
        "pnl()",
        "bots()",
        "lab()",
        "labHealth()",
        "labRecs()",
        "storage()",
        "system()",
        "health()",
        "bias()",
        "liveBias()",
        "liveBiasAccuracy()",
        "liveBiasCalibration()",
        "signalRows()",
        "signalSources()",
        "signalAccuracy()",
        "No signals processed yet.",
        "No closed trade history found. Import MT5 history.",
        "Import MT5 History",
        "LOW SAMPLE SIZE",
        "history_empty",
        "closed_trades_empty",
        "CONSOLIDATION",
    ]
    missing = [marker for marker in required if marker not in HTML]
    assert not missing, missing

    endpoint_markers = [
        "endpoints.account",
        "endpoints.accountHistory",
        "endpoints.positions",
        "endpoints.trades",
        "endpoints.stats",
        "endpoints.biasLive",
        "endpoints.biasLiveAccuracy",
        "endpoints.biasLiveCalibration",
        "endpoints.lab",
        "endpoints.labRecs",
        "endpoints.labHealth",
        "endpoints.storage",
        "endpoints.signalSources",
        "endpoints.signalAccuracy",
    ]
    missing_endpoints = [marker for marker in endpoint_markers if marker not in HTML]
    assert not missing_endpoints, missing_endpoints
    print({"dashboard_real_data_blocks": "ok"})


if __name__ == "__main__":
    main()
