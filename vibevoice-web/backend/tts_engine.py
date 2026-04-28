import os
import threading
import queue
from pathlib import Path

import numpy as np
import torch
import scipy.io.wavfile as wavfile

from vibevoice import (
    VibeVoiceStreamingForConditionalGenerationInference,
    VibeVoiceStreamingProcessor,
)
from vibevoice.modular.streamer import AudioStreamer

SAMPLE_RATE = 24000
VOICES_DIR  = Path(__file__).parent.parent / "voices"


def _select_device() -> tuple[str, torch.dtype, str]:
    if torch.cuda.is_available():
        return "cuda", torch.bfloat16, "flash_attention_2"
    mps = getattr(torch.backends, "mps", None)
    if mps and mps.is_available():
        return "mps", torch.float32, "sdpa"
    return "cpu", torch.float32, "sdpa"


class VibeVoiceEngine:
    def __init__(self, model_id: str = "microsoft/VibeVoice-Realtime-0.5B"):
        self.model_id   = model_id
        self.device, self.dtype, self.attn = _select_device()
        self._processor: VibeVoiceStreamingProcessor | None = None
        self._model:     VibeVoiceStreamingForConditionalGenerationInference | None = None
        self._lock       = threading.Lock()
        self._voice_cache: dict[str, torch.Tensor] = {}

    # ── 로드 ───────────────────────────────────────────────
    def load(self):
        print(f"[Engine] 로딩: {self.model_id}  device={self.device}")
        self._processor = VibeVoiceStreamingProcessor.from_pretrained(self.model_id)
        try:
            self._model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                self.model_id,
                torch_dtype=self.dtype,
                attn_implementation=self.attn,
            ).to(self.device)
        except Exception:
            self._model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                self.model_id,
                torch_dtype=torch.float32,
                attn_implementation="sdpa",
            ).to(self.device)
        self._model.eval()
        print("[Engine] 준비 완료")

    @property
    def ready(self) -> bool:
        return self._model is not None

    # ── 음성 프리셋 ────────────────────────────────────────
    def list_voices(self) -> list[dict]:
        voices = []
        if VOICES_DIR.exists():
            for f in sorted(VOICES_DIR.glob("*.pt")):
                voices.append({"name": f.stem, "path": str(f)})
        return voices

    def _get_voice_tensor(self, name: str | None) -> torch.Tensor | None:
        if not name:
            presets = self.list_voices()
            if not presets:
                return None
            name = presets[0]["name"]
        if name not in self._voice_cache:
            path = VOICES_DIR / f"{name}.pt"
            if path.exists():
                self._voice_cache[name] = torch.load(str(path), map_location=self.device)
        return self._voice_cache.get(name)

    # ── 내부: 청크 스트림 제너레이터 ─────────────────────
    def _stream_chunks(
        self,
        text: str,
        voice: str | None,
        cfg: float,
        stop_event: threading.Event,
    ):
        inputs = self._processor.process_input_with_cached_prompt(
            text=text, return_tensors="pt"
        ).to(self.device)
        speech_tensors = self._get_voice_tensor(voice)
        streamer = AudioStreamer(batch_size=1)

        def _gen():
            self._model.generate(
                **inputs,
                speech_tensors=speech_tensors,
                cfg_scale=cfg,
                audio_streamer=streamer,
                stop_check_fn=lambda: stop_event.is_set(),
            )
            streamer.end()

        t = threading.Thread(target=_gen, daemon=True)
        t.start()
        for chunk in streamer.get_stream(0):
            if stop_event.is_set():
                break
            yield chunk
        t.join(timeout=5)

    # ── 공개: 전체 생성 → WAV 저장 ────────────────────────
    def generate(
        self,
        segments: list[dict],   # [{"speaker": "화자1", "text": "...", "voice_preset": "Carter"}]
        cfg: float = 3.0,
        output_path: str | None = None,
    ) -> tuple[np.ndarray, float]:
        """
        segments의 각 발화를 순서대로 생성하여 이어 붙인 numpy 배열과 재생 시간을 반환.
        output_path 지정 시 WAV로 저장.
        """
        if not self.ready:
            raise RuntimeError("모델이 아직 로드되지 않았습니다.")

        all_audio: list[np.ndarray] = []
        stop_event = threading.Event()

        with self._lock:
            for seg in segments:
                text  = seg.get("text", "").strip()
                voice = seg.get("voice_preset") or None
                if not text:
                    continue
                chunks = list(self._stream_chunks(text, voice, cfg, stop_event))
                if chunks:
                    audio = np.concatenate([c.squeeze().cpu().float().numpy() for c in chunks])
                    all_audio.append(audio)

        if not all_audio:
            return np.zeros(0, dtype=np.float32), 0.0

        combined = np.concatenate(all_audio)
        duration = len(combined) / SAMPLE_RATE

        if output_path:
            pcm16 = (np.clip(combined, -1.0, 1.0) * 32767).astype(np.int16)
            wavfile.write(output_path, SAMPLE_RATE, pcm16)

        return combined, duration

    # ── 공개: WebSocket 스트리밍용 청크 이터레이터 ─────────
    def stream(
        self,
        text: str,
        voice: str | None = None,
        cfg: float = 3.0,
        stop_event: threading.Event | None = None,
    ):
        if not self.ready:
            raise RuntimeError("모델이 아직 로드되지 않았습니다.")
        _stop = stop_event or threading.Event()
        with self._lock:
            yield from self._stream_chunks(text, voice, cfg, _stop)

    @staticmethod
    def chunk_to_pcm16(chunk: torch.Tensor) -> bytes:
        audio = chunk.squeeze().cpu().float().numpy()
        audio = np.clip(audio, -1.0, 1.0)
        return (audio * 32767).astype(np.int16).tobytes()
