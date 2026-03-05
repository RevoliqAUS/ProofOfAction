from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse
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
    description="基于 AI + 区块链的 0 信任视频裁判协议，比 MrBeast 的剪辑师更诚实。",
    version="1.0.0",
)

# 初始化组件
gamma_client = GammaClient()
video_analyzer = VideoAnalyzer()
authenticity_checker = AuthenticityChecker()
blockchain_notary = BlockchainNotary()

@app.get("/")
async def read_root():
    """入口检查，返回欢迎信息或简单的健康检查"""
    return {
        "status": "online",
        "protocol": "ProofOfAction V1.0 (Shielded)",
        "timestamp": int(time.time())
    }

@app.get("/debug/markets")
async def debug_markets(request: Request):
    """获取 Polymarket 相关的市场数据"""
    return await gamma_client.fetch_action_markets()

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
    """
    接收视频文件和规则描述，通过 Gemini 进行分析。
    使用 MessagePack 二进制传输和 EIP-712 签名。
    """
    # 1. Web3 签名校验
    if not skip_auth:
        if not (client_address and client_signature and client_timestamp):
            raise HTTPException(status_code=401, detail="Missing EIP-712 authentication parameters.")
        
        # 验证防重放攻击 (5分钟内有效)
        if abs(int(time.time()) - client_timestamp) > 300:
            raise HTTPException(status_code=401, detail="Signature expired or invalid timestamp.")
            
        if not verify_eip712_signature(client_signature, client_address, rule_description, client_timestamp):
            raise HTTPException(status_code=401, detail="Invalid EIP-712 Web3 signature.")

    # 2. 处理临时文件 (使用 Vercel 允许的 /tmp 目录)
    temp_dir = "/tmp" 
    os.makedirs(temp_dir, exist_ok=True)
    temp_filename = f"{uuid.uuid4()}_{video.filename}"
    temp_filepath = os.path.join(temp_dir, temp_filename)
    
    try:
        # 保存上传的文件
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # 3. 真实性校验 (视频原件检查)
        if not skip_auth:
            auth_result = await authenticity_checker.check_authenticity(temp_filepath)
            if not auth_result.get("is_authentic"):
                raise HTTPException(status_code=400, detail=f"视频未能通过真实性校验: {auth_result.get('reason')}")
        else:
            auth_result = {"is_authentic": True, "reason": "Skipped per user request"}
            
        # 4. AI 核心分析
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        
        # 5. 生成视频哈希和 EIP-712 存证
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        # 6. 模拟上链存证
        onchain_receipt = None
        if "error" not in attestation:
            onchain_receipt = await blockchain_notary.simulate_onchain_settlement(attestation)
        
        response_data = {
            "authenticity": auth_result,
            "analysis": result,
            "notary": {
                "video_hash": video_hash,
                "attestation": attestation,
                "onchain_receipt": onchain_receipt
            }
        }
        
        # 7. 后端二次签名防篡改 (使用环境变量中的 SALT)
        salt = os.getenv("BACKEND_SALT_POA", "default_salt_if_missing")
        response_string = json.dumps(response_data, sort_keys=True)
        backend_hash = hashlib.sha256((response_string + salt).encode()).hexdigest()
        response_data["backend_signature"] = backend_hash
        
        # 8. 转为 MessagePack 二进制格式返回 (防 Neo 插件直接读取 JSON)
        packed_data = msgpack.packb(response_data, use_bin_type=True)
        return Response(content=packed_data, media_type="application/x-msgpack")
        
    except Exception as e:
        # 异常情况也返回二进制格式，保持通信协议一致
        err_msg = {"detail": str(e), "status": "error"}
        error_data = msgpack.packb(err_msg, use_bin_type=True)
        return Response(content=error_data, status_code=500, media_type="application/x-msgpack")
    finally:
        # 清理临时视频，释放 Serverless 内存
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
