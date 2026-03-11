from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from src.procurement_agent import Bid, BidProvider, CoupaClient, NormalizedFields, Requisition, Supplier, SupplierRepository


@dataclass
class InMemoryCoupaClient(CoupaClient):
    requisitions: Dict[str, Requisition]
    updates: Dict[str, Dict[str, object]] = field(default_factory=dict)

    def fetch_requisition(self, requisition_id: str) -> Requisition:
        return self.requisitions[requisition_id]

    def update_requisition(self, requisition_id: str, payload: Dict[str, object]) -> None:
        self.updates[requisition_id] = payload


@dataclass
class InMemorySupplierRepository(SupplierRepository):
    suppliers: List[Supplier]

    def search(self, category: str, location: str) -> List[Supplier]:
        category_l = category.lower()
        location_l = location.lower()
        return [
            supplier
            for supplier in self.suppliers
            if category_l in [c.lower() for c in supplier.categories]
            and location_l in [l.lower() for l in supplier.serviceable_locations]
            and supplier.compliant
        ]


@dataclass
class StaticBidProvider(BidProvider):
    bids_by_supplier_id: Dict[str, Bid]

    def request_bids(self, requisition: NormalizedFields, suppliers: List[Supplier]) -> List[Bid]:
        return [self.bids_by_supplier_id[s.supplier_id] for s in suppliers if s.supplier_id in self.bids_by_supplier_id]


@dataclass
class InMemoryAuditLogger:
    events: List[Dict[str, object]] = field(default_factory=list)

    def log(self, event: str, data: Dict[str, object]) -> None:
        self.events.append({"event": event, "data": data})
