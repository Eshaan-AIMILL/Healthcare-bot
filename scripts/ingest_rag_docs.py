import asyncio
import hashlib
import logging
import os
import sys
import textwrap
import warnings

# ── Silence ChromaDB telemetry bug BEFORE any chromadb import ─────────────────
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

class _SuppressChromaTelemetry(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "telemetry" not in record.getMessage().lower()

for _logger_name in (
    "chromadb",
    "chromadb.telemetry",
    "chromadb.telemetry.product",
    "chromadb.telemetry.product.posthog",
):
    _l = logging.getLogger(_logger_name)
    _l.addFilter(_SuppressChromaTelemetry())
    _l.propagate = False

warnings.filterwarnings("ignore", message=".*telemetry.*")
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from app.config import settings
from app.rag.chroma_client import get_chroma_client, get_or_create_collection

CHUNK_SIZE = settings.rag_chunk_size
CHUNK_OVERLAP = settings.rag_chunk_overlap


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


async def embed(texts: list[str]) -> list[list[float]]:
    embeddings = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for text in texts:
            response = await client.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={"model": "nomic-embed-text", "prompt": text},
            )
            response.raise_for_status()
            embeddings.append(response.json()["embedding"])
    return embeddings


def doc_id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ── Synthetic Policy Documents ────────────────────────────────────────────────

BILLING_DOCS = [
    textwrap.dedent("""
    ICD-10-CM Coding Guidelines — Section I.C.1: Certain Infectious and Parasitic Diseases

    The ICD-10-CM code J06.9 (Acute upper respiratory infection, unspecified) should be used when
    the documentation does not specify the exact causative agent or site. Coders must not assign
    a more specific code without physician documentation. When coding encounters for acute upper
    respiratory infections, the CPT codes 99213 or 99214 should match the documented complexity.
    A CPT 99214 requires at least two of the following: detailed history, detailed examination, or
    medical decision making of moderate complexity. Assigning 99214 for a visit documented as
    low complexity constitutes upcoding and is subject to payer audit.
    """),
    textwrap.dedent("""
    Payer Rejection Policy — Apollo Munich Health Insurance: Claim Modifier Requirements

    Claims for surgical procedures must include applicable HCPCS modifiers. Modifier -25 must be
    appended to evaluation and management (E&M) services performed on the same day as a procedure.
    Failure to append Modifier -25 is one of the most common causes of claim rejection for
    same-day E&M and procedure billing. Modifier -59 is required when a procedure is distinct and
    separate from another procedure performed on the same day. Unbundling — billing separately for
    services that should be included in a comprehensive code — is a violation of payer policy and
    will result in claim rejection and potential fraud investigation.
    """),
    textwrap.dedent("""
    ICD-10 to CPT Alignment Rules — Diagnosis-Procedure Validation

    Each CPT code must be clinically consistent with the ICD-10 diagnosis code on the same claim.
    Examples of code mismatches that trigger automatic rejection:
    - CPT 43239 (esophagogastroduodenoscopy with biopsy) billed with ICD-10 M54.5 (low back pain)
      — anatomical mismatch.
    - CPT 27447 (total knee arthroplasty) billed with ICD-10 J18.9 (pneumonia) — system mismatch.
    - CPT 93000 (ECG) billed with ICD-10 F32.1 (major depressive disorder) — requires documented
      cardiac risk factor to avoid rejection.
    All claims with a diagnosis-procedure mismatch score a rejection risk of Critical.
    """),
    textwrap.dedent("""
    NABH Billing Standards — Documentation Requirements for Claim Submission

    All inpatient claims must include: (1) admitting diagnosis with ICD-10 code, (2) discharge
    summary signed by the attending physician, (3) itemised bill matching the CPT codes submitted,
    (4) pre-authorisation number for elective procedures. Missing any of these four elements
    results in automatic claim rejection. The payer turnaround time for resubmission is 30 days
    from rejection notice. Claims not resubmitted within 30 days are written off as bad debt.
    """),
    textwrap.dedent("""
    CPT Code 99285 — Emergency Department Visit, High Complexity

    This code requires documentation of: presenting problem of high severity, comprehensive
    history and examination, and high-complexity medical decision making. It must NOT be used
    for visits documented as moderate complexity — that level maps to CPT 99284. Systematic
    upcoding from 99284 to 99285 is a known audit trigger for the Emergency Department.
    Payers define high complexity as decisions involving at least three diagnoses, multiple
    diagnostic studies, and assessment of new or established conditions with management options.
    """),
]

COMPLIANCE_DOCS = [
    textwrap.dedent("""
    HIPAA Privacy Rule — 45 CFR § 164.502: Uses and Disclosures of Protected Health Information

    Covered entities must not use or disclose protected health information (PHI) without valid
    patient authorisation except for treatment, payment, and healthcare operations (TPO).
    Incidental disclosures that occur as a result of a permitted use or disclosure are not a
    violation of the Privacy Rule. However, a covered entity must have in place reasonable
    safeguards to limit incidental disclosures. Failure to obtain written authorisation before
    disclosing PHI for purposes outside TPO is classified as a Critical HIPAA violation.
    Penalties range from USD 100 to USD 50,000 per violation, with an annual maximum of
    USD 1.5 million per violation category.
    """),
    textwrap.dedent("""
    HIPAA Security Rule — 45 CFR § 164.312: Technical Safeguards

    Covered entities must implement technical security measures to guard against unauthorised
    access to ePHI transmitted over electronic communications networks. Required safeguards:
    (1) Access control — unique user identification for every user who accesses ePHI systems.
    (2) Audit controls — hardware, software, and procedural mechanisms to record and examine
    activity in information systems containing ePHI. (3) Integrity controls — measures to
    authenticate ePHI and ensure it has not been altered or destroyed. (4) Transmission security —
    measures to guard against unauthorised access to ePHI transmitted over electronic networks.
    Violations of technical safeguards are classified Major severity.
    """),
    textwrap.dedent("""
    JCI Standard COP.1 — Care of Patients

    The hospital defines and implements processes to provide patient care. The care process
    includes: (a) assessment of the patient's needs, (b) planning of care to meet identified
    needs, (c) providing planned care and treatment, (d) monitoring of patient response to
    care, (e) reassessment as necessary. Each clinical process record must contain a signed
    care plan within 24 hours of admission. Absence of a signed care plan within 24 hours
    constitutes a Documentation Gap violation classified as Major severity under JCI standard COP.1.
    """),
    textwrap.dedent("""
    JCI Standard PFR.4 — Patient Consent

    The hospital has a process to obtain and document informed consent. Informed consent must be
    obtained before surgery, anaesthesia or sedation, blood and blood component transfusion, and
    high-risk treatment or procedure. A signed consent form must be present in the patient record
    before the procedure commences. Missing consent for any of these procedure types is classified
    as a ConsentMissing violation at Critical severity. JCI accreditation surveyors will cite the
    hospital for any pattern of missing consent affecting more than 5% of applicable procedures.
    """),
    textwrap.dedent("""
    Hospital SOP — Medication Administration Protocol (SOP-PHARM-003)

    The five rights of medication administration must be verified before every dose: right patient,
    right drug, right dose, right route, right time. A double-check by a second nurse is mandatory
    for high-alert medications (insulin, anticoagulants, chemotherapy). Failure to perform the
    double-check for high-alert medications constitutes a ProtocolDeviation violation at Major
    severity. All medication errors — including near-misses — must be reported to the pharmacy
    within 4 hours using the incident reporting system. Failure to report constitutes a
    ReportingFailure violation at Major severity.
    """),
]

SLA_DOCS = [
    textwrap.dedent("""
    Hospital Appointment Wait-Time SLA Policy — Version 3.1

    Appointment categories and maximum permissible wait times from scheduled time:
    - General Practitioner (GP): 30 minutes
    - Specialist Consultation: 45 minutes
    - Emergency Department triage-to-physician: 15 minutes

    Severity classification of SLA breaches:
    - Critical: wait time exceeds 2x the SLA threshold
    - High: wait time exceeds SLA threshold by 1-50%
    - Medium: wait time exceeds SLA threshold by less than 1%

    Breach response:
    - Critical breach: automatic escalation to Clinical Ops Manager within 5 minutes.
    - High breach: patient must be offered an apology voucher.
    - Medium breach: logged for trend analysis only.
    """),
    textwrap.dedent("""
    Compensation Eligibility Rules — Patient Experience Policy v2.4

    A patient is eligible for compensation if ALL of the following are true:
    1. A valid SLA breach occurred (wait time exceeded the applicable threshold).
    2. The patient has submitted a formal written or electronic complaint within 48 hours.
    3. The breach was not caused by a genuine medical emergency requiring immediate reallocation of staff.

    Compensation types:
    - Voucher: INR 500 gift voucher for the hospital pharmacy. Applicable to Medium and High breaches.
    - Refund: 50% refund of the consultation fee. Applicable to Critical breaches for Standard and Premium tier patients.
    - Escalation: Dedicated patient liaison officer. Applicable to Critical breaches for VIP tier patients or repeat breaches.

    Auto-resolution: Eligible complaints meeting Voucher criteria are auto-resolved and a digital voucher
    is issued by the system within 24 hours. All other cases are escalated to staff.
    """),
    textwrap.dedent("""
    SLA Compliance Monitoring — Monthly Reporting Requirements

    The Patient Experience team must produce a monthly SLA compliance report covering:
    (a) Total appointments by category (GP / Specialist / Emergency).
    (b) SLA breach count and percentage by category.
    (c) Average wait time and 95th-percentile wait time by category.
    (d) Total compensation issued (count and INR value by type).
    (e) Departments with SLA breach rates exceeding 10%.

    Departments exceeding 10% breach rate for two consecutive months are placed on a Performance
    Improvement Plan (PIP). The Clinical Ops Director must approve the PIP within 14 days of notification.
    """),
]


async def ingest_collection(name: str, docs: list[str]) -> None:
    print(f"  Ingesting collection '{name}'...")
    client = get_chroma_client()
    collection = get_or_create_collection(client, name)

    all_chunks, all_ids, all_metas = [], [], []
    for doc_idx, doc in enumerate(docs):
        chunks = chunk_text(doc)
        for c_idx, chunk in enumerate(chunks):
            all_chunks.append(chunk.strip())
            all_ids.append(f"{doc_id(chunk)}-{doc_idx}-{c_idx}")
            all_metas.append({"doc_index": doc_idx, "chunk_index": c_idx})

    print(f"    {len(all_chunks)} chunks to embed...")
    embeddings = await embed(all_chunks)

    collection.upsert(
        documents=all_chunks,
        embeddings=embeddings,
        ids=all_ids,
        metadatas=all_metas,
    )
    print(f"    ✓ {len(all_chunks)} chunks stored in '{name}'")


async def main() -> None:
    print("\nIngesting RAG documents into ChromaDB...")
    print("(Requires Ollama with nomic-embed-text running)")
    print(f"Ollama URL: {settings.ollama_base_url}\n")

    await ingest_collection("billing_policies", BILLING_DOCS)
    await ingest_collection("compliance_guidelines", COMPLIANCE_DOCS)
    await ingest_collection("patient_sla_policies", SLA_DOCS)

    print("\n✅ RAG ingestion complete.\n")


if __name__ == "__main__":
    asyncio.run(main())