from fastapi import APIRouter
from backend.tts_engine import VibeVoiceEngine

router = APIRouter()
_engine: VibeVoiceEngine | None = None


def set_engine(engine: VibeVoiceEngine):
    global _engine
    _engine = engine


@router.get("")
def list_voices():
    if _engine is None:
        return []
    return _engine.list_voices()
