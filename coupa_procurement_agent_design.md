# Coupa Procurement AI Agent Blueprint

## 1) Objective
Design an AI-driven procurement assistant that:
1. Reads purchase requests from Coupa.
2. Extracts structured sourcing signals (for example: requester email, delivery location, category, item, make/brand, quantity, required date, budget hints, etc.).
3. Uses category + location intelligence to invite suitable suppliers from an online supplier database.
4. Compares bids and selects the lowest compliant offer.
5. Writes the winning supplier back into the Coupa purchase request.

---

## 2) End-to-End Process (Business View)

### Step A — Intake from Coupa
- Trigger when a new requisition is created or reaches a sourcing-ready status.
- Pull requisition data through Coupa API/webhooks.

### Step B — Information Extraction
- Extract and normalize key fields:
  - Requester/User email
  - Delivery location (site, city, state, country, postal code)
  - Category name and code
  - Item description/name
  - Make/brand (if any)
  - Quantity/UOM
  - Need-by date
  - Internal notes/specification text
- Resolve missing values where possible from Coupa master data.

### Step C — Supplier Discovery
- Match suppliers by:
  - Category capability
  - Delivery geography
  - Compliance and qualification status
  - Historical performance
- Generate a shortlist (for example top 5–20 suppliers).

### Step D — Bidding / RFQ Dispatch
- Send bid invitations (API/email/portal integration).
- Include clear specs, quantity, delivery location, and due date.
- Capture responses in a normalized format.

### Step E — Bid Evaluation
- Rank by lowest **compliant** total landed cost (not only unit price).
- Apply business rules:
  - Mandatory certifications
  - Lead-time threshold
  - Quality/SLA constraints
  - Preferred supplier policy (if configured)

### Step F — Award and Update Coupa
- Select winning supplier.
- Write supplier name/ID (and optional quote details) into requisition custom fields or sourcing-linked object in Coupa.
- Notify requester and procurement approvers.

### Step G — Audit Trail
- Persist every decision input:
  - Why suppliers were invited
  - Why winner was selected
  - Which rule excluded each non-winning bid
- Keep logs for compliance and later dispute handling.

---

## 3) Recommended Agent Architecture

## Components
1. **Coupa Connector Service**
   - Handles Coupa authentication and API calls.
   - Subscribes to requisition events.

2. **Extraction + Normalization Engine**
   - Deterministic parsing for known fields.
   - LLM/NLP layer for free-text specifications.
   - Confidence scoring and fallback rules.

3. **Supplier Matching Service**
   - Queries supplier database.
   - Filters by category, location, compliance, contract status.

4. **RFQ/Bidding Orchestrator**
   - Sends invites and receives quotes.
   - Enforces bid deadlines and version control.

5. **Evaluation Engine**
   - Computes comparable commercial scorecards.
   - Runs policy checks and selects winner.

6. **Coupa Updater**
   - Writes winner back to Coupa requisition/sourcing object.
   - Handles retries/idempotency.

7. **Monitoring + Audit Store**
   - Immutable event logs.
   - Business KPI dashboards.

---

## 4) Data Model (Minimum Fields)

## Requisition Extract (Input)
- `requisition_id`
- `requester_email`
- `business_unit`
- `delivery_location`
- `category_name`
- `category_code`
- `item_name`
- `item_description`
- `make_or_brand`
- `quantity`
- `uom`
- `currency`
- `target_price` (optional)
- `need_by_date`
- `attachments`

## Bid Record (Supplier Response)
- `supplier_id`
- `supplier_name`
- `item_price`
- `shipping_cost`
- `tax`
- `total_cost`
- `lead_time_days`
- `quote_valid_till`
- `compliance_flags`
- `comments`

## Award Decision
- `winning_supplier_id`
- `winning_supplier_name`
- `awarded_total_cost`
- `award_reason`
- `decision_timestamp`
- `decision_rules_version`

---

## 5) Practical Coupa Integration Notes

- Use Coupa webhooks/events where possible for near-real-time intake.
- Keep field mapping explicit between Coupa requisition schema and your agent schema.
- Store Coupa object IDs to support precise updates.
- Use idempotency keys (for example requisition ID + version) to avoid duplicate bidding rounds.
- If winner write-back fails, queue and retry with dead-letter handling.

---

## 6) Decision Logic (Example)

1. Validate requisition completeness.
2. If critical fields are missing (category/location/item), route to procurement analyst for correction.
3. Retrieve suppliers matching category + location.
4. Invite suppliers and wait until deadline.
5. Remove non-compliant bids.
6. Compute total landed cost for compliant bids.
7. Pick minimum total landed cost.
8. Update Coupa with winner and full rationale.

---

## 7) Governance & Controls

- **Human-in-the-loop thresholds**:
  - High value requisitions
  - Single-bid scenarios
  - New/unrated suppliers
- **Segregation of duties**: Agent recommends; approver can be mandatory above thresholds.
- **Policy versioning**: Keep award logic versioned and traceable.
- **Data privacy**: Limit PII exposure and encrypt logs.

---

## 8) KPIs to Track

- Average sourcing cycle time
- Percentage of requisitions auto-sourced
- Savings vs baseline/target price
- Bid participation rate
- Award-to-PO conversion rate
- Exception rate requiring manual intervention

---

## 9) Suggested Implementation Roadmap

### Phase 1 — MVP (4–8 weeks)
- Ingest Coupa requisitions.
- Extract key fields (email, location, category, item, make).
- Invite suppliers from category + location match.
- Select lowest compliant bid.
- Update Coupa winner field.

### Phase 2 — Optimization
- Add lead-time and quality weighted scoring.
- Add supplier performance signals.
- Add recommendation explanations in UI.

### Phase 3 — Scale
- Multi-category logic.
- Contract-aware sourcing.
- Advanced anomaly detection (e.g., suspicious low bids).

---

## 10) Example Pseudocode

```python
def process_requisition(req_id):
    req = coupa.fetch_requisition(req_id)
    fields = extractor.parse(req)

    required = ["requester_email", "delivery_location", "category_name", "item_name"]
    if not fields.has(required):
        return workflow.route_for_manual_fix(req_id, missing=fields.missing(required))

    suppliers = supplier_db.search(
        category=fields["category_name"],
        location=fields["delivery_location"],
    )

    rfq_id = bidding.send_rfq(req_id=req_id, suppliers=suppliers, spec=fields)
    bids = bidding.collect_bids(rfq_id)

    compliant = [b for b in bids if rules.is_compliant(b, fields)]
    if not compliant:
        return workflow.raise_exception(req_id, reason="No compliant bids")

    winner = min(compliant, key=lambda b: pricing.total_landed_cost(b))

    coupa.update_requisition(req_id, {
        "winning_supplier_name": winner.supplier_name,
        "winning_supplier_id": winner.supplier_id,
        "awarded_total_cost": pricing.total_landed_cost(winner),
    })

    audit.log_award(req_id, winner, bids, rules.version)
    return winner
```

---

## 11) What to Prepare Before Build

- Coupa API credentials and sandbox access.
- Confirm where winner data should be written in Coupa (custom field/object).
- Supplier master data quality check (category + location tags).
- Procurement policy matrix (mandatory filters, exceptions, approval thresholds).
- RFQ communication channel (email/API/supplier portal).

This blueprint can directly drive a technical design document and implementation backlog.
