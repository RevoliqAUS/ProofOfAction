# ProofOfAction

A zero-trust video referee protocol powered by AI and blockchain — more honest than a MrBeast editor.

ProofOfAction is a fully automated video verification oracle system that combines advanced multimodal AI models, such as Gemini Pro, with blockchain-based notarization on Base / Arbitrum L2. It is designed for Web3 prediction markets such as Polymarket.

## 🌟 Core Concept and Anti-Cheating Network

1. Authenticity Interception (AuthenticityChecker)  
   Performs strict checks on low-level video metadata, including EXIF/XMP data, GPS fingerprints, and first-frame validation. At the AI layer, it also specifically monitors for signs of “screen re-recording raster effects,” burned-in timestamps, and reverse-editing manipulation.

2. Multimodal Referee (VideoAnalyzer)  
   Integrates the latest Gemini 1.5 Pro model to evaluate whether a video successfully proves the target outcome, such as whether a goal was scored or whether a challenge was completed.

3. Tamper-Proof Notarization (BlockchainNotary)  
   For every analysis, the platform extracts a full SHA-256 hash from the video as a digital fingerprint. The oracle hot wallet then packages the judgment result — yes/no, confidence score, and timestamp — using the EIP-712 protocol, signs it, and records it on-chain.

## 🚀 Quick Start

bash # Set up the environment and install core dependencies python3 -m venv .venv source .venv/bin/activate pip install -e .  # Configure environment variables # Please complete the API keys and wallet private key in your .env file  # Start the API fastapi dev src/main.py --port 8000 

## 📖 Interactive Documentation — Swagger UI

To help your development team clearly understand or integrate with our node logic, start the service and visit:

👉 http://127.0.0.1:8000/docs

In the OpenAPI documentation, you can directly test the following core endpoints:

- GET /debug/markets: Fetch the latest video-based prediction markets from Polymarket via Gamma Client integration.
- POST /analyze: Upload a video and enter the prediction rule. The protocol will automatically complete the full workflow: authenticity detection → AI multimodal analysis → digital fingerprint hashing → EIP-712 signing and on-chain notarization, and then return a legally defensible comprehensive judgment result.
