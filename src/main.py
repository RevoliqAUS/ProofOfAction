import os
import sys
import uuid
import shutil
import time
import json

from fastapi import FastAPI, UploadFile, File, Form, Response, Request
from fastapi.middleware.cors import CORSMiddleware

# --- Path fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
for p in [current_dir, parent_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from analyzer.multimodal_llm import VideoAnalyzer
from analyzer.metadata_generator import PoAMetadataGenerator

app = FastAPI()

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


@app.get("/")
async def root():
    """Health check"""
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
        # 1. Save video
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)

        # 2. Compute video hash
        video_hash = metadata_gen.compute_video_hash(temp_filepath)

        # 3. AI analysis
        analysis = await video_analyzer.analyze_challenge_video(
            temp_filepath, rule_description
        )

        # 4. Generate signed NFT Metadata
        nft_metadata = metadata_gen.generate(
            analysis=analysis,
            rule_description=rule_description,
            video_hash=video_hash,
        )

        # 5. Store report
        verification_id = nft_metadata["poa_evidence"]["verification_id"]
        reports_store[verification_id] = nft_metadata

        # 6. Response
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
