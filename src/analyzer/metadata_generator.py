"""
PoA NFT Metadata Generator
将 AI 判定结果转换为符合 ERC-721 / OpenSea 标准的 NFT 元数据
"""
import hashlib
import time
import uuid
from typing import Dict, Any, Optional


class PoAMetadataGenerator:
    """生成符合 Web3 审美的 PoA 判定报告 NFT Metadata"""

    def __init__(self, base_url: str = "https://proof-of-action-two.vercel.app"):
        self.base_url = base_url
        self.version = "1.0"

    def generate(
        self,
        analysis: Dict[str, Any],
        rule_description: str,
        video_hash: Optional[str] = None,
        verification_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        将 AI 分析结果打包成 ERC-721 兼容的 NFT Metadata

        Args:
            analysis: VideoAnalyzer 返回的分析结果
            rule_description: 赌局/挑战规则描述
            video_hash: 视频文件的 SHA-256 哈希（可选）
            verification_id: 核销编号（可选，自动生成）

        Returns:
            符合 OpenSea Metadata Standard 的 JSON dict
        """
        # 生成唯一 ID
        if not verification_id:
            verification_id = str(uuid.uuid4())[:8].upper()

        ts = int(time.time())

        # 从分析结果提取字段
        is_goal = analysis.get("is_goal", False)
        confidence = analysis.get("confidence", 0.0)
        timestamp_str = analysis.get("timestamp", "N/A")
        cheat_suspected = analysis.get("cheat_suspected", False)
        reasoning = analysis.get("reasoning", "")
        model_used = analysis.get("_model", "unknown")

        # 判定状态
        if is_goal and not cheat_suspected:
            status = "VERIFIED"
            status_emoji = "✅"
        elif is_goal and cheat_suspected:
            status = "SUSPICIOUS"
            status_emoji = "⚠️"
        else:
            status = "REJECTED"
            status_emoji = "❌"

        # 置信度等级
        if confidence >= 0.9:
            confidence_tier = "Platinum"
        elif confidence >= 0.7:
            confidence_tier = "Gold"
        elif confidence >= 0.5:
            confidence_tier = "Silver"
        else:
            confidence_tier = "Bronze"

        # 从规则描述推断动作类别
        action_category = self._infer_action_category(rule_description)

        # 构建 ERC-721 / OpenSea 标准 Metadata
        metadata = {
            # === 基本字段 (OpenSea 标准) ===
            "name": f"PoA Verification #{verification_id}: {action_category}",
            "description": (
                f"{status_emoji} 这是由 Proof of Action 协议自动核销的视频判定报告。"
                f"该报告通过 AI 视觉分析验证了物理动作的真实性与合规性。\n\n"
                f"📋 规则: {rule_description}\n"
                f"🤖 判定模型: {model_used}\n"
                f"📊 置信度: {confidence * 100:.1f}%\n"
                f"📝 分析: {reasoning[:200]}{'...' if len(reasoning) > 200 else ''}"
            ),
            "image": f"{self.base_url}/api/report-card/{verification_id}",
            "external_url": f"{self.base_url}/report/{verification_id}",

            # === NFT 属性 (OpenSea traits) ===
            "attributes": [
                {
                    "trait_type": "Challenge Status",
                    "value": status
                },
                {
                    "trait_type": "Action Category",
                    "value": action_category
                },
                {
                    "display_type": "boost_percentage",
                    "trait_type": "Inference Confidence",
                    "value": int(confidence * 100)
                },
                {
                    "trait_type": "AI Model",
                    "value": model_used
                },
                {
                    "trait_type": "Confidence Tier",
                    "value": confidence_tier
                },
                {
                    "trait_type": "Cheat Detection",
                    "value": "Suspicious" if cheat_suspected else "Clean"
                },
                {
                    "trait_type": "Verifier",
                    "value": f"PoA_Engine_v{self.version}"
                },
                {
                    "display_type": "date",
                    "trait_type": "Verification Date",
                    "value": ts
                },
            ],

            # === PoA 特有证据字段 ===
            "poa_evidence": {
                "verification_id": verification_id,
                "video_sha256": video_hash or "pending_upload_to_ipfs",
                "rule_description": rule_description,
                "timestamp": ts,
                "engine_version": self.version,
                "full_analysis": {
                    "is_goal": is_goal,
                    "confidence": confidence,
                    "cheat_suspected": cheat_suspected,
                    "action_timestamp": timestamp_str,
                    "reasoning": reasoning,
                    "model": model_used,
                },
            },
        }

        # 如果有交叉验证结果，加入
        cross_check = analysis.get("_cross_check")
        if cross_check:
            metadata["attributes"].append({
                "trait_type": "Cross-Validation",
                "value": "Agreed" if cross_check.get("openai_agrees") else "Disagreed"
            })
            metadata["poa_evidence"]["cross_check"] = cross_check

        return metadata

    def compute_video_hash(self, video_path: str) -> str:
        """计算视频文件的 SHA-256 哈希"""
        sha256 = hashlib.sha256()
        with open(video_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return f"0x{sha256.hexdigest()}"

    def _infer_action_category(self, rule_description: str) -> str:
        """从规则描述中推断动作类别"""
        desc_lower = rule_description.lower()

        categories = {
            "Golf": ["高尔夫", "golf", "swing", "挥杆", "球杆", "hackmotion"],
            "Basketball": ["篮球", "basketball", "dunk", "投篮", "三分"],
            "Soccer": ["足球", "soccer", "football", "射门", "goal"],
            "Fitness": ["健身", "fitness", "pushup", "俯卧撑", "深蹲", "squat"],
            "Gaming": ["游戏", "game", "gaming", "电竞", "esports"],
            "Cooking": ["烹饪", "cooking", "cook", "做菜", "料理"],
            "Dance": ["舞蹈", "dance", "dancing", "跳舞"],
            "Music": ["音乐", "music", "guitar", "piano", "吉他", "钢琴"],
        }

        for category, keywords in categories.items():
            for keyword in keywords:
                if keyword in desc_lower:
                    return category

        return "General Challenge"
