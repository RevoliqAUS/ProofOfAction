import os
import sys
import uuid
import shutil
import time
import json

from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware

# --- 路径补丁 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
for p in [current_dir, parent_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

# --- 导入核心组件 ---
from analyzer.multimodal_llm import VideoAnalyzer
from analyzer.metadata_generator import PoAMetadataGenerator

app = FastAPI()

# --- 跨域配置 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化
video_analyzer = VideoAnalyzer()
metadata_gen = PoAMetadataGenerator()

# 内存存储（demo 用，生产环境应换数据库）
reports_store: dict = {}


@app.get("/")
async def root():
    """健康检查"""
    return {
        "status": "PoA Online",
        "timestamp": int(time.time()),
        "info": "Ready for Proof of Action analysis",
        "models": {
            "gemini": video_analyzer.gemini_model is not None,
            "openai": video_analyzer.openai_client is not None,
        }
    }


@app.post("/analyze")
async def analyze_video(
    video: UploadFile = File(...),
    rule_description: str = Form(...),
):
    temp_filepath = f"/tmp/{uuid.uuid4()}_{video.filename}"
    try:
        # 1. 接收视频
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # 2. 计算视频哈希
        video_hash = metadata_gen.compute_video_hash(temp_filepath)

        # 3. AI 分析
        analysis = await video_analyzer.analyze_challenge_video(
            temp_filepath, rule_description
        )

        # 4. 生成 NFT Metadata
        nft_metadata = metadata_gen.generate(
            analysis=analysis,
            rule_description=rule_description,
            video_hash=video_hash,
        )

        # 5. 存储报告（用 verification_id 作为 key）
        verification_id = nft_metadata["poa_evidence"]["verification_id"]
        reports_store[verification_id] = nft_metadata

        # 6. 返回完整响应
        response_data = {
            "analysis": analysis,
            "nft_metadata": nft_metadata,
            "verification_id": verification_id,
            "timestamp": int(time.time()),
        }

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


@app.get("/report/{verification_id}")
async def get_report(verification_id: str):
    """获取判定报告的 NFT Metadata（供 NFT 市场读取）"""
    if verification_id in reports_store:
        return reports_store[verification_id]
    return Response(
        content=json.dumps({"error": "Report not found"}, ensure_ascii=False),
        status_code=404,
        media_type="application/json",
    )


@app.get("/reports")
async def list_reports():
    """列出所有判定报告"""
    return {
        "total": len(reports_store),
        "reports": [
            {
                "verification_id": vid,
                "name": meta.get("name", ""),
                "status": next(
                    (a["value"] for a in meta.get("attributes", [])
                     if a.get("trait_type") == "Challenge Status"),
                    "unknown"
                ),
            }
            for vid, meta in reports_store.items()
        ]
    }
