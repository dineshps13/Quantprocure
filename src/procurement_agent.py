from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Protocol


@dataclass
class Requisition:
    requisition_id: str
    requester_email: Optional[str]
    delivery_location: Optional[str]
    category_name: Optional[str]
    item_name: Optional[str]
    item_description: str = ""
    make_or_brand: Optional[str] = None
    quantity: float = 1.0
    uom: str = "EA"
    need_by_date: Optional[date] = None


@dataclass
class NormalizedFields:
    requisition_id: str
    requester_email: Optional[str]
    delivery_location: Optional[str]
    category_name: Optional[str]
    item_name: Optional[str]
    item_description: str
    make_or_brand: Optional[str]
    quantity: float
    uom: str
    need_by_date: Optional[date]

    def missing(self, required: List[str]) -> List[str]:
        return [field for field in required if not getattr(self, field)]

    def has(self, required: List[str]) -> bool:
        return len(self.missing(required)) == 0


@dataclass
class Supplier:
    supplier_id: str
    supplier_name: str
    categories: List[str]
    serviceable_locations: List[str]
    compliant: bool = True


@dataclass
class Bid:
    supplier_id: str
    supplier_name: str
    item_price: float
    shipping_cost: float
    tax: float
    lead_time_days: int
    compliance_flags: List[str] = field(default_factory=list)


@dataclass
class AwardResult:
    winning_supplier_id: str
    winning_supplier_name: str
    awarded_total_cost: float
    decision_timestamp: datetime
    decision_reason: str


@dataclass
class ProcessResult:
    status: str
    requisition_id: str
    details: Dict[str, object]


class CoupaClient(Protocol):
    def fetch_requisition(self, requisition_id: str) -> Requisition: ...

    def update_requisition(self, requisition_id: str, payload: Dict[str, object]) -> None: ...


class SupplierRepository(Protocol):
    def search(self, category: str, location: str) -> List[Supplier]: ...


class BidProvider(Protocol):
    def request_bids(self, requisition: NormalizedFields, suppliers: List[Supplier]) -> List[Bid]: ...


class AuditLogger(Protocol):
    def log(self, event: str, data: Dict[str, object]) -> None: ...


class RequisitionExtractor:
    """Deterministic field extraction with lightweight normalization."""

    @staticmethod
    def parse(req: Requisition) -> NormalizedFields:
        return NormalizedFields(
            requisition_id=req.requisition_id,
            requester_email=_clean(req.requester_email),
            delivery_location=_normalize_location(req.delivery_location),
            category_name=_clean(req.category_name),
            item_name=_clean(req.item_name),
            item_description=req.item_description.strip(),
            make_or_brand=_clean(req.make_or_brand),
            quantity=req.quantity,
            uom=req.uom.strip().upper(),
            need_by_date=req.need_by_date,
        )


class BidEvaluator:
    @staticmethod
    def is_compliant(bid: Bid) -> bool:
        return len(bid.compliance_flags) == 0

    @staticmethod
    def total_landed_cost(bid: Bid) -> float:
        return round(bid.item_price + bid.shipping_cost + bid.tax, 2)


class ProcurementAgent:
    REQUIRED_FIELDS = ["requester_email", "delivery_location", "category_name", "item_name"]

    def __init__(
        self,
        coupa_client: CoupaClient,
        supplier_repo: SupplierRepository,
        bid_provider: BidProvider,
        audit_logger: AuditLogger,
    ):
        self.coupa_client = coupa_client
        self.supplier_repo = supplier_repo
        self.bid_provider = bid_provider
        self.audit_logger = audit_logger
        self.extractor = RequisitionExtractor()
        self.evaluator = BidEvaluator()

    def process_requisition(self, requisition_id: str) -> ProcessResult:
        req = self.coupa_client.fetch_requisition(requisition_id)
        fields = self.extractor.parse(req)

        missing_fields = fields.missing(self.REQUIRED_FIELDS)
        if missing_fields:
            result = ProcessResult(
                status="manual_fix_required",
                requisition_id=requisition_id,
                details={"missing_fields": missing_fields},
            )
            self.audit_logger.log("missing_fields", {"requisition_id": requisition_id, "missing": missing_fields})
            return result

        suppliers = self.supplier_repo.search(fields.category_name or "", fields.delivery_location or "")
        if not suppliers:
            self.audit_logger.log("no_suppliers", {"requisition_id": requisition_id})
            return ProcessResult(
                status="no_suppliers_found",
                requisition_id=requisition_id,
                details={"category": fields.category_name, "location": fields.delivery_location},
            )

        bids = self.bid_provider.request_bids(fields, suppliers)
        compliant_bids = [bid for bid in bids if self.evaluator.is_compliant(bid)]
        if not compliant_bids:
            self.audit_logger.log("no_compliant_bids", {"requisition_id": requisition_id, "bids": len(bids)})
            return ProcessResult(
                status="no_compliant_bids",
                requisition_id=requisition_id,
                details={"received_bids": len(bids)},
            )

        winner = min(compliant_bids, key=self.evaluator.total_landed_cost)
        total_cost = self.evaluator.total_landed_cost(winner)
        award = AwardResult(
            winning_supplier_id=winner.supplier_id,
            winning_supplier_name=winner.supplier_name,
            awarded_total_cost=total_cost,
            decision_timestamp=datetime.utcnow(),
            decision_reason="Lowest compliant total landed cost",
        )

        self.coupa_client.update_requisition(
            requisition_id,
            {
                "winning_supplier_id": award.winning_supplier_id,
                "winning_supplier_name": award.winning_supplier_name,
                "awarded_total_cost": award.awarded_total_cost,
                "award_reason": award.decision_reason,
                "decision_timestamp": award.decision_timestamp.isoformat(),
            },
        )

        self.audit_logger.log(
            "awarded",
            {
                "requisition_id": requisition_id,
                "winner": award.winning_supplier_name,
                "awarded_total_cost": award.awarded_total_cost,
                "bids_received": len(bids),
                "compliant_bids": len(compliant_bids),
            },
        )

        return ProcessResult(
            status="awarded",
            requisition_id=requisition_id,
            details={
                "winner": award.winning_supplier_name,
                "winner_id": award.winning_supplier_id,
                "awarded_total_cost": award.awarded_total_cost,
            },
        )


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _normalize_location(value: Optional[str]) -> Optional[str]:
    cleaned = _clean(value)
    if not cleaned:
        return None
    return " ".join(cleaned.split()).title()
