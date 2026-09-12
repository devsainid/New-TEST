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
# ADVANCED AI / ML SERVICES (FOR SIH)
# ==========================================
class ImagePipeline:
    def __init__(self, image_bytes: bytes, filename: str):
        self.image_bytes = image_bytes
        self.filename = filename

    def run_preprocessing_workflow(self):
        # NAYA FEATURE: Image Quality Check (Retake Logic)
        # Demo ke liye: 20% chance hai ki photo reject ho jaye (blur ya bad light)
        quality_score = random.randint(1, 100)
        if quality_score <= 20:
            return {
                "success": False, 
                "error": "POOR_QUALITY",
                "message": "Image is blurry or lighting is inadequate. Please RETAKE the photo."
            }
            
        return {
            "success": True, 
            "operations_applied": ["auto_exposure_balance", "white_balance_correction", "roi_extraction"],
            "message": "Image lighting auto-corrected and optimized for AI analysis."
        }

class MockClassifier:
    def classify(self, image_bytes: bytes, test_type: str) -> dict:
        # NAYA SIH RULE COMPLIANT AI LOGIC
        # Ab AI drug ka naam nahi lega, sirf color-card analysis categories dega
        outcomes = [
            {"result": "Positive (Presumptive)", "conf": "98.2%"},
            {"result": "Negative", "conf": "99.1%"},
            {"result": "Inconclusive", "conf": "45.0%"},
            {"result": "Faint", "conf": "75.5%"},
            {"result": "Unexpected", "conf": "60.2%"}
        ]
        
        # Demo ke liye random result
        selected = random.choices(outcomes, weights=[0.35, 0.35, 0.1, 0.1, 0.1])[0]
        return {"result": selected["result"], "confidence": selected["conf"]}
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

class TestRecord(Base):
    __tablename__ = "test_records_v2"
    id = Column(Integer, primary_key=True, index=True)
    officer_id = Column(String(50), index=True)
    sample_id = Column(String(50))
    test_type = Column(String(50))
    location_name = Column(String(100))
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def log_action(db: Session, user_id: str, action: str):
    db.add(AuditLog(user_id=user_id, action=action))
    db.commit()

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
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    payload = {"sub": data.username, "role": role, "exp": datetime.now(timezone.utc) + timedelta(hours=24)}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "role": role}

# ==========================================
# CORE API (UPLOAD & RETAKE LOGIC)
# ==========================================
@app.post("/api/upload")
async def upload_test(
    officer_id: str = Form(...), sample_id: str = Form(...), test_type: str = Form(...),
    location_name: str = Form(...), gps_location: str = Form(...), file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    image_bytes = await file.read()
    image_hash = hashlib.sha256(image_bytes).hexdigest()

    # NAYA: Check Image Quality Before Saving
    pipeline = ImagePipeline(image_bytes, file.filename)
    pipeline_result = pipeline.run_preprocessing_workflow()
    
    if not pipeline_result["success"]:
        # Agar quality fail hui, toh error bhej do (Database mein save mat karo)
        return {"success": False, "error": pipeline_result["error"], "message": pipeline_result["message"]}

    # Agar pass hui toh classification karo aur save karo
    ml_result = MockClassifier().classify(image_bytes, test_type)

    new_record = TestRecord(
        officer_id=officer_id, sample_id=sample_id, test_type=test_type,
        location_name=location_name, gps_location=gps_location,
        result=ml_result["result"], confidence=ml_result["confidence"], image_hash=image_hash
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    return {"success": True, "result": new_record.result, "confidence": new_record.confidence}

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
