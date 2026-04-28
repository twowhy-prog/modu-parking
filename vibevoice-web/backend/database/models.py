from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, Text
from backend.database.db import Base


class History(Base):
    __tablename__ = "history"

    job_id     = Column(String, primary_key=True, index=True)
    segments   = Column(Text,   nullable=False)   # JSON 문자열
    model      = Column(String, nullable=False)
    audio_path = Column(String, nullable=False)
    duration   = Column(Float,  default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
