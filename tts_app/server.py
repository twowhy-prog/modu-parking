import argparse
import os
import uvicorn

parser = argparse.ArgumentParser(description="나만의 TTS 서버")
parser.add_argument("--port",       type=int,   default=7860)
parser.add_argument("--model_path", type=str,   default="microsoft/VibeVoice-Realtime-0.5B")
parser.add_argument("--device",     type=str,   default="cpu", choices=["cpu", "cuda", "mps"])
args = parser.parse_args()

os.environ["MODEL_PATH"]   = args.model_path
os.environ["MODEL_DEVICE"] = args.device

print(f"서버 시작: http://localhost:{args.port}  (device={args.device})")
uvicorn.run("app:app", host="0.0.0.0", port=args.port, reload=False)
