import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from faker import Faker
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import init_db, AsyncSessionLocal
from app.db.models import (
    Patient, Encounter, BillingClaim, ClinicalProcess, AuditFinding,
    Drug, DrugInventory, Appointment, PatientComplaint, Vehicle, Route, DeliveryRecord,
)

fake = Faker("en_IN")
random.seed(42)
Faker.seed(42)

N = settings.seed_rows_per_table  # default 2000

# ── Reference data ────────────────────────────────────────────────────────────

ICD10_CODES = [
    ("J06.9", "Acute upper respiratory infection"),
    ("I10", "Essential (primary) hypertension"),
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("K21.0", "Gastro-oesophageal reflux disease with oesophagitis"),
    ("M54.5", "Low back pain"),
    ("J18.9", "Pneumonia, unspecified organism"),
    ("N18.3", "Chronic kidney disease, stage 3"),
    ("F32.1", "Major depressive disorder, single episode, moderate"),
    ("C50.919", "Malignant neoplasm of unspecified site of unspecified female breast"),
    ("Z30.09", "Encounter for other general contraceptive management"),
]

CPT_CODES = [
    ("99213", "Office or outpatient visit, established patient, low complexity"),
    ("99214", "Office or outpatient visit, established patient, moderate complexity"),
    ("93000", "Electrocardiogram, routine ECG with at least 12 leads"),
    ("71046", "Radiologic examination, chest; 2 views"),
    ("80053", "Comprehensive metabolic panel"),
    ("99232", "Subsequent hospital care, moderate complexity"),
    ("36415", "Collection of venous blood by venipuncture"),
    ("43239", "Esophagogastroduodenoscopy, with biopsy"),
    ("27447", "Arthroplasty, knee, condyle and plateau; medial AND lateral"),
    ("99285", "Emergency department visit, high complexity"),
]

CODING_ERRORS = [
    "CodeMismatch", "MissingModifier", "Unbundling", "Upcoding",
]

PAYERS = [
    "Apollo Munich Health Insurance", "Star Health Insurance",
    "ICICI Lombard General Insurance", "Bajaj Allianz General Insurance",
    "New India Assurance", "National Insurance Company",
    "Reliance Health Insurance", "HDFC ERGO Health Insurance",
]

DEPARTMENTS = [
    "Cardiology", "Oncology", "Orthopaedics", "General Medicine",
    "Emergency", "Neurology", "Dermatology", "Gynaecology",
    "Paediatrics", "Radiology",
]

DRUG_CATALOG = [
    ("Amoxicillin 500mg", "Amoxicillin", "Antibiotic", "tablets"),
    ("Metformin 850mg", "Metformin", "Antidiabetic", "tablets"),
    ("Atorvastatin 10mg", "Atorvastatin", "Cardiovascular", "tablets"),
    ("Amlodipine 5mg", "Amlodipine", "Antihypertensive", "tablets"),
    ("Ondansetron 4mg", "Ondansetron", "Antiemetic", "tablets"),
    ("Pantoprazole 40mg", "Pantoprazole", "Gastrointestinal", "tablets"),
    ("Morphine Sulphate 10mg/ml", "Morphine", "Analgesic", "vials"),
    ("Insulin Glargine 100IU/ml", "Insulin Glargine", "Antidiabetic", "vials"),
    ("Vancomycin 500mg", "Vancomycin", "Antibiotic", "vials"),
    ("Cisplatin 50mg", "Cisplatin", "Oncology", "vials"),
    ("Dexamethasone 4mg", "Dexamethasone", "Corticosteroid", "vials"),
    ("Heparin Sodium 5000IU/ml", "Heparin", "Anticoagulant", "vials"),
    ("Azithromycin 250mg", "Azithromycin", "Antibiotic", "tablets"),
    ("Lisinopril 10mg", "Lisinopril", "Antihypertensive", "tablets"),
    ("Ceftriaxone 1g", "Ceftriaxone", "Antibiotic", "vials"),
    ("Tramadol 50mg", "Tramadol", "Analgesic", "tablets"),
    ("Folic Acid 5mg", "Folic Acid", "Vitamin", "tablets"),
    ("Omeprazole 20mg", "Omeprazole", "Gastrointestinal", "tablets"),
    ("Salbutamol 100mcg Inhaler", "Salbutamol", "Respiratory", "inhalers"),
    ("Paracetamol 500mg", "Paracetamol", "Analgesic", "tablets"),
]

SUPPLIERS = [
    "Sun Pharmaceutical Industries", "Cipla Ltd", "Dr Reddy's Laboratories",
    "Lupin Ltd", "Aurobindo Pharma", "Cadila Healthcare",
    "Mankind Pharma", "Torrent Pharmaceuticals",
]

VIOLATION_TYPES = [
    "DocumentationGap", "ConsentMissing", "ProtocolDeviation", "ReportingFailure",
]
SEVERITY_WEIGHTS = {"Critical": 0.15, "Major": 0.35, "Minor": 0.50}

VEHICLE_TYPES = ["Ambulance", "Van", "Motorcycle", "Refrigerated"]
ROUTE_TYPES = ["Highway", "Urban", "Rural"]
DELHI_LOCATIONS = [
    "AIIMS Delhi", "Safdarjung Hospital", "RML Hospital", "GTB Hospital",
    "Max Hospital Saket", "Apollo Hospital Saket", "Fortis Vasant Kunj",
    "Sir Ganga Ram Hospital", "BLK Hospital", "Lok Nanak Hospital",
    "Connaught Place Pharmacy Hub", "Karol Bagh Depot",
    "Rohini Medical Centre", "Dwarka Distribution Centre",
]

TODAY = date.today()


def rand_date(days_back: int = 365) -> date:
    return TODAY - timedelta(days=random.randint(0, days_back))


def rand_datetime(days_back: int = 365) -> datetime:
    d = rand_date(days_back)
    return datetime.combine(d, datetime.min.time()).replace(
        hour=random.randint(6, 22), minute=random.randint(0, 59)
    )


# ── Seeder functions ──────────────────────────────────────────────────────────

async def seed_patients(db: AsyncSession) -> list[str]:
    patient_ids = []
    for i in range(N):
        pid = f"PAT{i+1:06d}"
        patient_ids.append(pid)
        db.add(Patient(
            patient_id=pid,
            full_name=fake.name(),
            date_of_birth=fake.date_of_birth(minimum_age=1, maximum_age=90),
            gender=random.choice(["Male", "Female", "Other"]),
            blood_type=random.choice(["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]),
            insurance_provider=random.choice(PAYERS),
            insurance_policy_number=fake.bothify("POL-####-??????"),
            patient_tier=random.choices(["Standard", "Premium", "VIP"], weights=[0.7, 0.2, 0.1])[0],
            address=fake.address(),
            phone=fake.phone_number(),
        ))
    await db.commit()
    print(f"  ✓ {N} patients")
    return patient_ids


async def seed_billing(db: AsyncSession, patient_ids: list[str]) -> None:
    encounter_ids = []
    for i in range(N):
        pid = random.choice(patient_ids)
        enc_id = f"ENC{i+1:06d}"
        encounter_ids.append(enc_id)
        icd = random.choice(ICD10_CODES)
        db.add(Encounter(
            encounter_id=enc_id,
            patient_id=pid,
            encounter_date=rand_date(180),
            department=random.choice(DEPARTMENTS),
            attending_physician=fake.name(),
            primary_diagnosis_icd10=icd[0],
            diagnosis_description=icd[1],
        ))

    await db.flush()

    for i in range(N):
        has_error = random.random() < 0.20
        cpt = random.choice(CPT_CODES)
        status = random.choices(
            ["Pending", "Approved", "Rejected", "UnderReview"],
            weights=[0.30, 0.50, 0.12, 0.08]
        )[0]
        db.add(BillingClaim(
            claim_id=f"CLM{i+1:06d}",
            encounter_id=encounter_ids[i],
            patient_id=random.choice(patient_ids),
            cpt_code=cpt[0],
            procedure_description=cpt[1],
            claim_amount=round(random.uniform(500, 150000), 2),
            claim_date=rand_date(180),
            payer_name=random.choice(PAYERS),
            claim_status=status,
            rejection_reason=fake.sentence() if status == "Rejected" else None,
            has_coding_error=has_error,
            error_type=random.choice(CODING_ERRORS) if has_error else None,
        ))

    await db.commit()
    print(f"  ✓ {N} encounters + {N} billing claims")


async def seed_compliance(db: AsyncSession) -> None:
    process_ids = []
    for i in range(N):
        pid = f"PRC{i+1:06d}"
        process_ids.append(pid)
        non_compliant = random.random() < 0.18
        db.add(ClinicalProcess(
            process_id=pid,
            process_type=random.choice([
                "Surgical Procedure", "Patient Admission", "Drug Administration",
                "Blood Transfusion", "ICU Transfer", "Discharge Process",
                "Radiology Order", "Consent Procedure",
            ]),
            department=random.choice(DEPARTMENTS),
            staff_id=f"STF{random.randint(1, 200):04d}",
            staff_name=fake.name(),
            procedure_date=rand_date(365),
            patient_consent_obtained=not non_compliant if random.random() < 0.4 else True,
            documentation_complete=not non_compliant if random.random() < 0.5 else True,
            protocol_followed=not non_compliant if random.random() < 0.6 else True,
            incident_reported=random.random() < 0.05,
        ))

    await db.flush()

    finding_count = 0
    for i, pid in enumerate(process_ids):
        if random.random() < 0.18:
            severity = random.choices(
                list(SEVERITY_WEIGHTS.keys()),
                weights=list(SEVERITY_WEIGHTS.values())
            )[0]
            db.add(AuditFinding(
                finding_id=f"FND{finding_count+1:06d}",
                process_id=pid,
                auditor_id=f"AUD{random.randint(1, 30):03d}",
                audit_date=rand_date(90),
                violation_type=random.choice(VIOLATION_TYPES),
                severity=severity,
                regulation_body=random.choice(["HIPAA", "JCI", "SOP"]),
                policy_reference=f"Section {random.randint(1, 12)}.{random.randint(1, 20)}",
                finding_description=fake.paragraph(nb_sentences=2),
                resolution_status=random.choice(["Open", "InReview", "Resolved"]),
            ))
            finding_count += 1

    await db.commit()
    print(f"  ✓ {N} clinical processes + {finding_count} audit findings")


async def seed_pharmacy(db: AsyncSession) -> None:
    for i, (name, generic, cat, unit) in enumerate(DRUG_CATALOG):
        db.add(Drug(
            drug_id=f"DRG{i+1:04d}",
            drug_name=name,
            generic_name=generic,
            category=cat,
            unit=unit,
            unit_cost=round(random.uniform(2, 5000), 2),
            supplier=random.choice(SUPPLIERS),
        ))

    await db.flush()

    batch_count = 0
    for drug_idx in range(len(DRUG_CATALOG)):
        drug_id = f"DRG{drug_idx+1:04d}"
        batches_per_drug = N // len(DRUG_CATALOG)
        for b in range(batches_per_drug):
            expiry = TODAY + timedelta(days=random.choice([
                random.randint(-30, 30),   # near/past expiry (critical)
                random.randint(31, 60),    # 30-60 days (high alert)
                random.randint(61, 90),    # 60-90 days (medium)
                random.randint(91, 730),   # safe
            ]))
            stock = random.randint(0, 5000)
            threshold = random.randint(50, 500)
            db.add(DrugInventory(
                inventory_id=f"INV{batch_count+1:07d}",
                drug_id=drug_id,
                batch_number=fake.bothify("BAT-????-####"),
                manufacture_date=expiry - timedelta(days=random.randint(180, 730)),
                expiry_date=expiry,
                current_stock=stock,
                reorder_threshold=threshold,
                max_stock_level=threshold * random.randint(4, 10),
                average_daily_consumption=round(random.uniform(1, 80), 2),
                supplier_lead_time_days=random.randint(3, 21),
                last_restocked=rand_date(60),
            ))
            batch_count += 1

    await db.commit()
    print(f"  ✓ {len(DRUG_CATALOG)} drugs + {batch_count} inventory batches")


async def seed_appointments(db: AsyncSession, patient_ids: list[str]) -> None:
    SLA_THRESHOLDS = {"GP": 30, "Specialist": 45, "Emergency": 15}
    appt_ids = []

    for i in range(N):
        aid = f"APT{i+1:06d}"
        appt_ids.append(aid)
        pid = random.choice(patient_ids)
        cat = random.choice(["GP", "Specialist", "Emergency"])
        scheduled = rand_datetime(180)
        delay = random.choice([0, 0, 0, 5, 10, 15, 20, 30, 45, 60, 90])
        actual = scheduled + timedelta(minutes=delay)
        db.add(Appointment(
            appointment_id=aid,
            patient_id=pid,
            doctor_name=fake.name(),
            department=random.choice(DEPARTMENTS),
            appointment_category=cat,
            scheduled_datetime=scheduled,
            actual_start_datetime=actual,
            wait_time_minutes=delay,
            appointment_status=random.choices(
                ["Completed", "Cancelled", "NoShow"],
                weights=[0.80, 0.12, 0.08]
            )[0],
        ))

    await db.flush()

    complaint_count = 0
    for i, aid in enumerate(appt_ids):
        if random.random() < 0.15:
            cat = random.choice(["GP", "Specialist", "Emergency"])
            threshold = SLA_THRESHOLDS[cat]
            wait = random.randint(0, 120)
            breached = wait > threshold
            severe = "Critical" if wait > threshold * 2 else ("High" if breached else "None")
            comp_eligible = breached and random.random() < 0.6
            db.add(PatientComplaint(
                complaint_id=f"CMP{complaint_count+1:06d}",
                patient_id=random.choice(patient_ids),
                appointment_id=aid,
                complaint_date=rand_date(90),
                complaint_text=fake.paragraph(nb_sentences=2),
                sla_threshold_minutes=threshold,
                sla_breached=breached,
                breach_severity=severe,
                compensation_eligible=comp_eligible,
                compensation_type=random.choice(["Voucher", "Refund", "Escalation"]) if comp_eligible else "None",
                resolution_status=random.choices(["Open", "AutoResolved", "EscalatedToStaff"], weights=[0.3, 0.5, 0.2])[0],
            ))
            complaint_count += 1

    await db.commit()
    print(f"  ✓ {N} appointments + {complaint_count} complaints")


async def seed_dispatch(db: AsyncSession) -> None:
    for i in range(40):
        db.add(Vehicle(
            vehicle_id=f"VEH{i+1:04d}",
            vehicle_type=random.choice(VEHICLE_TYPES),
            registration=fake.bothify("DL-##-??-####"),
            capacity_kg=random.choice([200, 500, 1000, 1500]),
            available=random.random() > 0.2,
            base_location=random.choice(DELHI_LOCATIONS),
        ))

    for i in range(60):
        origin = random.choice(DELHI_LOCATIONS)
        dest = random.choice([l for l in DELHI_LOCATIONS if l != origin])
        dist = round(random.uniform(2, 45), 1)
        traffic = random.choice([0.9, 1.0, 1.1, 1.3, 1.5, 1.8])
        db.add(Route(
            route_id=f"RTE{i+1:04d}",
            origin=origin,
            destination=dest,
            distance_km=dist,
            estimated_duration_minutes=int(dist * random.uniform(2, 5)),
            traffic_factor=traffic,
            route_type=random.choice(ROUTE_TYPES),
        ))

    await db.flush()

    SLA_WINDOWS = {"Emergency": 30, "Routine": 120, "Refrigerated": 60}
    for i in range(N):
        cat = random.choice(["Emergency", "Routine", "Refrigerated"])
        sla = SLA_WINDOWS[cat]
        sched = rand_datetime(90)
        actual = sched + timedelta(minutes=random.randint(0, sla + 60))
        delivered_on_time = (actual - sched).seconds // 60 <= sla
        db.add(DeliveryRecord(
            delivery_id=f"DEL{i+1:06d}",
            vehicle_id=f"VEH{random.randint(1, 40):04d}",
            route_id=f"RTE{random.randint(1, 60):04d}",
            delivery_category=cat,
            scheduled_datetime=sched,
            actual_delivery_datetime=actual,
            sla_window_minutes=sla,
            estimated_cost=round(random.uniform(200, 8000), 2),
            delivery_status=random.choices(["Delivered", "Delayed", "InTransit"], weights=[0.7, 0.2, 0.1])[0],
            sla_breached=not delivered_on_time,
            delay_reason=fake.sentence() if not delivered_on_time else None,
        ))

    await db.commit()
    print(f"  ✓ 40 vehicles + 60 routes + {N} delivery records")


async def main() -> None:
    print(f"\nSeeding database with N={N} rows per domain...")
    await init_db()

    async with AsyncSessionLocal() as db:
        patient_ids = await seed_patients(db)

    async with AsyncSessionLocal() as db:
        await seed_billing(db, patient_ids)

    async with AsyncSessionLocal() as db:
        await seed_compliance(db)

    async with AsyncSessionLocal() as db:
        await seed_pharmacy(db)

    async with AsyncSessionLocal() as db:
        await seed_appointments(db, patient_ids)

    async with AsyncSessionLocal() as db:
        await seed_dispatch(db)

    print("\n✅ Database seeding complete.\n")


if __name__ == "__main__":
    asyncio.run(main())
