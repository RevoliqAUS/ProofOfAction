from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import msgpack

# 确保这些模块在你的 src 目录下
from src.analyzer.multimodal_llm import VideoAnalyzer
from src.blockchain.onchain_settle import BlockchainNotary

app = FastAPI()

# 解决跨域拦截
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
    return {"status": "PoA Online"}

@app.post("/analyze")
async def analyze_video(
    video: UploadFile = File(...),
    rule_description: str = Form(...),
):
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
        return Response(content=msgpack.packb(response_data), media_type="application/x-msgpack")
    finally:
        if os.path.exists(temp_filepath): os.remove(temp_filepath)
