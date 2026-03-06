from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import msgpack
import sys

# --- 关键补丁：确保 Vercel 能在任何路径下找到你的 src 模块 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
if os.path.dirname(current_dir) not in sys.path:
    sys.path.append(os.path.dirname(current_dir))

try:
    from src.analyzer.multimodal_llm import VideoAnalyzer
    from src.blockchain.onchain_settle import BlockchainNotary
except ImportError:
    from analyzer.multimodal_llm import VideoAnalyzer
    from blockchain.onchain_settle import BlockchainNotary

app = FastAPI()

# --- 开启跨域许可（解决你控制台中的红字报错） ---
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
    # 这一步用来测试你的 API 是否真的“活”了
    return {"status": "PoA Online", "msg": "Vercel is now handling your requests!"}

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
        # 2. 计算签名
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        response_data = {
            "analysis": result,
            "notary": {"video_hash": video_hash, "attestation": attestation}
        }
        # 打包返回二进制 MessagePack
        return Response(content=msgpack.packb(response_data), media_type="application/x-msgpack")
    except Exception as e:
        # 即使报错也要返回 CORS 许可，方便前端看到错误信息
        return Response(content=msgpack.packb({"error": str(e)}), status_code=500)
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
