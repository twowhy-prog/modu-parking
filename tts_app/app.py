import os
import asyncio
import queue
import threading
import numpy as np
import torch
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from vibevoice import (
    VibeVoiceStreamingForConditionalGenerationInference,
    VibeVoiceStreamingProcessor,
)
from vibevoice.modular.streamer import AudioStreamer

SAMPLE_RATE = 24000
MODEL_PATH = os.environ.get("MODEL_PATH", "microsoft/VibeVoice-Realtime-0.5B")
DEVICE = os.environ.get("MODEL_DEVICE", "cpu")

app = FastAPI()

_model: VibeVoiceStreamingForConditionalGenerationInference = None
_processor: VibeVoiceStreamingProcessor = None
_voice_presets: dict[str, str] = {}
_lock = asyncio.Lock()


def _detect_dtype_and_attn(device: str):
    if device == "cuda":
        return torch.bfloat16, "flash_attention_2"
    return torch.float32, "sdpa"


def _load_voice_presets(voices_dir: Path) -> dict[str, str]:
    presets = {}
    if voices_dir.exists():
        for f in sorted(voices_dir.glob("*.pt")):
            presets[f.stem] = str(f)
    return presets


@app.on_event("startup")
def startup():
    global _model, _processor, _voice_presets

    dtype, attn = _detect_dtype_and_attn(DEVICE)
    print(f"[TTS] 모델 로딩 중: {MODEL_PATH}  device={DEVICE}  dtype={dtype}")

    _processor = VibeVoiceStreamingProcessor.from_pretrained(MODEL_PATH)

    try:
        _model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            MODEL_PATH,
            torch_dtype=dtype,
            attn_implementation=attn,
        ).to(DEVICE)
    except Exception:
        # flash_attention_2 없으면 sdpa로 폴백
        _model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            MODEL_PATH,
            torch_dtype=dtype,
            attn_implementation="sdpa",
        ).to(DEVICE)

    _model.eval()

    voices_dir = Path(__file__).parent / "voices"
    _voice_presets = _load_voice_presets(voices_dir)
    print(f"[TTS] 준비 완료. 보이스 프리셋: {list(_voice_presets.keys()) or '없음'}")


def _chunk_to_pcm16(chunk: torch.Tensor) -> bytes:
    audio = chunk.squeeze().cpu().float().numpy()
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767).astype(np.int16)
    return pcm.tobytes()


def _run_generation(
    text: str,
    voice_path: str | None,
    cfg: float,
    steps: int,
    audio_q: queue.Queue,
    stop_event: threading.Event,
):
    try:
        inputs = _processor.process_input_with_cached_prompt(
            text=text, return_tensors="pt"
        ).to(DEVICE)

        speech_tensors = None
        if voice_path:
            speech_tensors = torch.load(voice_path, map_location=DEVICE)

        streamer = AudioStreamer(batch_size=1)

        def _generate():
            _model.generate(
                **inputs,
                speech_tensors=speech_tensors,
                cfg_scale=cfg,
                audio_streamer=streamer,
                stop_check_fn=lambda: stop_event.is_set(),
            )
            streamer.end()

        gen_thread = threading.Thread(target=_generate, daemon=True)
        gen_thread.start()

        for chunk in streamer.get_stream(0):
            if stop_event.is_set():
                break
            audio_q.put(chunk)

        gen_thread.join(timeout=5)
    except Exception as e:
        audio_q.put(e)
    finally:
        audio_q.put(None)


@app.get("/")
async def index():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/config")
async def config():
    voices = list(_voice_presets.keys())
    return {
        "voices": voices,
        "default_voice": voices[0] if voices else None,
        "sample_rate": SAMPLE_RATE,
    }


@app.websocket("/stream")
async def stream_ws(
    ws: WebSocket,
    text: str = "",
    voice: str = "",
    cfg: float = 3.0,
    steps: int = 16,
):
    await ws.accept()

    if not text.strip():
        await ws.send_json({"error": "텍스트를 입력해주세요."})
        await ws.close()
        return

    async with _lock:
        audio_q: queue.Queue = queue.Queue()
        stop_event = threading.Event()
        voice_path = _voice_presets.get(voice) if voice else None
        if not voice_path and _voice_presets:
            voice_path = next(iter(_voice_presets.values()))

        gen_thread = threading.Thread(
            target=_run_generation,
            args=(text.strip(), voice_path, cfg, steps, audio_q, stop_event),
            daemon=True,
        )
        gen_thread.start()

        try:
            while True:
                # 청크를 논블로킹으로 폴링
                try:
                    item = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: audio_q.get(timeout=30)
                    )
                except queue.Empty:
                    await ws.send_json({"error": "생성 타임아웃"})
                    break

                if item is None:
                    break
                if isinstance(item, Exception):
                    await ws.send_json({"error": str(item)})
                    break

                pcm = _chunk_to_pcm16(item)
                await ws.send_bytes(pcm)

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
