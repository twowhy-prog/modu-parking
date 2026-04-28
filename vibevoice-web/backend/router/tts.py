import json
import uuid
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.db import get_db
from backend.database.models import History
from backend.tts_engine import VibeVoiceEngine

router  = APIRouter()
_engine: VibeVoiceEngine | None = None
_ws_lock = threading.Lock()   # WebSocket 동시 생성 방지


def set_engine(engine: VibeVoiceEngine):
    global _engine
    _engine = engine


# ── REST: 다화자 생성 ─────────────────────────────────────
class Segment(BaseModel):
    speaker:      str = "화자1"
    text:         str
    voice_preset: str = ""


class GenerateRequest(BaseModel):
    segments:      list[Segment]
    model:         str  = "realtime-0.5b"
    cfg:           float = 3.0
    output_format: str  = "wav"


AUDIO_DIR = Path(__file__).parent.parent / "audio_outputs"


@router.post("/generate")
def generate(req: GenerateRequest, db: Session = Depends(get_db)):
    if _engine is None or not _engine.ready:
        from fastapi import HTTPException
        raise HTTPException(503, "모델 준비 중입니다. 잠시 후 다시 시도하세요.")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    job_id     = f"tts_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    audio_path = str(AUDIO_DIR / f"{job_id}.wav")

    segs = [s.model_dump() for s in req.segments]
    _, duration = _engine.generate(segs, cfg=req.cfg, output_path=audio_path)

    row = History(
        job_id=job_id,
        segments=json.dumps(segs, ensure_ascii=False),
        model=req.model,
        audio_path=audio_path,
        duration=duration,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()

    return {
        "job_id":   job_id,
        "audio_url": f"/audio/{job_id}.wav",
        "duration": round(duration, 2),
        "speakers": list({s.speaker for s in req.segments}),
        "created_at": row.created_at.isoformat(),
    }


# ── WebSocket: 실시간 스트리밍 ────────────────────────────
@router.websocket("/stream")
async def stream_ws(
    ws: WebSocket,
    text:  str   = "",
    voice: str   = "",
    cfg:   float = 3.0,
):
    import asyncio
    await ws.accept()

    if not text.strip():
        await ws.send_json({"error": "텍스트를 입력해주세요."})
        await ws.close()
        return

    if _engine is None or not _engine.ready:
        await ws.send_json({"error": "모델 준비 중입니다."})
        await ws.close()
        return

    stop_event = threading.Event()
    chunk_q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _produce():
        try:
            for chunk in _engine.stream(text.strip(), voice or None, cfg, stop_event):
                pcm = VibeVoiceEngine.chunk_to_pcm16(chunk)
                loop.call_soon_threadsafe(chunk_q.put_nowait, pcm)
        except Exception as e:
            loop.call_soon_threadsafe(chunk_q.put_nowait, {"error": str(e)})
        finally:
            loop.call_soon_threadsafe(chunk_q.put_nowait, None)

    t = threading.Thread(target=_produce, daemon=True)
    t.start()

    try:
        while True:
            item = await asyncio.wait_for(chunk_q.get(), timeout=30)
            if item is None:
                break
            if isinstance(item, dict):
                await ws.send_json(item)
                break
            await ws.send_bytes(item)
    except asyncio.TimeoutError:
        await ws.send_json({"error": "생성 타임아웃"})
    except WebSocketDisconnect:
        stop_event.set()
    except Exception as e:
        stop_event.set()
        try:
            await ws.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        stop_event.set()
        try:
            await ws.close()
        except Exception:
            pass
