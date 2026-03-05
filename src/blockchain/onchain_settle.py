import os
import hashlib
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data
import time

class BlockchainNotary:
    def __init__(self):
        # 初始化私钥
        self.private_key = os.getenv("WALLET_PRIVATE_KEY")
        
        # 为了测试，如果未设置私钥，生成一个临时的私钥用于签名演示
        if not self.private_key or self.private_key == "your_wallet_private_key_here":
            print("WARNING: WALLET_PRIVATE_KEY not set. Using a temporary key for debugging.")
            self.account = Account.create()
            self.private_key = self.account.key.hex()
        else:
            self.account = Account.from_key(self.private_key)
            
        # 模拟 Base / Arbitrum 上的以太坊节点 (这里作为占位)
        self.w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))

    def compute_video_hash(self, video_path: str) -> str:
        """计算视频文件的 SHA-256 哈希值作为数字指纹"""
        sha256_hash = hashlib.sha256()
        try:
            with open(video_path, "rb") as f:
                # 逐块读取避免内存溢出
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return "0x" + sha256_hash.hexdigest()
        except Exception as e:
            return f"Error computing hash: {e}"

    def generate_eip712_signature(self, video_hash: str, ai_result: dict) -> dict:
        """
        根据 EIP-712 标准打包视频特征与 AI 分析结果，并使用预言机平台私钥签名。
        这代表平台对该视频判决结果的加密背书。
        """
        # 构建域隔离(Domain Separator)
        domain = {
            "name": "ProofOfActionNotary",
            "version": "1",
            "chainId": 8453, # 假定部署在 Base 主网
            "verifyingContract": "0x0000000000000000000000000000000000000000" # 占位合约地址
        }

        # 定义数据结构
        types = {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"}
            ],
            "Attestation": [
                {"name": "videoHash", "type": "bytes32"},
                {"name": "isGoal", "type": "bool"},
                {"name": "confidence", "type": "uint256"},
                {"name": "timestamp", "type": "uint256"}
            ]
        }
        
        # 将浮点置信度转为整数表达便于链上验证 (例如 0.95 -> 95)
        confidence_int = int(ai_result.get("confidence", 0) * 100)
        current_time = int(time.time())

        message = {
            "videoHash": bytes.fromhex(video_hash.replace("0x", "")),
            "isGoal": bool(ai_result.get("is_goal", False)),
            "confidence": confidence_int,
            "timestamp": current_time
        }
        
        typed_data = {
            "types": types,
            "domain": domain,
            "primaryType": "Attestation",
            "message": message
        }

        try:
            signable_message = encode_typed_data(full_message=typed_data)
            signed_message = self.account.sign_message(signable_message)
            
            return {
                "signer": self.account.address,
                "signature": signed_message.signature.hex(),
                "payload": {
                    "video_hash": video_hash,
                    "is_goal": message["isGoal"],
                    "confidence": message["confidence"],
                    "timestamp": message["timestamp"]
                }
            }
        except Exception as e:
            return {"error": str(e)}

    async def simulate_onchain_settlement(self, attestation: dict) -> dict:
        """
        模拟将签名数据通过 Static Call (或交易) 提交给智能合约的流程。
        实际环境中这里会构建 Transaction 并用 w3.eth.send_raw_transaction 发送。
        """
        # 模拟链上校验或存证延迟
        return {
            "status": "simulated_success",
            "network": "Base",
            "block_explorer_url": f"https://basescan.org/address/{attestation.get('signer')}",
            "message": "Attestation successfully verified and recorded (Simulated)."
        }
