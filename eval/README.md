# Synthetic evaluation

In-process harness against Growth Decision Engine, Policy Engine, and catalogue.

**Milestone 0:** layout only. Scenarios and runner land in **M14** (thin fixtures may appear earlier as engines are tested).

Results are **synthetic / offline**. Do not present them as live GMV or conversion uplift.

Scenarios should cite stable `ref_id`s (`SES-001`, `SKU-001-M`, `EVD-001`, …), not UUIDs.

Safety gates that must stay at zero:

- hard-budget violations
- hallucinated SKUs
- unauthorized offers
- unapproved money actions
- duplicate payment processing
