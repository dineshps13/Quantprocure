from pathlib import Path

import pandas as pd

from bidding_system.core import analyze_bids, generate_invitations


def test_generate_invitations(tmp_path: Path) -> None:
    suppliers = pd.DataFrame(
        [
            {"supplier_id": "S001", "supplier_name": "Alpha", "email": "a@example.com"},
            {"supplier_id": "S002", "supplier_name": "Beta", "email": "b@example.com"},
        ]
    )
    suppliers_path = tmp_path / "suppliers.xlsx"
    output_path = tmp_path / "invites.csv"
    suppliers.to_excel(suppliers_path, index=False)

    invitations = generate_invitations(suppliers_path, output_path, rfq_id="RFQ-1")

    assert len(invitations) == 2
    assert output_path.exists()
    assert set(["rfq_id", "status"]).issubset(invitations.columns)


def test_analyze_bids_selects_lowest_cost(tmp_path: Path) -> None:
    suppliers = pd.DataFrame(
        [
            {"supplier_id": "S001", "supplier_name": "Alpha", "email": "a@example.com"},
            {"supplier_id": "S002", "supplier_name": "Beta", "email": "b@example.com"},
        ]
    )
    responses = pd.DataFrame(
        [
            {"supplier_id": "S001", "unit_price": 12.5, "currency": "USD"},
            {"supplier_id": "S002", "unit_price": 10.0, "currency": "USD"},
        ]
    )

    suppliers_path = tmp_path / "suppliers.xlsx"
    responses_path = tmp_path / "responses.xlsx"
    output_analysis_path = tmp_path / "analysis.csv"

    suppliers.to_excel(suppliers_path, index=False)
    responses.to_excel(responses_path, index=False)

    best, analysis = analyze_bids(suppliers_path, responses_path, output_analysis_path)

    assert best["supplier_id"] == "S002"
    assert analysis.iloc[0]["unit_price"] == 10.0
    assert output_analysis_path.exists()
