"""
Vercel Serverless Function 入口文件
放置位置: 项目根目录/api/analyze.py
"""
import os
import json
import tempfile
import time
from http.server import BaseHTTPRequestHandler
import google.generativeai as genai


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


class VideoAnalyzer:
    def __init__(self):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not set")
        
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name="gemini-1.5-pro-latest")
        
        self.system_prompt = """
        作为一个"专业博彩裁判"，你需要严查视频中的剪辑痕迹和是否有慢动作作弊行为。
        请结合提供的"赌局规则描述"，仔细分析上传的视频内容。
        
        你的输出必须严谨且是合法的 JSON 格式，结构如下：
        {
            "is_goal": boolean,
            "timestamp": "HH:MM:SS",
            "confidence": float,
            "cheat_suspected": boolean,
            "reasoning": "string"
        }
        """

    def analyze(self, video_path: str, rule_description: str) -> dict:
        video_file = None
        try:
            print(f"上传视频 {video_path} 到 Gemini...")
            video_file = genai.upload_file(path=video_path)
            
            while video_file.state.name == "PROCESSING":
                print("视频正在处理中...")
                time.sleep(2)
                video_file = genai.get_file(video_file.name)
            
            if video_file.state.name == "FAILED":
                raise ValueError("视频处理在 Gemini 后端失败。")

            prompt = f"{self.system_prompt}\n\n【赌局规则描述】：{rule_description}\n请根据规则和视频进行分析并返回 JSON 格式的结果。"
            
            response = self.model.generate_content(
                [video_file, prompt],
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                )
            )
            
            return json.loads(response.text)
            
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
                except Exception:
                    pass


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        """Handle CORS preflight request"""
        self.send_response(200)
        for key, value in cors_headers().items():
            self.send_header(key, value)
        self.end_headers()

    def do_POST(self):
        try:
            # 解析 multipart form data
            content_type = self.headers.get("Content-Type", "")
            
            if "multipart/form-data" not in content_type:
                self._send_json(400, {"error": "Content-Type must be multipart/form-data"})
                return

            # 读取请求体
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            
            # 解析 boundary
            boundary = content_type.split("boundary=")[1].encode()
            parts = body.split(b"--" + boundary)
            
            video_data = None
            rule_description = ""
            
            for part in parts:
                if b"Content-Disposition" not in part:
                    continue
                    
                header_body = part.split(b"\r\n\r\n", 1)
                if len(header_body) < 2:
                    continue
                    
                header = header_body[0].decode("utf-8", errors="ignore")
                body_content = header_body[1].rstrip(b"\r\n--")
                
                if 'name="rule_description"' in header:
                    rule_description = body_content.decode("utf-8")
                elif 'name="video"' in header:
                    video_data = body_content

            if not video_data:
                self._send_json(400, {"error": "No video file provided"})
                return
            
            if not rule_description:
                self._send_json(400, {"error": "No rule_description provided"})
                return

            # 保存视频到临时文件
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(video_data)
                tmp_path = tmp.name

            # 调用分析
            analyzer = VideoAnalyzer()
            result = analyzer.analyze(tmp_path, rule_description)
            
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

            self._send_json(200, result)

        except Exception as e:
            self._send_json(500, {"error": f"Server error: {str(e)}"})

    def _send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        for key, value in cors_headers().items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
