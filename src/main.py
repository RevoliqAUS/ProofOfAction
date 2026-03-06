import os
import sys
import uuid
import shutil
import time
import json

from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware

# --- 自动路径补丁：确保 Vercel 能找到 src 目录 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
for p in [current_dir, parent_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

# --- 导入核心组件 ---
from analyzer.multimodal_llm import VideoAnalyzer

app = FastAPI()

# --- 跨域配置 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化分析器
video_analyzer = VideoAnalyzer()


@app.get("/")
async def root():
    """健康检查接口"""
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
    temp_filepath = f"/tmp/{uuid.uuid4()}_{video.filename}"
    try:
        # 1. 接收视频，保存到 /tmp
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # 2. AI 分析视频
        result = await video_analyzer.analyze_challenge_video(
            temp_filepath, rule_description
        )

        # 3. 组装响应
        response_data = {
            "analysis": result,
            "timestamp": int(time.time()),
        }

        # 4. 返回 JSON 格式（前端更容易处理）
        return Response(
            content=json.dumps(response_data, ensure_ascii=False),
            media_type="application/json",
        )

    except Exception as e:
        error_payload = json.dumps({"error": str(e)}, ensure_ascii=False)
        return Response(
            content=error_payload,
            status_code=500,
            media_type="application/json",
        )
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
