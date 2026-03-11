from datetime import date

from src.adapters import InMemoryAuditLogger, InMemoryCoupaClient, InMemorySupplierRepository, StaticBidProvider
from src.procurement_agent import Bid, ProcurementAgent, Requisition, Supplier


def test_awards_lowest_compliant_bid_and_updates_coupa():
    coupa = InMemoryCoupaClient(
        requisitions={
            "REQ-1": Requisition(
                requisition_id="REQ-1",
                requester_email="buyer@example.com",
                delivery_location="new york",
                category_name="IT Hardware",
                item_name="Laptop",
                quantity=10,
                need_by_date=date(2026, 1, 10),
            )
        }
    )
    suppliers = InMemorySupplierRepository(
        suppliers=[
            Supplier("S1", "Alpha Tech", ["IT Hardware"], ["New York"]),
            Supplier("S2", "Budget Machines", ["IT Hardware"], ["New York"]),
        ]
    )
    bids = StaticBidProvider(
        bids_by_supplier_id={
            "S1": Bid("S1", "Alpha Tech", item_price=1000, shipping_cost=20, tax=50, lead_time_days=4),
            "S2": Bid("S2", "Budget Machines", item_price=950, shipping_cost=40, tax=45, lead_time_days=6),
        }
    )
    audit = InMemoryAuditLogger()

    agent = ProcurementAgent(coupa, suppliers, bids, audit)
    result = agent.process_requisition("REQ-1")

    assert result.status == "awarded"
    assert result.details["winner"] == "Budget Machines"
    assert coupa.updates["REQ-1"]["winning_supplier_name"] == "Budget Machines"
    assert coupa.updates["REQ-1"]["awarded_total_cost"] == 1035


def test_routes_manual_fix_when_required_fields_missing():
    coupa = InMemoryCoupaClient(
        requisitions={
            "REQ-2": Requisition(
                requisition_id="REQ-2",
                requester_email=None,
                delivery_location="Bangalore",
                category_name="Office Supplies",
                item_name=None,
            )
        }
    )
    suppliers = InMemorySupplierRepository(suppliers=[])
    bids = StaticBidProvider(bids_by_supplier_id={})
    audit = InMemoryAuditLogger()

    agent = ProcurementAgent(coupa, suppliers, bids, audit)
    result = agent.process_requisition("REQ-2")

    assert result.status == "manual_fix_required"
    assert set(result.details["missing_fields"]) == {"requester_email", "item_name"}


def test_returns_no_compliant_bids_when_all_bids_are_non_compliant():
    coupa = InMemoryCoupaClient(
        requisitions={
            "REQ-3": Requisition(
                requisition_id="REQ-3",
                requester_email="buyer@example.com",
                delivery_location="Austin",
                category_name="Facilities",
                item_name="Generator",
            )
        }
    )
    suppliers = InMemorySupplierRepository(
        suppliers=[Supplier("S3", "Power Corp", ["Facilities"], ["Austin"])]
    )
    bids = StaticBidProvider(
        bids_by_supplier_id={
            "S3": Bid(
                "S3",
                "Power Corp",
                item_price=5000,
                shipping_cost=300,
                tax=250,
                lead_time_days=12,
                compliance_flags=["Missing compliance certificate"],
            )
        }
    )
    audit = InMemoryAuditLogger()

    agent = ProcurementAgent(coupa, suppliers, bids, audit)
    result = agent.process_requisition("REQ-3")

    assert result.status == "no_compliant_bids"
    assert result.details["received_bids"] == 1
