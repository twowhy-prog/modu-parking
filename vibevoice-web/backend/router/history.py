import os
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database.db import get_db
from backend.database.models import History

router = APIRouter()


@router.get("")
def get_history(db: Session = Depends(get_db)):
    rows = db.query(History).order_by(History.created_at.desc()).all()
    return [
        {
            "job_id":     r.job_id,
            "segments":   json.loads(r.segments),
            "model":      r.model,
            "audio_url":  f"/audio/{os.path.basename(r.audio_path)}",
            "duration":   r.duration,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.delete("/{job_id}")
def delete_history(job_id: str, db: Session = Depends(get_db)):
    row = db.query(History).filter(History.job_id == job_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    try:
        if os.path.exists(row.audio_path):
            os.remove(row.audio_path)
    except OSError:
        pass
    db.delete(row)
    db.commit()
    return {"ok": True}
