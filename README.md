# Quant Procure Coupa PunchOut Starter

This repository provides a **working starter implementation** and an **administrator runbook** for configuring a Coupa PunchOut catalog for supplier **Quant Procure** with 5 catalog items.

## What's included

- `docs/COUPA_PUNCHOUT_SETUP.md` – complete Coupa admin + supplier setup checklist.
- `server.js` – lightweight Node.js PunchOut demo server with endpoints.
- `public/index.html` – front-end shopping page that simulates PunchOut checkout.
- `public/app.js` – client-side cart logic for 5 Quant Procure items.
- `public/styles.css` – styling for the PunchOut demo UI.

## Run locally

```bash
node server.js
```

Then open:

- `http://localhost:3000/` for the front-end catalog demo
- `http://localhost:3000/health` for a health check

## Key demo endpoints

- `POST /punchout/setup` – accepts a PunchOutSetupRequest (mock parser).
- `GET /api/items` – returns 5 punchout item definitions.
- `POST /api/checkout` – returns a PunchOutOrderMessage payload.

> This is a demo implementation for onboarding/testing. In production, add proper cXML validation, credentials storage, TLS cert hardening, audit logging, and ERP mapping.
