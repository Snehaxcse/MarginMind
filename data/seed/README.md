# Seed data

Hand-authored synthetic catalogue, demo merchant, demo customer, policies, and offers.

Loaded by `python -m app.db.seed` from `backend/app/db/seed_data.py`.

Initial catalogue size:

- 14 products (`PRD-001` … `PRD-014`)
- 24 variants (`SKU-001-M` …)

Use stable `ref_id`s (`MER-001`, `CUS-001`, `PRD-001`, `SKU-001-M`, `POL-001`, `OFR-001`, …). See `docs/ARCHITECTURE.md` §8.1.

Do not scrape a live store. All names and SKUs are fictional.
