# Quantprocure Bidding System (Purchase Manager Starter)

This repository now includes a practical Python CLI workflow for a purchase manager to:

1. Load suppliers from an Excel sheet (with email addresses).
2. Prepare invitation records for RFQ/RFP bidding.
3. Collect and analyze supplier responses.
4. Select the lowest-cost supplier automatically.

## Quick Start

### 1) Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Prepare your supplier Excel file

Create an Excel file (e.g. `suppliers.xlsx`) with at least these columns:

- `supplier_id`
- `supplier_name`
- `email`

Example:

| supplier_id | supplier_name   | email                |
|-------------|------------------|----------------------|
| S001        | Alpha Metals     | sales@alpha.com      |
| S002        | Beta Components  | rfq@beta.com         |

### 3) Generate invitation list from suppliers

```bash
python -m bidding_system invite \
  --suppliers suppliers.xlsx \
  --output invitations.csv \
  --rfq-id RFQ-2026-001
```

This creates `invitations.csv` with invitation timestamp and status (`invited`).

### 4) Prepare bid responses file

Create `responses.xlsx` (or `.csv`) with columns:

- `supplier_id`
- `unit_price`
- `currency` (optional, default `USD`)
- `lead_time_days` (optional)
- `notes` (optional)

### 5) Analyze bids and choose the lowest-cost supplier

```bash
python -m bidding_system analyze \
  --suppliers suppliers.xlsx \
  --responses responses.xlsx \
  --output-analysis bid_analysis.csv
```

The tool prints the selected supplier and writes full analysis to CSV.

## Notes

- Current selection rule: **lowest `unit_price` wins**.
- Responses with missing or non-positive price are excluded.
- You can extend this later to include quality/lead-time weighting.
