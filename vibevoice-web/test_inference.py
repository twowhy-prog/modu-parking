"""
Phase 1 환경 확인 스크립트
실행: python test_inference.py
"""

import sys
import platform

def check(label, fn):
    try:
        result = fn()
        print(f"  [OK] {label}: {result}")
        return True
    except Exception as e:
        print(f"  [FAIL] {label}: {e}")
        return False


print("=" * 55)
print("  VoiceStudio — 환경 확인")
print("=" * 55)

# ── 1. Python 버전 ────────────────────────────────────────
print("\n[1] Python 환경")
check("Python 버전", lambda: platform.python_version())
check("OS", lambda: platform.system() + " " + platform.release())

# ── 2. PyTorch / CUDA ─────────────────────────────────────
print("\n[2] PyTorch / GPU")
try:
    import torch
    check("PyTorch 버전", lambda: torch.__version__)
    check("CUDA 사용 가능", lambda: f"{torch.cuda.is_available()} (device count={torch.cuda.device_count()})")
    if torch.cuda.is_available():
        check("GPU 이름", lambda: torch.cuda.get_device_name(0))
        vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        check("GPU VRAM", lambda: f"{vram:.1f} GB")
    mps = getattr(torch.backends, "mps", None)
    if mps:
        check("Apple MPS 사용 가능", lambda: mps.is_available())
except ImportError:
    print("  [FAIL] PyTorch 미설치 — pip install torch 실행 필요")
    sys.exit(1)

# ── 3. 핵심 패키지 ────────────────────────────────────────
print("\n[3] 핵심 패키지")
packages = [
    ("transformers", "transformers"),
    ("fastapi",      "fastapi"),
    ("uvicorn",      "uvicorn"),
    ("soundfile",    "soundfile"),
    ("sqlalchemy",   "sqlalchemy"),
    ("scipy",        "scipy"),
]
for name, mod in packages:
    check(name, lambda m=mod: __import__(m).__version__)

# ── 4. VibeVoice 패키지 ───────────────────────────────────
print("\n[4] VibeVoice 패키지")
vv_ok = check(
    "vibevoice import",
    lambda: str(__import__("vibevoice")),
)

# ── 5. 모델 다운로드 테스트 ───────────────────────────────
if "--skip-model" not in sys.argv:
    print("\n[5] 모델 다운로드 테스트 (Realtime-0.5B)")
    print("    첫 실행 시 수 GB 다운로드될 수 있습니다...")
    print("    건너뛰려면: python test_inference.py --skip-model")

    if vv_ok:
        try:
            import os, time
            from vibevoice import (
                VibeVoiceStreamingForConditionalGenerationInference,
                VibeVoiceStreamingProcessor,
            )
            model_id = os.getenv("REALTIME_MODEL", "microsoft/VibeVoice-Realtime-0.5B")

            t0 = time.time()
            processor = VibeVoiceStreamingProcessor.from_pretrained(model_id)
            model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                model_id, torch_dtype=torch.float32, attn_implementation="sdpa"
            )
            elapsed = time.time() - t0
            print(f"  [OK] 모델 로드 완료 ({elapsed:.1f}s)")

            # 짧은 추론 테스트
            t0 = time.time()
            inputs = processor(text="Hello, this is a test.", return_tensors="pt")
            from vibevoice.modular.streamer import AudioStreamer
            streamer = AudioStreamer(batch_size=1)
            import threading
            chunks = []

            def gen():
                model.generate(**inputs, audio_streamer=streamer, cfg_scale=1.0)
                streamer.end()

            t = threading.Thread(target=gen, daemon=True)
            t.start()
            for chunk in streamer.get_stream(0):
                chunks.append(chunk)
            t.join(timeout=60)

            import numpy as np
            total_samples = sum(c.numel() for c in chunks)
            duration = total_samples / 24000
            rtf = (time.time() - t0) / max(duration, 0.001)
            print(f"  [OK] 추론 완료 — 오디오 {duration:.1f}s 생성, RTF={rtf:.2f}")

            # WAV 저장
            import scipy.io.wavfile as wav
            audio = np.concatenate([c.squeeze().cpu().float().numpy() for c in chunks])
            wav.write("test_output.wav", 24000, (audio * 32767).astype(np.int16))
            print("  [OK] test_output.wav 저장됨")

        except Exception as e:
            print(f"  [FAIL] 모델 테스트 실패: {e}")
    else:
        print("  [SKIP] vibevoice 미설치 — pip install -r requirements.txt 실행 필요")
else:
    print("\n[5] 모델 테스트 건너뜀 (--skip-model)")

print("\n" + "=" * 55)
print("  완료! 문제 없으면 Phase 2로 진행하세요.")
print("=" * 55)
