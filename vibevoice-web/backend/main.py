import os
import sys
import argparse
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.database.db import init_db
from backend.tts_engine import VibeVoiceEngine
from backend.router import tts as tts_router
from backend.router import voices as voices_router
from backend.router import history as history_router

# ── 앱 초기화 ─────────────────────────────────────────────
app = FastAPI(title="VoiceStudio API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_DIR    = ROOT / "backend" / "audio_outputs"
FRONTEND_DIR = ROOT / "frontend"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")

# ── 라우터 등록 ───────────────────────────────────────────
app.include_router(tts_router.router,     prefix="/api/tts")
app.include_router(voices_router.router,  prefix="/api/voices")
app.include_router(history_router.router, prefix="/api/history")

# ── 프론트엔드 서빙 ───────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))

@app.get("/{path:path}")
def frontend_static(path: str):
    target = FRONTEND_DIR / path
    if target.exists() and target.is_file():
        return FileResponse(str(target))
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# ── 시작 이벤트 ───────────────────────────────────────────
engine: VibeVoiceEngine | None = None


@app.on_event("startup")
def startup():
    global engine
    init_db()

    model_id = os.getenv("REALTIME_MODEL", "microsoft/VibeVoice-Realtime-0.5B")
    engine = VibeVoiceEngine(model_id=model_id)
    engine.load()

    tts_router.set_engine(engine)
    voices_router.set_engine(engine)


# ── 진입점 ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 7860)))
    args = parser.parse_args()

    print(f"\n  VoiceStudio 시작: http://localhost:{args.port}\n")
    uvicorn.run("backend.main:app", host=args.host, port=args.port, reload=False)
