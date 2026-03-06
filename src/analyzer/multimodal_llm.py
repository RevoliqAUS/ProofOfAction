import os
import json
import base64
import time
import subprocess
import tempfile
import asyncio
from typing import Dict, Any

# --- Gemini ---
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# --- OpenAI ---
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class VideoAnalyzer:
    def __init__(self):
        # Gemini 初始化
        self.gemini_model = None
        gemini_key = os.getenv("GOOGLE_API_KEY")
        if GEMINI_AVAILABLE and gemini_key:
            try:
                genai.configure(api_key=gemini_key)
                self.gemini_model = genai.GenerativeModel(model_name="gemini-2.0-flash")
                print("✅ Gemini 初始化成功")
            except Exception as e:
                print(f"⚠️ Gemini 初始化失败: {e}")

        # OpenAI 初始化
        self.openai_client = None
        openai_key = os.getenv("OPENAI_API_KEY")
        if OPENAI_AVAILABLE and openai_key:
            try:
                self.openai_client = OpenAI(api_key=openai_key)
                print("✅ OpenAI 初始化成功")
            except Exception as e:
                print(f"⚠️ OpenAI 初始化失败: {e}")

        if not self.gemini_model and not self.openai_client:
            print("❌ 警告：没有任何可用的 AI 模型！")

        self.system_prompt = """
你是一个"专业博彩裁判"，需要严查视频中的剪辑痕迹和是否有慢动作作弊行为。
请结合提供的"赌局规则描述"，仔细分析上传的视频内容。

你的输出必须严谨且是合法的 JSON 格式，结构如下：
{
    "is_goal": boolean,
    "timestamp": "HH:MM:SS",
    "confidence": float,
    "cheat_suspected": boolean,
    "reasoning": "string"
}

只返回 JSON，不要返回任何其他内容。不要用 markdown 代码块包裹。
"""

    # ===================== Gemini 分析 =====================
    async def _analyze_with_gemini(self, video_path: str, rule_description: str) -> Dict[str, Any]:
        """使用 Gemini 原生视频分析（支持完整视频上传）"""
        if not self.gemini_model:
            raise Exception("Gemini 模型不可用")

        video_file = None
        try:
            print("📤 [Gemini] 上传视频...")
            video_file = genai.upload_file(path=video_path)

            # 等待视频处理完毕
            while video_file.state.name == "PROCESSING":
                print("⏳ [Gemini] 视频处理中...")
                await asyncio.sleep(2)
                video_file = genai.get_file(video_file.name)

            if video_file.state.name == "FAILED":
                raise Exception("视频在 Gemini 后端处理失败")

            prompt = f"{self.system_prompt}\n\n【赌局规则描述】：{rule_description}\n请根据规则和视频进行分析并返回 JSON 格式的结果。"

            print("🧠 [Gemini] 分析中...")
            response = self.gemini_model.generate_content(
                [video_file, prompt],
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                )
            )

            result = json.loads(response.text)
            result["_model"] = "gemini-2.0-flash"
            return result

        finally:
            if video_file:
                try:
                    genai.delete_file(video_file.name)
                except Exception:
                    pass

    # ===================== OpenAI 分析 =====================
    def _extract_frames(self, video_path: str, max_frames: int = 8) -> list:
        """从视频中均匀抽取帧，返回 base64 编码的 JPEG 列表"""
        frames = []
        temp_dir = tempfile.mkdtemp()

        try:
            # 获取视频时长
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path
            ]
            result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            duration = float(result.stdout.strip()) if result.stdout.strip() else 10.0

            interval = max(duration / max_frames, 0.5)

            # ffmpeg 抽帧
            output_pattern = os.path.join(temp_dir, "frame_%04d.jpg")
            ffmpeg_cmd = [
                "ffmpeg", "-i", video_path,
                "-vf", f"fps=1/{interval}",
                "-frames:v", str(max_frames),
                "-q:v", "3",
                "-y",
                output_pattern
            ]
            subprocess.run(ffmpeg_cmd, capture_output=True, timeout=60)

            frame_files = sorted([
                f for f in os.listdir(temp_dir)
                if f.startswith("frame_") and f.endswith(".jpg")
            ])

            for frame_file in frame_files:
                frame_path = os.path.join(temp_dir, frame_file)
                with open(frame_path, "rb") as f:
                    frames.append(base64.standard_b64encode(f.read()).decode("utf-8"))

        except Exception as e:
            print(f"⚠️ 抽帧出错: {e}")
        finally:
            for f in os.listdir(temp_dir):
                try:
                    os.remove(os.path.join(temp_dir, f))
                except Exception:
                    pass
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass

        return frames

    async def _analyze_with_openai(self, video_path: str, rule_description: str) -> Dict[str, Any]:
        """使用 GPT-4o 分析视频帧"""
        if not self.openai_client:
            raise Exception("OpenAI 客户端不可用")

        # 1. 抽帧
        print("🎬 [OpenAI] 抽取视频帧...")
        frames = self._extract_frames(video_path, max_frames=8)

        if not frames:
            raise Exception("无法从视频中抽取帧，请检查视频格式或服务器是否安装了 ffmpeg")

        print(f"📸 [OpenAI] 成功抽取 {len(frames)} 帧，发送给 GPT-4o...")

        # 2. 构建消息
        user_content = []
        user_content.append({
            "type": "text",
            "text": f"【赌局规则描述】：{rule_description}\n\n以下是从视频中按时间顺序抽取的 {len(frames)} 帧画面，请分析并返回 JSON 结果。"
        })

        for i, frame_b64 in enumerate(frames):
            user_content.append({
                "type": "text",
                "text": f"--- 第 {i+1}/{len(frames)} 帧 ---"
            })
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{frame_b64}",
                    "detail": "low"
                }
            })

        # 3. 调用 GPT-4o
        response = self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content}
            ],
            max_tokens=1000,
            temperature=0.2,
        )

        raw_text = response.choices[0].message.content.strip()
        # 清理可能的 markdown 代码块
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw_text)
        result["_model"] = "gpt-4o"
        return result

    # ===================== 并行分析入口 =====================
    async def analyze_challenge_video(self, video_path: str, rule_description: str) -> Dict[str, Any]:
        """
        并行调用 Gemini 和 OpenAI，返回第一个成功的结果。
        如果两个都成功，优先返回 Gemini（因为它分析完整视频，信息更全）。
        如果两个都失败，返回错误信息。
        """
        tasks = {}

        if self.gemini_model:
            tasks["gemini"] = asyncio.create_task(
                self._safe_analyze(self._analyze_with_gemini, video_path, rule_description)
            )

        if self.openai_client:
            tasks["openai"] = asyncio.create_task(
                self._safe_analyze(self._analyze_with_openai, video_path, rule_description)
            )

        if not tasks:
            return {
                "is_goal": False,
                "timestamp": None,
                "confidence": 0.0,
                "cheat_suspected": False,
                "reasoning": "没有可用的 AI 模型。请设置 GOOGLE_API_KEY 或 OPENAI_API_KEY。"
            }

        # 等待所有任务完成
        results = {}
        errors = {}
        
        for name, task in tasks.items():
            result = await task
            if result.get("_success"):
                del result["_success"]
                results[name] = result
            else:
                errors[name] = result.get("_error", "未知错误")

        # 决定返回哪个结果
        if "gemini" in results and "openai" in results:
            # 两个都成功 — 返回 Gemini 结果，附带 OpenAI 作为参考
            final = results["gemini"]
            final["_cross_check"] = {
                "openai_agrees": results["openai"].get("is_goal") == results["gemini"].get("is_goal"),
                "openai_confidence": results["openai"].get("confidence"),
                "openai_reasoning": results["openai"].get("reasoning"),
            }
            return final

        elif results:
            # 只有一个成功，直接返回
            return list(results.values())[0]

        else:
            # 全部失败
            error_details = "; ".join([f"{k}: {v}" for k, v in errors.items()])
            return {
                "is_goal": False,
                "timestamp": None,
                "confidence": 0.0,
                "cheat_suspected": False,
                "reasoning": f"所有模型分析均失败 — {error_details}"
            }

    async def _safe_analyze(self, func, video_path: str, rule_description: str) -> Dict[str, Any]:
        """安全包装，捕获异常并标记成功/失败"""
        try:
            result = await func(video_path, rule_description)
            result["_success"] = True
            return result
        except Exception as e:
            return {"_success": False, "_error": str(e)}
