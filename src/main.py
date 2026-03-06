from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import msgpack
import logging

# 导入路径兼容性处理
try:
    from src.analyzer.multimodal_llm import VideoAnalyzer
    from src.blockchain.onchain_settle import BlockchainNotary
except ImportError:
    from analyzer.multimodal_llm import VideoAnalyzer
    from blockchain.onchain_settle import BlockchainNotary

app = FastAPI()

# --- 解决跨域（CORS）拦截 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化核心组件
video_analyzer = VideoAnalyzer()
blockchain_notary = BlockchainNotary()

@app.get("/")
async def root():
    """验证服务是否在线"""
    return {
        "status": "PoA Online",
        "service": "ProofOfAction-v1",
        "endpoint": "/analyze"
    }

@app.post("/analyze")
async def analyze_video(
    video: UploadFile = File(...),
    rule_description: str = Form(...),
):
    # Vercel 唯一的写权限目录是 /tmp，且大小有限制
    temp_filepath = f"/tmp/{uuid.uuid4()}_{video.filename}"
    
    try:
        # 1. 保存上传的临时视频
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # 2. AI 视频深度分析 (调用 Gemini 1.5 Pro)
        # 这一步最耗时，如果报错 504，说明视频需要进一步压缩
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        
        # 3. 计算视频哈希并生成区块链数字签名
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        # 4. 组装最终结果
        response_data = {
            "success": True,
            "analysis": result,
            "notary": {
                "video_hash": video_hash,
                "attestation": attestation
            }
        }
        
        # 5. 序列化为二进制 MessagePack 格式
        packed_data = msgpack.packb(response_data, use_bin_type=True)
        return Response(content=packed_data, media_type="application/x-msgpack")
        
    except Exception as e:
        # 错误反馈也使用 MessagePack，确保前端能解析
        error_msg = {"success": False, "error": str(e)}
        return Response(
            content=msgpack.packb(error_msg), 
            status_code=500, 
            media_type="application/x-msgpack"
        )
    finally:
        # 严格清理临时文件，防止内存溢出
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
