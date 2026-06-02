from datetime import date, datetime
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── EMR / Billing ─────────────────────────────────────────────────────────────

class Patient(Base):
    __tablename__ = "patients"

    patient_id = Column(String(20), primary_key=True)
    full_name = Column(String(120), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(String(10), nullable=False)
    blood_type = Column(String(5))
    insurance_provider = Column(String(80))
    insurance_policy_number = Column(String(40))
    patient_tier = Column(String(20), default="Standard")  # Standard | Premium | VIP
    address = Column(Text)
    phone = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)

    encounters = relationship("Encounter", back_populates="patient")
    appointments = relationship("Appointment", back_populates="patient")
    complaints = relationship("PatientComplaint", back_populates="patient")


class Encounter(Base):
    __tablename__ = "encounters"

    encounter_id = Column(String(20), primary_key=True)
    patient_id = Column(String(20), ForeignKey("patients.patient_id"), nullable=False)
    encounter_date = Column(Date, nullable=False)
    department = Column(String(60))
    attending_physician = Column(String(80))
    primary_diagnosis_icd10 = Column(String(10), nullable=False)
    secondary_diagnosis_icd10 = Column(String(10))
    diagnosis_description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="encounters")
    billing_claims = relationship("BillingClaim", back_populates="encounter")


class BillingClaim(Base):
    __tablename__ = "billing_claims"

    claim_id = Column(String(20), primary_key=True)
    encounter_id = Column(String(20), ForeignKey("encounters.encounter_id"), nullable=False)
    patient_id = Column(String(20), ForeignKey("patients.patient_id"), nullable=False)
    cpt_code = Column(String(10), nullable=False)
    procedure_description = Column(Text)
    claim_amount = Column(Float, nullable=False)
    claim_date = Column(Date, nullable=False)
    payer_name = Column(String(80))
    claim_status = Column(String(20), default="Pending")  # Pending|Approved|Rejected|UnderReview
    rejection_reason = Column(Text)
    has_coding_error = Column(Boolean, default=False)
    error_type = Column(String(60))  # CodeMismatch|MissingModifier|Unbundling|Upcoding
    created_at = Column(DateTime, default=datetime.utcnow)

    encounter = relationship("Encounter", back_populates="billing_claims")


# ── Compliance / Audit ────────────────────────────────────────────────────────

class ClinicalProcess(Base):
    __tablename__ = "clinical_processes"

    process_id = Column(String(20), primary_key=True)
    process_type = Column(String(80), nullable=False)
    department = Column(String(60))
    staff_id = Column(String(20))
    staff_name = Column(String(80))
    procedure_date = Column(Date, nullable=False)
    patient_consent_obtained = Column(Boolean, nullable=False)
    documentation_complete = Column(Boolean, nullable=False)
    protocol_followed = Column(Boolean, nullable=False)
    incident_reported = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    audit_findings = relationship("AuditFinding", back_populates="process")


class AuditFinding(Base):
    __tablename__ = "audit_findings"

    finding_id = Column(String(20), primary_key=True)
    process_id = Column(String(20), ForeignKey("clinical_processes.process_id"), nullable=False)
    auditor_id = Column(String(20))
    audit_date = Column(Date, nullable=False)
    violation_type = Column(String(60))  # DocumentationGap|ConsentMissing|ProtocolDeviation|ReportingFailure
    severity = Column(String(20))        # Critical|Major|Minor
    regulation_body = Column(String(20)) # HIPAA|JCI|SOP
    policy_reference = Column(String(120))
    finding_description = Column(Text)
    resolution_status = Column(String(30), default="Open")  # Open|InReview|Resolved
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    process = relationship("ClinicalProcess", back_populates="audit_findings")


# ── Pharmacy ──────────────────────────────────────────────────────────────────

class Drug(Base):
    __tablename__ = "drugs"

    drug_id = Column(String(20), primary_key=True)
    drug_name = Column(String(120), nullable=False)
    generic_name = Column(String(120))
    category = Column(String(60))  # Antibiotic|Analgesic|Cardiac|Oncology|...
    unit = Column(String(20))      # tablets|vials|ml|mg
    unit_cost = Column(Float)
    supplier = Column(String(80))
    created_at = Column(DateTime, default=datetime.utcnow)

    inventory = relationship("DrugInventory", back_populates="drug")


class DrugInventory(Base):
    __tablename__ = "drug_inventory"

    inventory_id = Column(String(20), primary_key=True)
    drug_id = Column(String(20), ForeignKey("drugs.drug_id"), nullable=False)
    batch_number = Column(String(30), nullable=False)
    manufacture_date = Column(Date)
    expiry_date = Column(Date, nullable=False)
    current_stock = Column(Integer, nullable=False)
    reorder_threshold = Column(Integer, nullable=False)
    max_stock_level = Column(Integer)
    average_daily_consumption = Column(Float)  # units per day
    supplier_lead_time_days = Column(Integer, default=7)
    last_restocked = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)

    drug = relationship("Drug", back_populates="inventory")


# ── Appointments / Patient Support ────────────────────────────────────────────

class Appointment(Base):
    __tablename__ = "appointments"

    appointment_id = Column(String(20), primary_key=True)
    patient_id = Column(String(20), ForeignKey("patients.patient_id"), nullable=False)
    doctor_name = Column(String(80))
    department = Column(String(60))
    appointment_category = Column(String(20))  # GP|Specialist|Emergency
    scheduled_datetime = Column(DateTime, nullable=False)
    actual_start_datetime = Column(DateTime)
    wait_time_minutes = Column(Integer)
    appointment_status = Column(String(20), default="Scheduled")  # Scheduled|Completed|Cancelled|NoShow
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="appointments")
    complaints = relationship("PatientComplaint", back_populates="appointment")


class PatientComplaint(Base):
    __tablename__ = "patient_complaints"

    complaint_id = Column(String(20), primary_key=True)
    patient_id = Column(String(20), ForeignKey("patients.patient_id"), nullable=False)
    appointment_id = Column(String(20), ForeignKey("appointments.appointment_id"))
    complaint_date = Column(Date, nullable=False)
    complaint_text = Column(Text)
    sla_threshold_minutes = Column(Integer)
    sla_breached = Column(Boolean, default=False)
    breach_severity = Column(String(20))  # Critical|High|Medium|None
    compensation_eligible = Column(Boolean, default=False)
    compensation_type = Column(String(40))  # Voucher|Refund|Escalation|None
    resolution_status = Column(String(30), default="Open")  # Open|AutoResolved|EscalatedToStaff
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="complaints")
    appointment = relationship("Appointment", back_populates="complaints")


# ── Dispatch / Logistics ──────────────────────────────────────────────────────

class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id = Column(String(20), primary_key=True)
    vehicle_type = Column(String(40))  # Ambulance|Van|Motorcycle|Refrigerated
    registration = Column(String(20))
    capacity_kg = Column(Float)
    available = Column(Boolean, default=True)
    base_location = Column(String(80))
    created_at = Column(DateTime, default=datetime.utcnow)

    deliveries = relationship("DeliveryRecord", back_populates="vehicle")


class Route(Base):
    __tablename__ = "routes"

    route_id = Column(String(20), primary_key=True)
    origin = Column(String(80), nullable=False)
    destination = Column(String(80), nullable=False)
    distance_km = Column(Float)
    estimated_duration_minutes = Column(Integer)
    traffic_factor = Column(Float, default=1.0)  # multiplier on duration
    route_type = Column(String(30))  # Highway|Urban|Rural
    created_at = Column(DateTime, default=datetime.utcnow)


class DeliveryRecord(Base):
    __tablename__ = "delivery_records"

    delivery_id = Column(String(20), primary_key=True)
    vehicle_id = Column(String(20), ForeignKey("vehicles.vehicle_id"))
    route_id = Column(String(20), ForeignKey("routes.route_id"))
    delivery_category = Column(String(40))  # Emergency|Routine|Refrigerated
    scheduled_datetime = Column(DateTime, nullable=False)
    actual_delivery_datetime = Column(DateTime)
    sla_window_minutes = Column(Integer, nullable=False)
    estimated_cost = Column(Float)
    delivery_status = Column(String(20), default="Scheduled")  # Scheduled|InTransit|Delivered|Delayed
    sla_breached = Column(Boolean, default=False)
    delay_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    vehicle = relationship("Vehicle", back_populates="deliveries")

# ── Security / RBAC ───────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    log_id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    role = Column(String(20), nullable=False)
    action = Column(String(50), nullable=False)
    target_resource = Column(String(100))
    query_text = Column(Text)
    status = Column(String(20)) # Success | Blocked | Error


# ── Alert History (Persistent Alert Storage) ──────────────────────────────────

class AlertHistory(Base):
    __tablename__ = "alert_history"

    alert_id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String(60), nullable=False, index=True)
    domain = Column(String(30), nullable=False)
    severity = Column(String(20), nullable=False)
    subject = Column(String(255), nullable=False)
    body = Column(Text)
    trigger_values = Column(Text)  # JSON string of the trigger row values
    email_sent = Column(Boolean, default=False)
    dry_run = Column(Boolean, default=False)
    fired_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

