# Coupa PunchOut Setup Runbook — Supplier: Quant Procure

## 1) Business scope

- **Supplier name:** Quant Procure
- **Catalog type:** PunchOut (Level 2)
- **Initial catalog items (5):**
  1. QP-1001 — Precision Sensor Module
  2. QP-1002 — Industrial Relay Pack
  3. QP-1003 — Calibration Service Kit
  4. QP-1004 — Control Valve Assembly
  5. QP-1005 — Safety Inspection Bundle
- **Currency:** USD
- **Tax handling:** Calculated by buyer policy in Coupa and/or ERP

---

## 2) Prerequisites checklist

### Buyer (Coupa admin) side

- Coupa admin permissions for Suppliers, Catalogs, and cXML setup.
- Buyer identity + shared secret for PunchOut.
- Test requester user account to validate checkout.
- Approved commodity/account mappings for requisition routing.

### Supplier (Quant Procure) side

- PunchOut endpoint URL hosted over HTTPS.
- Supplier identity + shared secret.
- Ability to consume `PunchOutSetupRequest` and return `PunchOutSetupResponse`.
- Return URL support for `PunchOutOrderMessage`.

---

## 3) Coupa configuration steps

## Step A — Create supplier

1. Navigate to **Suppliers > Create Supplier**.
2. Enter legal supplier details for **Quant Procure**.
3. Save and enable supplier for transacting.

## Step B — Configure PunchOut in Coupa

1. Open the Quant Procure supplier record.
2. Go to **Setup > Punchout Configuration**.
3. Set:
   - **Punchout Enabled:** Yes
   - **Punchout URL:** `https://supplier.quantprocure.com/punchout/setup` (or your test host)
   - **From Domain/Identity:** value supplied by Quant Procure
   - **Sender Domain/Identity:** buyer Coupa identity
   - **Shared Secret:** agreed test secret
   - **Operation Allowed:** Create requisition cart return
4. Save changes.

## Step C — Enable ordering and user visibility

1. Ensure supplier is **active for ordering**.
2. Add supplier to relevant **user/content groups**.
3. Verify requester can see PunchOut tile or supplier link.

## Step D — Commodity + accounting defaults

1. Define commodity mappings for each Quant Procure item family.
2. Apply accounting defaults if policy requires cost center/account.
3. Ensure no blocking validations for test users.

---

## 4) Supplier PunchOut technical flow

1. Coupa sends `PunchOutSetupRequest` (cXML) to Quant Procure endpoint.
2. Quant Procure validates credentials and returns `PunchOutSetupResponse` with `StartPage URL`.
3. User shops on Quant Procure website.
4. User clicks **Checkout to Coupa**.
5. Quant Procure sends `PunchOutOrderMessage` to Coupa return URL with selected line items.
6. Coupa converts lines into a draft requisition.

---

## 5) Required item payload fields

For each returned line item include:

- Supplier part number (`SupplierPartID`)
- Description
- Quantity
- Unit Price + Currency
- UOM
- Optional classification (UNSPSC)
- Optional lead time/auxiliary IDs

---

## 6) Test plan (SIT/UAT)

1. Launch PunchOut from Coupa using test requester.
2. Confirm session is created and start page opens.
3. Add at least 2 of the 5 catalog items.
4. Return cart to Coupa.
5. Verify item details, price, quantity, and totals.
6. Submit requisition and route for approval.
7. Validate downstream PO creation behavior.

---

## 7) Go-live checklist

- Replace test URL and credentials with production values.
- Confirm TLS certificate and endpoint monitoring.
- Set alerting for PunchOut failures.
- Document support ownership and incident runbook.
- Schedule quarterly credential rotation.

---

## 8) 5-item launch catalog (example)

| Supplier Part ID | Name | UOM | Unit Price (USD) | UNSPSC |
|---|---|---|---:|---|
| QP-1001 | Precision Sensor Module | EA | 249.00 | 41111900 |
| QP-1002 | Industrial Relay Pack | EA | 89.50 | 39121500 |
| QP-1003 | Calibration Service Kit | KIT | 315.00 | 41116100 |
| QP-1004 | Control Valve Assembly | EA | 512.75 | 40141600 |
| QP-1005 | Safety Inspection Bundle | JOB | 780.00 | 46171600 |
