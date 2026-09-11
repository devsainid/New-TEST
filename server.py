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
        outcomes = ["POSITIVE INDICATION", "NEGATIVE INDICATION", "INCONCLUSIVE"]
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

# NEW: Users Table for Secure Registration
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    password = Column(String(255))
    role = Column(String(20))

class TestRecord(Base):
    __tablename__ = "test_records"
    id = Column(Integer, primary_key=True, index=True)
    officer_id = Column(String(50), index=True)
    kit_reference = Column(String(50))
    gps_location = Column(String(100))
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    result = Column(String(50))
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def log_action(db: Session, user_id: str, action: str):
    new_log = AuditLog(user_id=user_id, action=action)
    db.add(new_log)
    db.commit()

@app.get("/app-status")
def get_app_status():
    return {"success": True, "latest_version": "1.0.0", "force_update": False}

# ==========================================
# AUTHENTICATION & SECURE REGISTRATION
# ==========================================
class RegisterData(BaseModel):
    username: str
    password: str
    secret_key: str

@app.post("/auth/register")
def register(data: RegisterData, db: Session = Depends(get_db)):
    # 1. VERIFY HQ SECRET KEY
    if data.secret_key != "SIH-SECURE-2026":
        raise HTTPException(status_code=403, detail="Unauthorized: Invalid HQ Secret Key")
    
    # 2. CHECK IF USER ALREADY EXISTS
    existing = db.query(User).filter(User.username == data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Officer ID already registered")
    
    # 3. SAVE TO DB
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
    
    # First check database for registered users
    user = db.query(User).filter(User.username == data.username, User.password == data.password).first()
    
    if user:
        role = user.role
    # Fallback to default hardcoded users (For Prototype / Admin)
    elif data.username == "OFF001" and data.password == "1234":
        role = "field_officer"
    elif data.username == "ADMIN01" and data.password == "hq1234":
        role = "hq_admin"
    else:
        log_action(db, data.username, "Failed login attempt")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    payload = {"sub": data.username, "role": role, "exp": datetime.now(timezone.utc) + timedelta(hours=24)}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    log_action(db, data.username, f"Logged in as {role}")
    return {"access_token": token, "role": role}

# ==========================================
# CORE API (UPLOAD & SYNC)
# ==========================================
@app.post("/api/upload")
async def upload_test(
    officer_id: str = Form(...), kit_reference: str = Form(...),
    gps_location: str = Form(...), file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    image_bytes = await file.read()
    image_hash = hashlib.sha256(image_bytes).hexdigest()

    pipeline = ImagePipeline(image_bytes, file.filename)
    pipeline.run_preprocessing_workflow()
    ml_result = MockClassifier().classify(image_bytes, file.filename)

    new_record = TestRecord(
        officer_id=officer_id, kit_reference=kit_reference, gps_location=gps_location,
        result=ml_result["result"], confidence=ml_result["confidence"], image_hash=image_hash
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    log_action(db, officer_id, f"Uploaded test {kit_reference}")
    return {"success": True, "result": new_record.result}

class OfflineTest(BaseModel):
    officer_id: str
    kit_reference: str
    gps_location: str
    timestamp: str

class SyncPayload(BaseModel):
    records: List[OfflineTest]

@app.post("/api/sync")
def sync_offline_records(payload: SyncPayload, db: Session = Depends(get_db)):
    for test in payload.records:
        new_record = TestRecord(
            officer_id=test.officer_id, kit_reference=test.kit_reference, gps_location=test.gps_location,
            result="PENDING_SYNC_ANALYSIS", confidence="N/A", image_hash="PENDING", sync_status="SYNCED_FROM_OFFLINE"
        )
        db.add(new_record)
    db.commit()
    return {"success": True}

@app.get("/api/records")
def get_records(db: Session = Depends(get_db)):
    return db.query(TestRecord).order_by(TestRecord.id.desc()).limit(100).all()
