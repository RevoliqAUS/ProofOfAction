import os
import time
import json
from typing import Dict, Any
import google.generativeai as genai

class VideoAnalyzer:
    def __init__(self):
        # API 密钥配置
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key or api_key == "your_google_api_key_here":
            print("WARNING: GOOGLE_API_KEY is not set properly.")
        
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name="gemini-1.5-pro-latest")
        
        self.system_prompt = """
        作为一个“专业博彩裁判”，你需要严查视频中的剪辑痕迹和是否有慢动作作弊行为。
        请结合提供的“赌局规则描述”，仔细分析上传的视频内容。
        
        你的输出必须严谨且是合法的 JSON 格式，结构如下：
        {
            "is_goal": boolean, // 是否达成赌局描述中的目标
            "timestamp": "HH:MM:SS", // 达成目标的时间点，如果没有则为 null
            "confidence": float, // 你的判定置信度，范围 0.0 到 1.0
            "cheat_suspected": boolean, // 是否有作弊嫌疑（剪辑、拼接、慢动作等）
            "reasoning": "string" // 详细的分析依据和裁决理由
        }
        """

    async def analyze_challenge_video(self, video_path: str, rule_description: str) -> Dict[str, Any]:
        video_file = None
        try:
            print(f"上传视频 {video_path} 到 Gemini...")
            video_file = genai.upload_file(path=video_path)
            
            # 等待视频在 Gemini 后台处理完毕
            while video_file.state.name == "PROCESSING":
                print("视频正在处理中，请稍候...")
                time.sleep(2)
                video_file = genai.get_file(video_file.name)
            
            if video_file.state.name == "FAILED":
                raise ValueError("视频处理在 Gemini 后端失败。")

            prompt = f"{self.system_prompt}\n\n【赌局规则描述】：{rule_description}\n请根据规则和视频进行分析并返回 JSON 格式的结果。"
            
            print("发送分析请求给 Gemini 模型...")
            response = self.model.generate_content(
                [video_file, prompt],
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.2, # 较低的 temperature 避免过多幻觉
                )
            )
            
            result = response.text
            return json.loads(result)
            
        except Exception as e:
            return {
                "is_goal": False,
                "timestamp": None,
                "confidence": 0.0,
                "cheat_suspected": False,
                "reasoning": f"分析过程发生错误: {str(e)}"
            }
        finally:
            if video_file:
                try:
                    genai.delete_file(video_file.name)
                except Exception as cleanup_error:
                    print(f"无法清理远端文件: {cleanup_error}")
