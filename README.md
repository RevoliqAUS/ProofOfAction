# ProofOfAction

**基于 AI + 区块链的 0 信任视频裁判协议，比 MrBeast 的剪辑师更诚实。**

ProofOfAction 是一个结合了先进多模态大模型（Gemini Pro）和区块链技术（Base/Arbitrum L2 存证）的全自动视频验证预言机系统。专为如 Polymarket 这样的 Web3 预测市场设计。

## 🌟 核心理念与防作弊网络
1. **真实性拦截 (AuthenticityChecker)**：严查视频底层 EXIF/XMP、GPS指纹、首帧效验。并在 AI 层专项监控“翻拍光栅效应”、烧录时间戳和反剪辑。
2. **多模态裁判 (VideoAnalyzer)**：接入 Gemini 1.5 Pro 最新模型，通过提示词严审视频目标达成情况（如进球或挑战是否完成）。
3. **不可篡改存证 (BlockchainNotary)**：每一次分析，平台都会提取视频内部的完全 SHA-256 哈希作为数字指纹，并通过预言机热钱包使用 **EIP-712** 协议打包判定结果 (是/否，置信度，时间戳) 签名并上链记录。

## 🚀 快速启动

```bash
# 安装环境与核心依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 配置环境变量 (请完善 .env 文件中的 API Key 及 Wallet Private Key)
# 启动 API
fastapi dev src/main.py --port 8000
```

## 📖 交互文档 (Swagger UI)

如果想让您的开发团队清晰地了解或集成我们的节点逻辑，服务启动后直接访问：
👉 `http://127.0.0.1:8000/docs`

在 OpenAPI 文档中，您可以直接测试以下核心接口：
- `GET /debug/markets`: 获取 Polymarket 上的最新视频对赌市场 (Gamma Client 集成)。
- `POST /analyze`: 上传视频、输入对赌规则，协议会自动进行【防伪检测 -> AI多模态分析 -> 数字指纹哈希 -> EIP-712 签名及上链】的全套流程闭环，并返还附带法务背书的综合发牌结果。
