import os
import sys
import uuid
import shutil
import time
import json
import logging
import msgpack  # <--- 新增：引入二进制序列化库

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, Response, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse

# --- Path fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
for p in [current_dir, parent_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from analyzer.multimodal_llm import VideoAnalyzer
from analyzer.metadata_generator import PoAMetadataGenerator
from integrations.kick import KickOAuth, KickBot, kick_webhook_router
from integrations.kick.webhooks import get_webhook_handler, set_webhook_handler, KickWebhookHandler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

# 跨域配置：确保前端 Frame 能顺利访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

video_analyzer = VideoAnalyzer()
metadata_gen = PoAMetadataGenerator()

# In-memory store (use DB in production)
reports_store: dict = {}

# ===================== Kick Integration Setup =====================
kick_oauth: KickOAuth | None = None
kick_bot: KickBot | None = None

try:
    kick_oauth = KickOAuth()
    kick_bot = KickBot(oauth=kick_oauth)

    # Setup webhook handler with bot
    webhook_handler = KickWebhookHandler(bot=kick_bot)
    set_webhook_handler(webhook_handler)

    # Register challenge callback
    async def on_challenge_detected(challenge):
        """Handle detected !challenge commands"""
        logger.info(f"Challenge detected: {challenge.description}")
        # Announce challenge in chat
        await kick_bot.announce_challenge(
            channel_id=challenge.channel_id,
            challenge=challenge,
        )

    kick_bot.on_challenge = on_challenge_detected
    logger.info("✅ Kick integration initialized")

except Exception as e:
    logger.warning(f"⚠️ Kick integration not available: {e}")

@app.get("/")
async def root():
    """Health check - 依然返回 JSON，方便浏览器直接查看状态"""
    return {
        "status": "PoA Online",
        "timestamp": int(time.time()),
        "info": "Ready for Proof of Action analysis",
        "signer_address": metadata_gen.signer_address,
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
        # 1. 保存视频
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # 2. 计算视频 Hash
        video_hash = metadata_gen.compute_video_hash(temp_filepath)

        # 3. AI 分析
        analysis = await video_analyzer.analyze_challenge_video(
            temp_filepath, rule_description
        )

        # 4. 生成带签名的 NFT Metadata
        nft_metadata = metadata_gen.generate(
            analysis=analysis,
            rule_description=rule_description,
            video_hash=video_hash,
        )

        # 5. 存储报告
        verification_id = nft_metadata["poa_evidence"]["verification_id"]
        reports_store[verification_id] = nft_metadata

        # 6. 构造返回数据
        response_data = {
            "analysis": analysis,
            "nft_metadata": nft_metadata,
            "verification_id": verification_id,
            "timestamp": int(time.time()),
        }

        # --- 核心手术：将数据转为二进制 MessagePack ---
        # use_bin_type=True 确保字符串和二进制被正确区分
        binary_payload = msgpack.packb(response_data, use_bin_type=True)

        return Response(
            content=binary_payload,
            media_type="application/x-msgpack",  # <--- 告诉前端这是二进制包
        )

    except Exception as e:
        # 错误处理也建议保持一致，或者简单返回二进制错误包
        error_data = {"error": str(e)}
        return Response(
            content=msgpack.packb(error_data, use_bin_type=True),
            status_code=500,
            media_type="application/x-msgpack",
        )
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

# ... 其余的 GET 路由 (report/signer等) 保持不变即可 ...
# 因为它们主要用于后端查询或验证，不需要强行走二进制流

@app.get("/report/{verification_id}")
async def get_report(verification_id: str):
    """Get NFT Metadata by verification ID"""
    if verification_id in reports_store:
        return reports_store[verification_id]
    return Response(
        content=json.dumps({"error": "Report not found"}),
        status_code=404,
        media_type="application/json",
    )


@app.get("/reports")
async def list_reports():
    """List all reports"""
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


@app.post("/verify")
async def verify_signature(request: Request):
    """
    Verify the ECDSA signature of a PoA verdict.
    
    Send the full nft_metadata JSON and this endpoint will:
    1. Reconstruct the payload hash
    2. Recover the signer from the ECDSA signature
    3. Confirm it matches the authorized PoA signer
    
    Usage:
        POST /verify
        Body: { full nft_metadata JSON }
    
    Response:
        {
            "valid": true/false,
            "recovered_address": "0x...",
            "claimed_signer": "0x...",
            "reason": "..."
        }
    """
    try:
        metadata = await request.json()
        result = metadata_gen.verify_signature(metadata)
        return result
    except Exception as e:
        return Response(
            content=json.dumps({"valid": False, "reason": str(e)}),
            status_code=400,
            media_type="application/json",
        )


@app.get("/signer")
async def get_signer():
    """
    Public endpoint: returns the PoA server's signer address.
    Anyone can use this to verify signatures independently.
    """
    return {
        "signer_address": metadata_gen.signer_address,
        "engine_version": metadata_gen.version,
        "chain": "base",
        "chain_id": 8453,
        "how_to_verify": (
            "1. Take the poa_evidence from any verdict JSON. "
            "2. POST it to /verify endpoint, or verify locally: "
            "3. Hash the payload fields with SHA-256. "
            "4. Recover ECDSA signer from the signature. "
            "5. Confirm it matches this signer_address."
        ),
    }


# ===================== Kick Integration Routes =====================

# Include webhook router
app.include_router(kick_webhook_router)


@app.get("/kick/auth")
async def kick_auth_start():
    """
    Start Kick OAuth authorization flow.

    Redirects user to Kick login page. After authorization,
    user will be redirected to /kick/callback with auth code.
    """
    if not kick_oauth:
        return JSONResponse(
            status_code=503,
            content={"error": "Kick integration not configured"}
        )

    auth_data = kick_oauth.get_authorization_url()
    return RedirectResponse(url=auth_data["url"])


@app.get("/kick/callback")
async def kick_auth_callback(
    code: str = Query(None, description="Authorization code from Kick"),
    state: str = Query(None, description="State parameter for CSRF protection"),
    error: str = Query(None, description="Error code if authorization failed"),
    error_description: str = Query(None, description="Human-readable error description"),
):
    """
    Handle OAuth callback from Kick.

    Successful authorization: receives code and state parameters.
    Failed/denied authorization: receives error and error_description parameters.
    """
    if not kick_oauth:
        return JSONResponse(
            status_code=503,
            content={"error": "Kick integration not configured"}
        )

    # Handle error response (user denied authorization or other error)
    if error:
        logger.warning(f"OAuth error: {error} - {error_description}")
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": error,
                "error_description": error_description or "Authorization was denied or failed",
            }
        )

    # Validate required parameters for success case
    if not code:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": "missing_code",
                "error_description": "Authorization code is required",
            }
        )

    if not state:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": "missing_state",
                "error_description": "State parameter is required for CSRF protection",
            }
        )

    try:
        token = await kick_oauth.exchange_code_for_token(code, state)
        return {
            "status": "success",
            "message": "Successfully authenticated with Kick",
            "token_type": token.token_type,
            "expires_in": token.expires_in,
            "scope": token.scope,
        }
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": "invalid_state",
                "error_description": str(e),
            }
        )
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": "token_exchange_failed",
                "error_description": "Failed to exchange authorization code for token",
            }
        )


@app.get("/callback")
async def callback_root(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
):
    """
    Root callback route for Kick OAuth.
    Forwards to kick_auth_callback to match KICK_REDIRECT_URL.
    """
    return await kick_auth_callback(
        code=code,
        state=state,
        error=error,
        error_description=error_description,
    )


@app.post("/kick/challenge")
async def kick_create_challenge(
    channel_id: str = Form(..., description="Kick channel ID"),
    description: str = Form(..., description="Challenge description"),
    time_limit: str = Form(default="5m", description="Time limit (e.g., 30s, 5m, 1h)"),
):
    """
    Manually trigger a challenge creation (for testing).

    This simulates what happens when a user sends !challenge in chat.
    """
    if not kick_bot:
        return JSONResponse(
            status_code=503,
            content={"error": "Kick integration not configured"}
        )

    try:
        # Create a mock challenge command
        from integrations.kick.bot import ChallengeCommand
        from datetime import datetime

        challenge = ChallengeCommand(
            description=description,
            time_limit=time_limit,
            channel_id=channel_id,
            sender_username="api_test",
            sender_user_id="0",
            message_id=str(uuid.uuid4()),
            raw_message=f"!challenge {description} {time_limit}",
            timestamp=datetime.utcnow(),
        )

        # Announce in chat
        result = await kick_bot.announce_challenge(
            channel_id=channel_id,
            challenge=challenge,
        )

        return {
            "status": "success",
            "challenge": {
                "description": challenge.description,
                "time_limit": challenge.time_limit,
                "channel_id": challenge.channel_id,
                "message_id": challenge.message_id,
            },
            "chat_response": result,
        }

    except Exception as e:
        logger.error(f"Challenge creation error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.post("/kick/judge")
async def kick_judge_challenge(
    channel_id: str = Form(..., description="Kick channel ID"),
    challenge_description: str = Form(..., description="Challenge description"),
    video: UploadFile = File(..., description="Video evidence"),
):
    """
    Judge a challenge and send results to Kick chat.

    This endpoint:
    1. Analyzes the uploaded video using AI
    2. Generates signed proof-of-action metadata
    3. Sends the result to the specified Kick channel
    """
    if not kick_bot:
        return JSONResponse(
            status_code=503,
            content={"error": "Kick integration not configured"}
        )

    temp_filepath = f"/tmp/{uuid.uuid4()}_{video.filename}"
    try:
        # 1. Save video
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # 2. Compute video hash
        video_hash = metadata_gen.compute_video_hash(temp_filepath)

        # 3. AI analysis
        analysis = await video_analyzer.analyze_challenge_video(
            temp_filepath, challenge_description
        )

        # 4. Generate signed NFT metadata
        nft_metadata = metadata_gen.generate(
            analysis=analysis,
            rule_description=challenge_description,
            video_hash=video_hash,
        )

        # 5. Store report
        verification_id = nft_metadata["poa_evidence"]["verification_id"]
        reports_store[verification_id] = nft_metadata

        # 6. Create mock challenge for result formatting
        from integrations.kick.bot import ChallengeCommand
        from datetime import datetime

        mock_challenge = ChallengeCommand(
            description=challenge_description,
            time_limit="N/A",
            channel_id=channel_id,
            sender_username="judge",
            sender_user_id="0",
            message_id="",
            raw_message="",
            timestamp=datetime.utcnow(),
        )

        # 7. Send result to Kick chat
        chat_result = await kick_bot.send_judge_result(
            channel_id=channel_id,
            challenge=mock_challenge,
            result=analysis,
        )

        return {
            "analysis": analysis,
            "verification_id": verification_id,
            "chat_sent": True,
            "chat_response": chat_result,
        }

    except Exception as e:
        logger.error(f"Judge challenge error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
