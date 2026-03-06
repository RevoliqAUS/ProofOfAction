from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import msgpack
import sys

# 关键：确保 Vercel 能找到 src 目录下的模块
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

try:
    from src.analyzer.multimodal_llm import VideoAnalyzer
    from src.blockchain.onchain_settle import BlockchainNotary
except ImportError:
    from analyzer.multimodal_llm import VideoAnalyzer
    from blockchain.onchain_settle import BlockchainNotary

# Vercel 需要这个 app 实例
app = FastAPI()

# --- 强制开启 CORS 补丁 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

video_analyzer = VideoAnalyzer()
blockchain_notary = BlockchainNotary()

@app.get("/")
async def root():
    return {"status": "PoA Online", "version": "1.0.1"}

@app.post("/analyze")
async def analyze_video(
    video: UploadFile = File(...),
    rule_description: str = Form(...),
):
    # Vercel 只允许写入 /tmp
    temp_filepath = f"/tmp/{uuid.uuid4()}_{video.filename}"
    try:
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # 1. AI 分析
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        # 2. 存证
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        response_data = {
            "analysis": result,
            "notary": {"video_hash": video_hash, "attestation": attestation}
        }
        # 显式返回 Response 确保 Header 被正确发送
        return Response(
            content=msgpack.packb(response_data), 
            media_type="application/x-msgpack"
        )
    except Exception as e:
        # 如果报错，也打包成 msgpack 返回
        return Response(
            content=msgpack.packb({"error": str(e)}), 
            status_code=500,
            media_type="application/x-msgpack"
        )
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
