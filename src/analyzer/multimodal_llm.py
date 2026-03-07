import os
import json
import base64
import time
import asyncio
import io
from typing import Dict, Any, List, Tuple

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

# --- imageio + PIL ---
try:
    import imageio.v3 as iio
    from PIL import Image
    IMAGEIO_AVAILABLE = True
except ImportError:
    IMAGEIO_AVAILABLE = False


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

每帧图片都标注了它在视频中的时间戳，请利用这些时间信息来：
1. 判断动作是否连续（帧间跳变可能意味着剪辑）
2. 精确标注目标达成的时间点

你的输出必须严谨且是合法的 JSON 格式，结构如下：
{
    "is_goal": boolean,
    "timestamp": "HH:MM:SS",
    "confidence": float,
    "cheat_suspected": boolean,
    "reasoning": "string"
}

其中 timestamp 应该是目标达成时对应的视频时间点，基于帧的时间戳来判断。
只返回 JSON，不要返回任何其他内容。不要用 markdown 代码块包裹。
"""

    # ===================== Gemini 分析 =====================
    async def _analyze_with_gemini(self, video_path: str, rule_description: str) -> Dict[str, Any]:
        """使用 Gemini 原生视频分析"""
        if not self.gemini_model:
            raise Exception("Gemini 模型不可用")

        video_file = None
        try:
            print("📤 [Gemini] 上传视频...")
            video_file = genai.upload_file(path=video_path)

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
    def _extract_frames(self, video_path: str, max_frames: int = 8) -> List[Tuple[str, float]]:
        """
        从视频中均匀抽取帧。
        返回: [(base64_jpeg, timestamp_seconds), ...]
        """
        if not IMAGEIO_AVAILABLE:
            raise Exception("imageio 或 Pillow 未安装")

        frames_with_time = []

        try:
            # 读取视频元数据获取 fps
            meta = iio.immeta(video_path, plugin="pyav")
            fps = meta.get("fps", 30.0)

            # 读取所有帧
            all_frames = []
            for frame in iio.imiter(video_path, plugin="pyav"):
                all_frames.append(frame)

            total_frames = len(all_frames)
            if total_frames == 0:
                raise Exception("视频中没有可读取的帧")

            # 均匀选取帧
            if total_frames <= max_frames:
                selected_indices = list(range(total_frames))
            else:
                step = total_frames / max_frames
                selected_indices = [int(step * i) for i in range(max_frames)]

            for idx in selected_indices:
                frame_array = all_frames[idx]

                # 计算该帧对应的时间戳
                timestamp_sec = idx / fps

                # numpy array → PIL Image
                img = Image.fromarray(frame_array)

                # 缩小以节省 token
                w, h = img.size
                if w > 512:
                    ratio = 512 / w
                    img = img.resize((512, int(h * ratio)), Image.LANCZOS)

                # PIL → JPEG bytes → base64
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=75)
                b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
                frames_with_time.append((b64, timestamp_sec))

        except Exception as e:
            raise Exception(f"抽帧失败: {e}")

        return frames_with_time

    def _format_timestamp(self, seconds: float) -> str:
        """将秒数格式化为 HH:MM:SS"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    async def _analyze_with_openai(self, video_path: str, rule_description: str) -> Dict[str, Any]:
        """使用 GPT-4o 分析视频帧"""
        if not self.openai_client:
            raise Exception("OpenAI 客户端不可用")

        print("🎬 [OpenAI] 抽取视频帧...")
        frames_with_time = self._extract_frames(video_path, max_frames=8)

        if not frames_with_time:
            raise Exception("未能从视频中抽取到任何帧")

        total_duration = frames_with_time[-1][1]
        print(f"📸 [OpenAI] 成功抽取 {len(frames_with_time)} 帧 (视频时长约 {self._format_timestamp(total_duration)})，发送给 GPT-4o...")

        # 构建消息
        user_content = []
        user_content.append({
            "type": "text",
            "text": (
                f"【赌局规则描述】：{rule_description}\n\n"
                f"以下是从视频中按时间顺序抽取的 {len(frames_with_time)} 帧画面。"
                f"视频总时长约 {self._format_timestamp(total_duration)}。"
                f"每帧都标注了对应的视频时间戳，请据此判断目标达成的准确时间点。"
            )
        })

        for i, (frame_b64, ts) in enumerate(frames_with_time):
            ts_str = self._format_timestamp(ts)
            user_content.append({
                "type": "text",
                "text": f"--- 第 {i+1}/{len(frames_with_time)} 帧 | 时间: {ts_str} ---"
            })
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{frame_b64}",
                    "detail": "low"
                }
            })

        # 调用 GPT-4o
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
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw_text)
        result["_model"] = "gpt-4o"
        return result

    # ===================== 并行分析入口 =====================
    async def analyze_challenge_video(self, video_path: str, rule_description: str) -> Dict[str, Any]:
        """并行调用 Gemini 和 OpenAI，返回最佳结果。"""
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

        results = {}
        errors = {}

        for name, task in tasks.items():
            result = await task
            if result.get("_success"):
                del result["_success"]
                results[name] = result
            else:
                errors[name] = result.get("_error", "未知错误")

        if "gemini" in results and "openai" in results:
            final = results["gemini"]
            final["_cross_check"] = {
                "openai_agrees": results["openai"].get("is_goal") == results["gemini"].get("is_goal"),
                "openai_confidence": results["openai"].get("confidence"),
                "openai_reasoning": results["openai"].get("reasoning"),
            }
            return final
        elif results:
            return list(results.values())[0]
        else:
            error_details = "; ".join([f"{k}: {v}" for k, v in errors.items()])
            return {
                "is_goal": False,
                "timestamp": None,
                "confidence": 0.0,
                "cheat_suspected": False,
                "reasoning": f"所有模型分析均失败 — {error_details}"
            }

    async def _safe_analyze(self, func, video_path: str, rule_description: str) -> Dict[str, Any]:
        """安全包装，捕获异常"""
        try:
            result = await func(video_path, rule_description)
            result["_success"] = True
            return result
        except Exception as e:
            return {"_success": False, "_error": str(e)}
