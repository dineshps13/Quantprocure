from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

import pandas as pd

REQUIRED_SUPPLIER_COLUMNS = ["supplier_id", "supplier_name", "email"]
REQUIRED_RESPONSE_COLUMNS = ["supplier_id", "unit_price"]


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported file type '{suffix}' for {path}")


def _validate_columns(df: pd.DataFrame, required_columns: list[str], label: str) -> None:
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"Missing required columns in {label}: {missing_text}")


def generate_invitations(suppliers_path: Path, output_path: Path, rfq_id: str) -> pd.DataFrame:
    suppliers = _read_table(suppliers_path)
    _validate_columns(suppliers, REQUIRED_SUPPLIER_COLUMNS, "suppliers")

    invitations = suppliers[REQUIRED_SUPPLIER_COLUMNS].copy()
    invitations["rfq_id"] = rfq_id
    invitations["invited_at_utc"] = datetime.now(timezone.utc).isoformat()
    invitations["status"] = "invited"

    invitations.to_csv(output_path, index=False)
    return invitations


def analyze_bids(
    suppliers_path: Path,
    responses_path: Path,
    output_analysis_path: Path,
) -> Tuple[pd.Series, pd.DataFrame]:
    suppliers = _read_table(suppliers_path)
    responses = _read_table(responses_path)

    _validate_columns(suppliers, REQUIRED_SUPPLIER_COLUMNS, "suppliers")
    _validate_columns(responses, REQUIRED_RESPONSE_COLUMNS, "responses")

    responses = responses.copy()
    responses["unit_price"] = pd.to_numeric(responses["unit_price"], errors="coerce")

    if "currency" not in responses.columns:
        responses["currency"] = "USD"

    merged = responses.merge(
        suppliers[["supplier_id", "supplier_name", "email"]],
        on="supplier_id",
        how="left",
    )

    valid = merged[merged["unit_price"].notna() & (merged["unit_price"] > 0)].copy()
    if valid.empty:
        raise ValueError("No valid responses with positive unit_price were found.")

    analysis = valid.sort_values(["unit_price", "supplier_id"], ascending=[True, True]).reset_index(drop=True)
    analysis["rank"] = analysis["unit_price"].rank(method="dense", ascending=True).astype(int)

    best_supplier = analysis.iloc[0]
    analysis.to_csv(output_analysis_path, index=False)
    return best_supplier, analysis
