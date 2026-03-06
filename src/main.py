from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import msgpack

# 尝试兼容 Vercel 和本地的导入路径
try:
    from src.analyzer.multimodal_llm import VideoAnalyzer
    from src.blockchain.onchain_settle import BlockchainNotary
except ImportError:
    from analyzer.multimodal_llm import VideoAnalyzer
    from blockchain.onchain_settle import BlockchainNotary

app = FastAPI()

# 核心：开启跨域许可
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
    return {"status": "PoA Online", "msg": "Send POST to /analyze"}

@app.post("/analyze")
async def analyze_video(
    video: UploadFile = File(...),
    rule_description: str = Form(...),
):
    # Vercel 只允许写入 /tmp 目录
    temp_filepath = f"/tmp/{uuid.uuid4()}_{video.filename}"
    try:
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # 1. AI 视频分析
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        
        # 2. 生成签名与哈希
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        response_data = {
            "analysis": result,
            "notary": {
                "video_hash": video_hash, 
                "attestation": attestation
            }
        }
        
        # 3. 打包返回二进制数据
        return Response(content=msgpack.packb(response_data), media_type="application/x-msgpack")
        
    except Exception as e:
        return Response(content=msgpack.packb({"error": str(e)}), status_code=500)
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
