from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import msgpack

# 1. 初始化 FastAPI
app = FastAPI()

# 2. 核心补丁：强制开启跨域 (解决 image_061702.png 的报错)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. 根目录测试接口
@app.get("/")
async def root():
    return {"status": "PoA Online", "ready": True}

# 4. 核心分析接口
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
        
        # 这里先返回一个模拟数据，确保链路通畅
        response_data = {
            "analysis": f"AI 正在审理规则: {rule_description}",
            "notary": {"video_hash": "mock_hash_123", "attestation": "mock_sig_456"}
        }
        
        # 按照 test_poa.html 的要求返回二进制包
        return Response(
            content=msgpack.packb(response_data), 
            media_type="application/x-msgpack"
        )
    except Exception as e:
        return Response(content=msgpack.packb({"error": str(e)}), status_code=500)
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
