import json
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from hexbytes import HexBytes

def verify_eip712_signature(signature: str, address: str, rule_description: str, timestamp: int) -> bool:
    """
    验证客户端发来的 EIP-712 签名。
    为简化演示，域与类型信息需与前端/客户端侧保持一致。
    """
    domain = {
        "name": "ProofOfActionClientAuth",
        "version": "1",
        "chainId": 8453,
    }

    types = {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
        ],
        "AuthRequest": [
            {"name": "rule_description", "type": "string"},
            {"name": "timestamp", "type": "uint256"}
        ]
    }

    message = {
        "rule_description": rule_description,
        "timestamp": timestamp
    }

    typed_data = {
        "types": types,
        "domain": domain,
        "primaryType": "AuthRequest",
        "message": message
    }

    try:
        signable_message = encode_typed_data(full_message=typed_data)
        # 恢复出签名地址并忽略大小写对比
        recovered_address = Account.recover_message(signable_message, signature=HexBytes(signature))
        return recovered_address.lower() == address.lower()
    except Exception as e:
        print(f"Signature recovery failed: {e}")
        return False
