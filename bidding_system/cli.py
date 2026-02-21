import argparse
from pathlib import Path

from .core import analyze_bids, generate_invitations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bidding-system",
        description="Invite suppliers from Excel and select lowest-cost bid.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    invite_parser = subparsers.add_parser(
        "invite", help="Generate invitation records from supplier file."
    )
    invite_parser.add_argument("--suppliers", required=True, help="Path to supplier Excel/CSV file")
    invite_parser.add_argument("--output", required=True, help="Path to write invitation CSV")
    invite_parser.add_argument("--rfq-id", required=True, help="RFQ identifier")

    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze bid responses and choose lowest-cost supplier."
    )
    analyze_parser.add_argument("--suppliers", required=True, help="Path to supplier Excel/CSV file")
    analyze_parser.add_argument("--responses", required=True, help="Path to responses Excel/CSV file")
    analyze_parser.add_argument("--output-analysis", required=True, help="Path to write analysis CSV")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "invite":
        invitations = generate_invitations(
            suppliers_path=Path(args.suppliers),
            output_path=Path(args.output),
            rfq_id=args.rfq_id,
        )
        print(f"Invitations generated: {len(invitations)} -> {args.output}")
        return

    if args.command == "analyze":
        best_supplier, analysis = analyze_bids(
            suppliers_path=Path(args.suppliers),
            responses_path=Path(args.responses),
            output_analysis_path=Path(args.output_analysis),
        )
        print("Selected supplier (lowest cost):")
        print(
            f"- supplier_id={best_supplier['supplier_id']}, "
            f"supplier_name={best_supplier['supplier_name']}, "
            f"unit_price={best_supplier['unit_price']}, "
            f"currency={best_supplier['currency']}"
        )
        print(f"Analysis rows: {len(analysis)} -> {args.output_analysis}")
        return

    parser.error("Unknown command")
