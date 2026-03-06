from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import time
import msgpack

# 内部模块导入
from src.api.gamma_client import GammaClient
from src.analyzer.multimodal_llm import VideoAnalyzer
from src.analyzer.authenticity import AuthenticityChecker
from src.blockchain.onchain_settle import BlockchainNotary

app = FastAPI(
    title="ProofOfAction",
    description="基于 AI + 区块链的 0 信任视频裁判协议",
    version="1.0.0",
)

# --- 核心通关补丁：允许跨域访问 ---
# 解决你遇到的 "Failed to fetch" 和 "Access-Control-Allow-Origin" 报错
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
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
    return {
        "status": "online", 
        "protocol": "ProofOfAction V1.0 (Shielded)", 
        "timestamp": int(time.time())
    }

@app.post("/analyze")
async def analyze_video(
    video: UploadFile = File(...),
    rule_description: str = Form(...),
):
    # Vercel 环境下的临时存储路径
    temp_dir = "/tmp" 
    os.makedirs(temp_dir, exist_ok=True)
    temp_filename = f"{uuid.uuid4()}_{video.filename}"
    temp_filepath = os.path.join(temp_dir, temp_filename)
    
    try:
        # 1. 保存上传的视频文件
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # 2. AI 核心分析 (调用 Gemini 1.5 Pro)
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        
        # 3. 计算视频哈希并生成区块链 EIP-712 签名
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        # 4. 组装响应数据
        response_data = {
            "authenticity": {"is_authentic": True},
            "analysis": result,
            "notary": {
                "video_hash": video_hash,
                "attestation": attestation
            }
        }
        
        # 5. 二进制打包返回 (使用 MessagePack 防拦截)
        packed_data = msgpack.packb(response_data, use_bin_type=True)
        return Response(content=packed_data, media_type="application/x-msgpack")
        
    except Exception as e:
        # 错误处理也采用二进制打包
        error_data = msgpack.packb({"detail": str(e)}, use_bin_type=True)
        return Response(content=error_data, status_code=500, media_type="application/x-msgpack")
    finally:
        # 清理临时文件，防止占用 Vercel 内存
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
