import os
import sys
import uuid
import shutil
import time
import msgpack
from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware

# --- 自动路径补丁：确保 Vercel 能在任何地方找到你的 src 目录 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
if os.path.dirname(current_dir) not in sys.path:
    sys.path.append(os.path.dirname(current_dir))

# 尝试导入你的核心组件 (增加容错)
try:
    from src.analyzer.multimodal_llm import VideoAnalyzer
    from src.blockchain.onchain_settle import BlockchainNotary
except ImportError:
    try:
        from analyzer.multimodal_llm import VideoAnalyzer
        from blockchain.onchain_settle import BlockchainNotary
    except ImportError:
        # 如果还是找不到，定义简单的占位符防止程序彻底崩溃
        class VideoAnalyzer:
            async def analyze_challenge_video(self, p, r): return f"Analyzer Load Error. Rule: {r}"
        class BlockchainNotary:
            def compute_video_hash(self, p): return "hash_error"
            def generate_eip712_signature(self, h, r): return "sig_error"

app = FastAPI()

# --- 核心：开启跨域许可 (解决 image_061ea2.jpg 中的红字报错) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化组件
video_analyzer = VideoAnalyzer()
blockchain_notary = BlockchainNotary()

@app.get("/")
async def root():
    """生命体征检查接口"""
    return {
        "status": "PoA Online",
        "timestamp": int(time.time()),
        "info": "Ready for Proof of Action analysis"
    }

@app.post("/analyze")
async def analyze_video(
    video: UploadFile = File(...),
    rule_description: str = Form(...),
):
    # Vercel 只允许写入 /tmp
    temp_filepath = f"/tmp/{uuid.uuid4()}_{video.filename}"
    try:
        # 1. 接收视频
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # 2. AI 分析
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        
        # 3. 签名
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        # 4. 组装响应
        response_data = {
            "analysis": result,
            "notary": {
                "video_hash": video_hash, 
                "attestation": attestation
            }
        }
        
        # 5. 返回二进制流 (前端 test_poa.html 正在等这个格式)
        return Response(
            content=msgpack.packb(response_data), 
            media_type="application/x-msgpack"
        )
    except Exception as e:
        # 即使报错也返回二进制包，方便前端展示错误原因
        error_payload = msgpack.packb({"error": str(e)})
        return Response(content=error_payload, status_code=500, media_type="application/x-msgpack")
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
