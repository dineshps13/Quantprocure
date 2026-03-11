from datetime import date

from src.adapters import InMemoryAuditLogger, InMemoryCoupaClient, InMemorySupplierRepository, StaticBidProvider
from src.procurement_agent import Bid, ProcurementAgent, Requisition, Supplier


if __name__ == "__main__":
    coupa = InMemoryCoupaClient(
        requisitions={
            "REQ-1001": Requisition(
                requisition_id="REQ-1001",
                requester_email="user@company.com",
                delivery_location="hyderabad",
                category_name="IT Hardware",
                item_name="Business Laptop",
                make_or_brand="Dell",
                quantity=25,
                need_by_date=date(2026, 2, 1),
            )
        }
    )

    supplier_repo = InMemorySupplierRepository(
        suppliers=[
            Supplier("SUP-1", "TechNova", ["IT Hardware"], ["Hyderabad"]),
            Supplier("SUP-2", "LowCost Systems", ["IT Hardware"], ["Hyderabad"]),
        ]
    )

    bid_provider = StaticBidProvider(
        bids_by_supplier_id={
            "SUP-1": Bid("SUP-1", "TechNova", 820.0, 15.0, 60.0, 7),
            "SUP-2": Bid("SUP-2", "LowCost Systems", 790.0, 30.0, 62.0, 9),
        }
    )

    audit = InMemoryAuditLogger()
    agent = ProcurementAgent(coupa, supplier_repo, bid_provider, audit)

    outcome = agent.process_requisition("REQ-1001")
    print(outcome)
    print(coupa.updates["REQ-1001"])
