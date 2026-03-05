from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import os
import shutil
import uuid
import time
import json
import msgpack
from api.gamma_client import GammaClient
from analyzer.multimodal_llm import VideoAnalyzer
from analyzer.authenticity import AuthenticityChecker
from blockchain.onchain_settle import BlockchainNotary
from api.auth import verify_eip712_signature

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="ProofOfAction",
    description="基于 AI + 区块链的 0 信任视频裁判协议，比 MrBeast 的剪辑师更诚实。",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

gamma_client = GammaClient()
video_analyzer = VideoAnalyzer()
authenticity_checker = AuthenticityChecker()
blockchain_notary = BlockchainNotary()

from fastapi.responses import FileResponse

@app.get("/")
def read_root():
    # Return the frontend security portal
    frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "index.html")
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    return {"message": "Welcome to the ProofOfAction API"}

@app.get("/debug/markets")
@limiter.limit("10/minute")
async def debug_markets(request: Request):
    """Fetch structured Polymarket data related to 'video' for debugging."""
    return await gamma_client.fetch_action_markets()

@app.post("/analyze")
@limiter.limit("2/minute")
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
    Receive a video file and rule description to analyze via Gemini.
    Requires EIP-712 Signature authentication unless skipped.
    """
    if not skip_auth:
        if not (client_address and client_signature and client_timestamp):
            raise HTTPException(status_code=401, detail="Missing EIP-712 authentication parameters.")
        
        # 验证防重放攻击 (例如时间戳不能超过 5 分钟)
        if abs(int(time.time()) - client_timestamp) > 300:
            raise HTTPException(status_code=401, detail="Signature expired or invalid timestamp.")
            
        if not verify_eip712_signature(client_signature, client_address, rule_description, client_timestamp):
            raise HTTPException(status_code=401, detail="Invalid EIP-712 Web3 signature.")


    temp_dir = "data/raw"
    os.makedirs(temp_dir, exist_ok=True)
    temp_filename = f"{uuid.uuid4()}_{video.filename}"
    temp_filepath = os.path.join(temp_dir, temp_filename)
    
    try:
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
            
        # 前置调用 AuthenticityChecker 校验是否为原件
        if not skip_auth:
            auth_result = await authenticity_checker.check_authenticity(temp_filepath)
            if not auth_result.get("is_authentic"):
                raise HTTPException(status_code=400, detail=f"视频未能通过真实性校验: {auth_result.get('reason')}")
        else:
            auth_result = {"is_authentic": True, "reason": "Skipped per user request"}
            
        result = await video_analyzer.analyze_challenge_video(temp_filepath, rule_description)
        
        # 4. 如果分析成功，生成视频哈希和 EIP-712 签名存证
        video_hash = blockchain_notary.compute_video_hash(temp_filepath)
        attestation = blockchain_notary.generate_eip712_signature(video_hash, result)
        
        # 5. 模拟上链存证 (发送至 Base / Arbitrum 智能合约)
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
        
        # 6. 为防止前端篡改，后端再对最终 payload 做一次轻量级哈希签名返回
        import hashlib
        response_string = json.dumps(response_data, sort_keys=True)
        backend_hash = hashlib.sha256((response_string + "backend_salt_POA").encode()).hexdigest()
        response_data["backend_signature"] = backend_hash
        
        # 7. 转为 MessagePack 二进制格式返回
        packed_data = msgpack.packb(response_data, use_bin_type=True)
        
        return Response(content=packed_data, media_type="application/x-msgpack")
        
        
    except Exception as e:
        # 异常情况也返回 MsgPack 格式
        error_data = msgpack.packb({"detail": str(e)}, use_bin_type=True)
        return Response(content=error_data, status_code=500, media_type="application/x-msgpack")
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

