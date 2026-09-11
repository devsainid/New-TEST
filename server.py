import os
import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from dotenv import load_dotenv
import jwt

# ==========================================
# AI / ML SERVICES
# ==========================================
class ImagePipeline:
    def __init__(self, image_bytes: bytes, filename: str):
        self.image_bytes = image_bytes
        self.filename = filename
    def run_preprocessing_workflow(self):
        return {"success": True, "message": "CV Pipeline passed.", "roi_found": True}

class MockClassifier:
    def classify(self, image_bytes: bytes, filename: str) -> dict:
        outcomes = ["Positive", "Negative", "Inconclusive"]
        result = random.choices(outcomes, weights=[0.4, 0.4, 0.2])[0]
        return {"result": result, "confidence": "95.5%", "model_type": "Simulated"}

# ==========================================
# DB & ENV SETUP
# ==========================================
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "my_super_secret_key_for_sih_2026")
ALGORITHM = "HS256"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    password = Column(String(255))
    role = Column(String(20))

# NAYI TABLE (V2) IMAGE WALE FORMAT KE LIYE
class TestRecord(Base):
    __tablename__ = "test_records_v2"
    id = Column(Integer, primary_key=True, index=True)
    officer_id = Column(String(50), index=True)       # Analyst
    sample_id = Column(String(50))                    # Sample ID (e.g. A-12)
    test_type = Column(String(50))                    # Test Type (e.g. NIK)
    location_name = Column(String(100))               # Location (e.g. Site - Warehouse)
    gps_location = Column(String(100))                # Digital GPS Backup
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc)) # Date & Time
    result = Column(String(50))                       # Result (Positive)
    confidence = Column(String(20))
    image_hash = Column(String(64))
    sync_status = Column(String(20), default="ONLINE")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(50))
    action = Column(String(255))
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(bind=engine)

app = FastAPI(title="SIH 2026 FieldTest API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def log_action(db: Session, user_id: str, action: str):
    db.add(AuditLog(user_id=user_id, action=action))
    db.commit()

@app.get("/app-status")
def get_app_status():
    return {"success": True, "latest_version": "1.0.0", "force_update": False}

# ==========================================
# AUTHENTICATION
# ==========================================
class RegisterData(BaseModel):
    username: str
    password: str
    secret_key: str

@app.post("/auth/register")
def register(data: RegisterData, db: Session = Depends(get_db)):
    if data.secret_key != "SIH-SECURE-2026":
        raise HTTPException(status_code=403, detail="Unauthorized: Invalid HQ Secret Key")
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Officer ID already registered")
    
    new_user = User(username=data.username, password=data.password, role="field_officer")
    db.add(new_user)
    db.commit()
    log_action(db, data.username, "New Officer Registered")
    return {"success": True, "message": "Officer Authenticated & Registered Successfully"}

class LoginData(BaseModel):
    username: str
    password: str

@app.post("/auth/login")
def login(data: LoginData, db: Session = Depends(get_db)):
    role = None
    user = db.query(User).filter(User.username == data.username, User.password == data.password).first()
    if user: role = user.role
    elif data.username == "OFF001" and data.password == "1234": role = "field_officer"
    elif data.username == "ADMIN01" and data.password == "hq1234": role = "hq_admin"
    else:
        log_action(db, data.username, "Failed login attempt")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    payload = {"sub": data.username, "role": role, "exp": datetime.now(timezone.utc) + timedelta(hours=24)}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    log_action(db, data.username, f"Logged in as {role}")
    return {"access_token": token, "role": role}

# ==========================================
# CORE API (UPLOAD & SYNC FORMATTED TO IMAGE)
# ==========================================
@app.post("/api/upload")
async def upload_test(
    officer_id: str = Form(...), 
    sample_id: str = Form(...),
    test_type: str = Form(...),
    location_name: str = Form(...),
    gps_location: str = Form(...), 
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    image_bytes = await file.read()
    image_hash = hashlib.sha256(image_bytes).hexdigest()

    ImagePipeline(image_bytes, file.filename).run_preprocessing_workflow()
    ml_result = MockClassifier().classify(image_bytes, file.filename)

    new_record = TestRecord(
        officer_id=officer_id, sample_id=sample_id, test_type=test_type,
        location_name=location_name, gps_location=gps_location,
        result=ml_result["result"], confidence=ml_result["confidence"], image_hash=image_hash
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    return {"success": True, "result": new_record.result}

class OfflineTest(BaseModel):
    officer_id: str
    sample_id: str
    test_type: str
    location_name: str
    gps_location: str
    timestamp: str

class SyncPayload(BaseModel):
    records: List[OfflineTest]

@app.post("/api/sync")
def sync_offline_records(payload: SyncPayload, db: Session = Depends(get_db)):
    for test in payload.records:
        new_record = TestRecord(
            officer_id=test.officer_id, sample_id=test.sample_id, test_type=test.test_type,
            location_name=test.location_name, gps_location=test.gps_location,
            result="PENDING_SYNC_ANALYSIS", confidence="N/A", image_hash="PENDING", sync_status="SYNCED_FROM_OFFLINE"
        )
        db.add(new_record)
    db.commit()
    return {"success": True}

@app.get("/api/records")
def get_records(db: Session = Depends(get_db)):
    return db.query(TestRecord).order_by(TestRecord.id.desc()).limit(100).all()
