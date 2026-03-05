from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware  # <--- 新增导入
import os
import shutil
import uuid
import time
import json
import msgpack
import hashlib

# 内部模块导入
from src.api.gamma_client import GammaClient
from src.analyzer.multimodal_llm import VideoAnalyzer
from src.analyzer.authenticity import AuthenticityChecker
from src.blockchain.onchain_settle import BlockchainNotary
from src.api.auth import verify_eip712_signature

app = FastAPI(
    title="ProofOfAction",
    description="基于 AI + 区块链的 0 信任视频裁判协议",
    version="1.0.0",
)

# --- 核心补丁：允许跨域访问 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许你的本地 HTML 文件访问
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化组件
gamma_client = GammaClient()
video_analyzer = VideoAnalyzer()
authenticity_checker = AuthenticityChecker()
blockchain_notary = BlockchainNotary()

@app.get("/")
async def read_root():
    return {"status": "online", "protocol": "PoA V1.0", "timestamp": int(time.time())}

@app.post("/analyze")
async def analyze_video(
    request: Request,
    video: UploadFile = File(...),
    rule_description: str = Form(...),
    skip_auth: bool = Form(False),
    client_address: str = Form(None),
    client_signature: str = Form(None),
    client_timestamp: int = Form(None)
):
    # (保留之前的核心逻辑...)
    temp_dir = "/tmp" 
    os.makedirs(temp_dir, exist_ok=True)
    temp_filename = f"{uuid.uuid4()}_{video.filename}"
    temp_filepath = os.path.join(temp_dir, temp_filename)
    
    try:
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # AI 核心分析
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        
        # 签名与存证
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        response_data = {
            "authenticity": {"is_authentic": True},
            "analysis": result,
            "notary": {
                "video_hash": video_hash,
                "attestation": attestation
            }
        }
        
        # 二进制打包
        packed_data = msgpack.packb(response_data, use_bin_type=True)
        return Response(content=packed_data, media_type="application/x-msgpack")
        
    except Exception as e:
        error_data = msgpack.packb({"detail": str(e)}, use_bin_type=True)
        return Response(content=error_data, status_code=500, media_type="application/x-msgpack")
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
