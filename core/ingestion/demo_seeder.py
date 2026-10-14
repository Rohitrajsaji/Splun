"""Warehouse 72 80-Scenario Benchmark Demo Seeder.

Deterministically initializes and populates the Warehouse 72 Application:
1. 80-row Excel workbook covering 5 operational modules:
   - Order Intake (Scenarios 1-16)
   - NSI Tax Validation (Scenarios 17-32)
   - Inventory Allocation (Scenarios 33-48)
   - Bulk Shipment (Scenarios 49-64)
   - Audit Logging (Scenarios 65-80)
2. BRD document with requirements REQ-001 through REQ-008 (mandatory vs optional).
3. Python codebase scan with intentional REQ-001 (tax_id mandatory vs nullable=True) conflict.
4. Bi-temporal decisions with superseded history on REQ-003.
5. Verifications:
   - SC-001..SC-003 verified via Git Diff.
   - SC-004..SC-005 set to Impl_Reported.
   - SC-006 blocked on NSI credentials.
6. Multi-turn conversation thread.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openpyxl import Workbook

from core.engine.consultant import TechnicalConsultant
from core.engine.coordinator import AutonomousCoordinator
from core.ingestion.brd_parser import ingest_brd_markdown
from core.ingestion.code_scanner import scan_python_codebase
from core.ingestion.excel_parser import ingest_scenarios_excel
from core.ingestion.git_verifier import verify_scenarios_from_git
from core.storage.db import ProjectMemoryDB

WAREHOUSE_MODULES = [
    ("Order Intake", 1, 16, "REQ-001", "REQ-002"),
    ("NSI Tax Validation", 17, 32, "REQ-002", "REQ-004"),
    ("Inventory Allocation", 33, 48, "REQ-003", "REQ-008"),
    ("Bulk Shipment", 49, 64, "REQ-005", "REQ-006"),
    ("Audit Logging", 65, 80, "REQ-007", "REQ-008"),
]

SCENARIO_TEMPLATES = {
    "Order Intake": [
        ("Inbound Order JSON Schema Parsing", "Validate incoming order payload structure against JSON Schema v7.", "Ready_For_Impl", None),
        ("Customer Channel ID Authentication", "Authenticate API keys for channel partner ingestion endpoints.", "Ready_For_Impl", None),
        ("Tax ID Format Verification", "Ensure tax ID contains valid regional prefix and checksum digits.", "Ready_For_Impl", None),
        ("Duplicate Order Idempotency Check", "Reject re-submitted orders sharing identical idempotency key.", "Impl_Reported", None),
        ("Sanitize Customer Delivery Address", "Strip illegal symbols and normalize postal addresses to ISO standard.", "Impl_Reported", None),
        ("NSI Tax Service V2 Handshake", "Verify upstream connectivity with NSI tax gateway before queueing.", "Blocked", "Waiting on NSI Tax Service V2 credentials"),
        ("Item Quantity Range Boundary Check", "Validate order line items have quantity between 1 and 9999 units.", "Ready_For_Impl", None),
        ("Currency Conversion Exchange Fetch", "Lookup latest mid-market forex rates for international transactions.", "Ready_For_Impl", None),
        ("Order Webhook Event Emission", "Publish OrderReceived event to Kafka topic 'warehouse72.orders'.", "Ready_For_Impl", None),
        ("Customer Credit Limit Check", "Verify customer credit line before accepting large credit orders.", "Ready_For_Impl", None),
        ("Priority Order Flagging", "Flag expedited VIP orders for priority queue placement.", "Ready_For_Impl", None),
        ("Gift Message Filtering", "Scan optional customer gift notes for prohibited content.", "Ready_For_Impl", None),
        ("Order Cancellation Window Validation", "Allow cancellation only within 15 minutes of intake registration.", "Ready_For_Impl", None),
        ("Multi-Currency Rounding Normalization", "Ensure rounding differences under $0.01 are balanced in ledger.", "Ready_For_Impl", None),
        ("B2B Purchase Order Attachment Check", "Ensure B2B consignments attach valid corporate PO PDF reference.", "Ready_For_Impl", None),
        ("Intake Metrics Influx Telemetry", "Publish latency and throughput metrics to Prometheus exporter.", "Ready_For_Impl", None),
    ],
    "NSI Tax Validation": [
        ("Standard VAT Rate Evaluation", "Query NSI database for standard 20% VAT rate on non-exempt items.", "Ready_For_Impl", None),
        ("NSI Gateway Timeout Failover", "Handle upstream gateway timeouts with graceful circuit breaker retry.", "Clarification_Needed", "Ambiguity in NSI timeout failover duration"),
        ("Municipal Surcharge Computation", "Calculate localized municipal district surcharges on warehouse dispatch.", "Ready_For_Impl", None),
        ("Cross-Border Exemption Certificate Check", "Parse digital certificates for zero-rated cross-border trade.", "Ready_For_Impl", None),
        ("Tax Invoicing Sequence Counter", "Maintain sequential gap-free invoice number series per legal mandate.", "Ready_For_Impl", None),
        ("Dual-Tax Region Split Allocation", "Split tax apportionment between state and federal collection bodies.", "Ready_For_Impl", None),
        ("Tax Exemption Expiration Verification", "Verify tax exemption certificate validity dates prior to checkout.", "Ready_For_Impl", None),
        ("Retroactive VAT Adjustment Handler", "Recalculate invoice totals when retrospective tax credit is applied.", "Ready_For_Impl", None),
        ("Zero-Rated Export Manifest Formatting", "Format international invoice manifests according to WTO guidelines.", "Ready_For_Impl", None),
        ("NSI Cryptographic Seal Validation", "Verify RSA-2048 digital signature returned by NSI validation server.", "Ready_For_Impl", None),
        ("Tax Calculation Precision Test", "Assert 4-decimal-place intermediate precision before rounding.", "Ready_For_Impl", None),
        ("Reverse-Charge VAT B2B Exemption", "Apply reverse-charge mechanism for verified cross-border EU buyers.", "Ready_For_Impl", None),
        ("Daily Tax Reconciler Job", "Reconcile daily tax totals with treasury settlement reports.", "Ready_For_Impl", None),
        ("Tax Archive Immutable Storage", "Store stamped tax receipts in write-once audit storage tier.", "Ready_For_Impl", None),
        ("High-Volume Tax Rate Cache", "Cache active tax tables in Redis with 1-hour TTL and invalidation hook.", "Ready_For_Impl", None),
        ("Tax Discrepancy Alert Dispatcher", "Notify tax compliance officer when calculations differ by > $0.05.", "Ready_For_Impl", None),
    ],
    "Inventory Allocation": [
        ("Multi-Depot Stock Availability Query", "Query stock balances across all 4 regional warehouse nodes.", "Ready_For_Impl", None),
        ("Transit Latency Optimization Routing", "Select depot with lowest delivery latency to destination postal code.", "Ready_For_Impl", None),
        ("Shelf Reservation Atomic Locking", "Acquire atomic Redis distributed lock on bin locations during allocation.", "Ready_For_Impl", None),
        ("Backorder Threshold Monitoring", "Create backorder notification when available quantity drops below zero.", "Ready_For_Impl", None),
        ("Split-Shipment Consignment Strategy", "Split order into multiple shipments when single depot cannot fulfill.", "Ready_For_Impl", None),
        ("Perishable Inventory FIFO/FEFO Rule", "Prioritize inventory batches by First Expired, First Out (FEFO).", "Ready_For_Impl", None),
        ("Heavy Pallet Floor Storage Routing", "Route pallets exceeding 500kg to ground-level storage bays.", "Ready_For_Impl", None),
        ("Supervisor Lock Override Authorization", "Allow supervisor credential override on safety-locked SKU bins.", "Ready_For_Impl", None),
        ("Stock Depletion Safety Threshold", "Hold minimum safety stock of 10% for critical product categories.", "Ready_For_Impl", None),
        ("Automated Replenishment Trigger", "Fire automated PO creation when warehouse stock reaches reorder point.", "Ready_For_Impl", None),
        ("Damaged Goods Quarantine Segregation", "Isolate flagged damaged items preventing accidental allocation.", "Ready_For_Impl", None),
        ("Bin Location Path Optimization", "Generate pick-path minimizing total picker walking travel distance.", "Ready_For_Impl", None),
        ("Cross-Docking Direct Transfer", "Directly route inbound freight to outbound bay bypassing storage.", "Ready_For_Impl", None),
        ("Cycle Count Reconciliation Sweep", "Perform continuous cycle counts and update ledger variances.", "Ready_For_Impl", None),
        ("Cold Storage Temperature Zone Check", "Verify cold-chain goods are exclusively assigned to refrigerated bays.", "Ready_For_Impl", None),
        ("Stock Movement Telemetry Emission", "Log every pallet movement to internal telemetry stream.", "Ready_For_Impl", None),
    ],
    "Bulk Shipment": [
        ("Pallet Manifest PDF Generation", "Assemble multi-order consignment manifest with GS1-128 barcodes.", "Ready_For_Impl", None),
        ("Carrier API Rate Negotiation", "Fetch dynamic freight spot quotes from registered transport APIs.", "Ready_For_Impl", None),
        ("Commercial Invoice Customs Packet", "Compile export declarations and customs invoices for cross-border freight.", "Ready_For_Impl", None),
        ("Shipping Label Thermal Print Engine", "Render ZPL format shipping labels for high-speed thermal printers.", "Ready_For_Impl", None),
        ("Consignment Weight and Cube Balancing", "Compute truck axle load distribution and volumetric cube utilization.", "Ready_For_Impl", None),
        ("Hazardous Material Placard Tagging", "Affix Hazmat documentation when orders contain Class 9 lithium batteries.", "Ready_For_Impl", None),
        ("Carrier Tracking Webhook Ingestion", "Process real-time carrier milestone events (Pickup, In-Transit, Delivered).", "Ready_For_Impl", None),
        ("Proof of Delivery Signature Capture", "Archive driver signoff signatures and GPS coordinates on delivery.", "Ready_For_Impl", None),
        ("Freight Delay Notification Pipeline", "Dispatch automated email and SMS alerts when carrier logs weather delay.", "Ready_For_Impl", None),
        ("Return Merchandise Authorization Handling", "Generate reverse-logistics return shipping labels and RMA barcodes.", "Ready_For_Impl", None),
        ("Third-Party Logistics EDI 856 Dispatch", "Transmit Ship Notice/Manifest EDI 856 message to external 3PL partner.", "Ready_For_Impl", None),
        ("Dispatch Bay Scheduling Calendar", "Schedule loading dock appointments for outbound freight carriers.", "Ready_For_Impl", None),
        ("Container Seal Number Verification", "Record tamper-evident mechanical bolt seal numbers on ocean containers.", "Ready_For_Impl", None),
        ("Freight Claims Damage Documentation", "Capture multi-photo evidence for damaged cargo insurance filing.", "Ready_For_Impl", None),
        ("Carrier SLA Compliance Reporting", "Calculate on-time departure percentage against agreed carrier SLAs.", "Ready_For_Impl", None),
        ("Outbound Freight Cost Reconciler", "Compare billed freight invoice charges against quoted API spot rates.", "Ready_For_Impl", None),
    ],
    "Audit Logging": [
        ("Cryptographic Event SHA-256 Hashing", "Hash all state change events with SHA-256 before disk persistence.", "Ready_For_Impl", None),
        ("Immutable Merkle Tree Root Chaining", "Calculate hourly Merkle root hashes anchoring state history.", "Ready_For_Impl", None),
        ("Tamper Detection Anomaly Alerting", "Trigger high-severity alert if hash sequence validation fails.", "Ready_For_Impl", None),
        ("Role-Based Access Audit Trail", "Log user identity, role, and timestamp for all configuration changes.", "Ready_For_Impl", None),
        ("GDPR Right-to-be-Forgotten Redaction", "Anonymize PII in historical transaction logs while preserving hash links.", "Ready_For_Impl", None),
        ("Regulatory Compliance Report Exporter", "Generate ISO-27001 compliant audit log export for external examiners.", "Ready_For_Impl", None),
        ("System Heartbeat Liveness Probing", "Record periodic system health snapshots in tamper-evident ledger.", "Ready_For_Impl", None),
        ("Supervisor Allocation Override Log", "Record mandatory justification comment for every manual override.", "Ready_For_Impl", None),
        ("Database Migration Audit Archival", "Record schema DDL changes with git commit hashes in audit ledger.", "Ready_For_Impl", None),
        ("Privileged Key Rotation Log", "Track lifecycle and rotation events for encryption keys and API secrets.", "Ready_For_Impl", None),
        ("API Request Payload Sanitization", "Strip sensitive credit card tokens before logging inbound API payloads.", "Ready_For_Impl", None),
        ("Log Retention Tiering Lifecycle", "Transition 90-day logs from warm SSD to cold immutable object storage.", "Ready_For_Impl", None),
        ("Failed Authentication Rate Monitor", "Flag brute-force account credential guessing on warehouse terminals.", "Ready_For_Impl", None),
        ("Hardware Barcode Scanner Audit Check", "Log scanner hardware ID with every physical pallet confirmation.", "Ready_For_Impl", None),
        ("Disaster Recovery Replication Verification", "Verify checksum replication across primary and secondary data centers.", "Ready_For_Impl", None),
        ("Audit Integrity Attestation API", "Expose endpoint verifying chain cryptographic integrity from genesis.", "Ready_For_Impl", None),
    ],
}

BRD_CONTENT = """# Warehouse 72 Business Requirements Document (BRD)

### REQ-001: Order Intake Mandatory Tax ID
Constraint: Mandatory
Field: `tax_id`, `OrderRecord.tax_id`
Priority: High
Acceptance Criteria:
- Every inbound order must supply a validated alphanumeric tax_id before ingestion.
- Orders with null, missing, or empty tax_id must be rejected immediately at the intake boundary.

### REQ-002: Real-time NSI Tax Validation Gateway
Constraint: Mandatory
Field: `nsi_tax_status`
Priority: High
Acceptance Criteria:
- Validate domestic and regional tax compliance via the National Standard Invoicing gateway.
- Record validation status before committing orders to fulfillment.

### REQ-003: Inventory Multi-Warehouse Allocation Strategy
Constraint: Mandatory
Field: `allocation_strategy`
Priority: High
Acceptance Criteria:
- Dynamically allocate inventory across regional hubs based on minimum transit latency.
- Fulfill orders from nearest stock node to ensure 24-hour delivery SLA.

### REQ-004: International Tax Exemption Processing
Constraint: Optional
Field: `exemption_certificate`
Priority: Medium
Acceptance Criteria:
- Cross-border shipments may attach a recognized VAT exemption certificate.
- Waive localized sales tax when a valid exemption is attached.

### REQ-005: Automated Bulk Shipment Manifest Generation
Constraint: Mandatory
Field: `manifest_id`
Priority: High
Acceptance Criteria:
- Generate unified PDF/JSON freight manifests for all outbound pallet consignments.
- Include GS1-128 logistics labels and transport carrier codes.

### REQ-006: Dynamic Courier Dispatch Routing
Constraint: Optional
Field: `courier_code`
Priority: Low
Acceptance Criteria:
- Shipments may be routed to secondary carriers when primary provider exceeds SLA.

### REQ-007: Immutable Cryptographic Audit Log
Constraint: Mandatory
Field: `audit_hash`
Priority: High
Acceptance Criteria:
- All scenario state transitions and operational actions must be hashed with SHA-256.
- Maintain an unbroken cryptographic chain preventing historical log tampering.

### REQ-008: Role-Based Authorization Gate
Constraint: Mandatory
Field: `auth_role`
Priority: High
Acceptance Criteria:
- Operations overriding stock allocation or credit locks require warehouse_supervisor role.
- Unauthenticated or unauthorized overrides must be blocked and alerted.
"""

MODELS_PY_CONTENT = """\"\"\"Warehouse 72 Data Models.

Contains SQLAlchemy ORM representations of warehouse intake and fulfillment entities.
\"\"\"

from sqlalchemy import Column, Integer, String, Boolean, Float


class OrderRecord:
    id = Column(Integer, primary_key=True)
    order_number = Column(String(64), nullable=False)
    # Intentional discrepancy: REQ-001 specifies tax_id is Mandatory,
    # but the schema definition marks it nullable=True.
    tax_id = Column(String(50), nullable=True)
    nsi_tax_status = Column(String(20), nullable=False)
    allocation_strategy = Column(String(50), nullable=False)
    exemption_certificate = Column(String(64), nullable=True)
    manifest_id = Column(String(64), nullable=False)
    courier_code = Column(String(32), nullable=True)
    audit_hash = Column(String(64), nullable=False)
    auth_role = Column(String(32), nullable=False)
"""

SERVICES_PY_CONTENT = """\"\"\"Warehouse 72 Fulfillment Services.
\"\"\"

from .models import OrderRecord


class TaxValidationService:
    def validate_order_tax(self, order: OrderRecord) -> bool:
        if not order.tax_id:
            return False
        return len(order.tax_id) >= 6


class InventoryAllocationService:
    def allocate_stock(self, sku: str, quantity: int, strategy: str = "LATENCY") -> dict:
        return {
            "status": "ALLOCATED",
            "sku": sku,
            "quantity": quantity,
            "strategy": strategy,
        }


class BulkShipmentService:
    def generate_manifest(self, consignment_id: str) -> dict:
        return {
            "manifest_id": f"MAN-{consignment_id}",
            "status": "GENERATED",
        }
"""

SAMPLE_GIT_DIFF = """commit 9a4b82c1e7f3028d71249b6e83dc28f1042738fa
Author: Lead Systems Architect <arch@warehouse72.io>
Date:   Mon Oct 12 10:15:00 2026 -0400

    feat(intake): implement order schema parsing and channel auth for SC-001, SC-002, SC-003

diff --git a/warehouse72/intake.py b/warehouse72/intake.py
new file mode 100644
--- /dev/null
+++ b/warehouse72/intake.py
@@ -0,0 +1,24 @@
+def parse_order_intake(payload: dict) -> dict:
+    if not payload.get("order_number"):
+        raise ValueError("Missing required order_number")
+    return payload
+
+def verify_channel_auth(channel_key: str) -> bool:
+    return bool(channel_key and len(channel_key) >= 16)
+
+def check_tax_format(tax_id: str) -> bool:
+    return bool(tax_id and tax_id.isalnum())
"""


def _generate_80_scenarios_workbook() -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Scenarios"

    # Header Row
    ws.append(["Scenario #", "Title", "Description", "Status", "Blockers", "Linked Requirement"])

    current_num = 1
    for mod_name, start_idx, end_idx, req_a, req_b in WAREHOUSE_MODULES:
        templates = SCENARIO_TEMPLATES[mod_name]
        for i, (title, desc, status, blocker) in enumerate(templates):
            # Alternating assignment of linked requirements within module
            linked_req = req_a if (i < 8) else req_b
            ws.append([
                current_num,
                title,
                desc,
                status,
                blocker or "",
                linked_req,
            ])
            current_num += 1

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


def seed_warehouse72_demo(
    db: Optional[ProjectMemoryDB] = None,
    project_id: str = "warehouse-72",
    workspace_dir: Optional[str] = None,
) -> dict:
    """Deterministically seeds or resets the 'Warehouse 72 Application' benchmark."""
    if db is None:
        db = ProjectMemoryDB()

    # 1. Reset / initialize project in database
    with db.transaction():
        # Clear existing project records if resetting
        db._conn.execute("DELETE FROM messages WHERE project_id = ?", (project_id,))
        db._conn.execute("DELETE FROM threads WHERE project_id = ?", (project_id,))
        db._conn.execute("DELETE FROM scenarios WHERE project_id = ?", (project_id,))
        db._conn.execute("DELETE FROM requirements WHERE project_id = ?", (project_id,))
        db._conn.execute("DELETE FROM code_symbols WHERE project_id = ?", (project_id,))
        db._conn.execute("DELETE FROM decisions WHERE project_id = ?", (project_id,))
        db._conn.execute("DELETE FROM evidence_vault WHERE project_id = ?", (project_id,))

    # Setup target workspace directory
    ws_path = Path(workspace_dir) if workspace_dir else Path(".consultant") / "workspaces" / project_id
    ws_path.resolve().mkdir(parents=True, exist_ok=True)

    db.create_project(
        project_id=project_id,
        name="Warehouse 72 Application",
        repo_path=str(ws_path),
    )

    # 2. Ingest 80-Scenario Excel Workbook
    excel_bio = _generate_80_scenarios_workbook()
    excel_bio.name = "Warehouse72_80_Scenarios.xlsx"
    excel_res = ingest_scenarios_excel(db, project_id, excel_bio)

    # 3. Ingest BRD Markdown Document
    brd_res = ingest_brd_markdown(db, project_id, "Warehouse72_BRD.md", content=BRD_CONTENT)

    # 4. Write sample Python codebase to workspace and scan AST
    models_file = ws_path / "models.py"
    models_file.write_text(MODELS_PY_CONTENT, encoding="utf-8")
    services_file = ws_path / "services.py"
    services_file.write_text(SERVICES_PY_CONTENT, encoding="utf-8")

    scan_res = scan_python_codebase(db, project_id, str(ws_path))

    # 5. Seed Bi-Temporal Decisions on REQ-003 (older decision superseded by newer)
    req3 = db.get_requirement(project_id, "REQ-003")
    req3_ev_id = req3["evidence_id"] if req3 else f"BRD:Warehouse72_BRD.md:REQ-003"
    req7 = db.get_requirement(project_id, "REQ-007")
    req7_ev_id = req7["evidence_id"] if req7 else f"BRD:Warehouse72_BRD.md:REQ-007"

    dec1_id = db.record_decision(
        project_id=project_id,
        subject_entity="requirement:REQ-003",
        decision_text="Single-depot FIFO allocation model without regional split routing",
        epistemic_status="APPROVED_DECISION",
        source_evidence_id=req3_ev_id,
        rationale="Phase 1 legacy baseline",
        created_by="Architecture Board",
    )
    dec2_id = db.record_decision(
        project_id=project_id,
        subject_entity="requirement:REQ-003",
        decision_text="Dynamic multi-warehouse latency-based allocation model across regional stock nodes",
        epistemic_status="APPROVED_DECISION",
        source_evidence_id=req3_ev_id,
        rationale="Warehouse 72 Phase 2 operational directive to minimize SLA delivery breaches",
        created_by="VP Logistics",
    )

    # Additional audit security decision on REQ-007
    db.record_decision(
        project_id=project_id,
        subject_entity="requirement:REQ-007",
        decision_text="Cryptographic audit log must use SHA-256 with HMAC salted signature",
        epistemic_status="APPROVED_DECISION",
        source_evidence_id=req7_ev_id,
        rationale="ISO-27001 regulatory compliance standard",
        created_by="Chief Security Officer",
    )

    # 6. Verify SC-001..SC-003 with sample Git Diff
    git_res = verify_scenarios_from_git(
        db=db,
        project_id=project_id,
        diff_text=SAMPLE_GIT_DIFF,
        commit_sha="9a4b82c1",
        scenario_ids=["SC-001", "SC-002", "SC-003"],
    )

    # 7. Update explicit statuses for SC-004..SC-006
    db.upsert_scenario(
        project_id,
        {
            "scenario_num": 4,
            "status": "Impl_Reported",
        },
    )
    db.upsert_scenario(
        project_id,
        {
            "scenario_num": 5,
            "status": "Impl_Reported",
        },
    )
    db.upsert_scenario(
        project_id,
        {
            "scenario_num": 6,
            "status": "Blocked",
            "blockers": "Waiting on NSI Tax Service V2 credentials",
        },
    )

    # 8. Create multi-turn kickoff chat thread
    thread_id = "th-warehouse72-kickoff"
    db.create_thread(
        project_id=project_id,
        title="Warehouse 72 Kickoff & Triangulation",
        mode="project",
        thread_id=thread_id,
    )
    db.add_message(
        project_id=project_id,
        thread_id=thread_id,
        role="user",
        content="Welcome team. Where do we stand on the Warehouse 72 modules and NSI tax compliance?",
    )
    db.add_message(
        project_id=project_id,
        thread_id=thread_id,
        role="assistant",
        content=(
            "Initial triangulation complete across 80 scenarios:\n"
            "- SC-001..SC-003 verified via Git commit 9a4b82c1.\n"
            "- SC-004..SC-005 implemented and pending verification.\n"
            "- SC-006 blocked on NSI Tax Service V2 credentials.\n"
            "- [DISCREPANCY] Live Discrepancy on REQ-001: BRD specifies mandatory tax_id, but "
            "OrderRecord.tax_id is nullable=True in models.py."
        ),
    )

    consultant = TechnicalConsultant(db, project_id)
    coordinator = AutonomousCoordinator(db, project_id)

    return {
        "project_id": project_id,
        "name": "Warehouse 72 Application",
        "scenarios_seeded": excel_res.get("rows_ingested", 80),
        "requirements_seeded": len(brd_res.get("req_ids", [])),
        "symbols_scanned": scan_res.get("symbols_found", 0),
        "status_report": consultant.get_project_status_report(),
        "execution_plan": coordinator.build_execution_plan(),
    }


if __name__ == "__main__":
    db = ProjectMemoryDB()
    res = seed_warehouse72_demo(db)
    print(f"Seeded {res['name']} ({res['project_id']}) with {res['scenarios_seeded']} scenarios into {db.db_path}")
