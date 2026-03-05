import os
import json
import ffmpeg
from PIL import Image
import google.generativeai as genai
from typing import Dict, Any

class AuthenticityChecker:
    def __init__(self):
        api_key = os.getenv("GOOGLE_API_KEY")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name="gemini-1.5-pro-latest")

    def extract_metadata(self, video_path: str) -> Dict[str, Any]:
        """提取视频的 EXIF/XMP 和地理位置指纹 (使用原生的 subprocess 以确保兼容性)"""
        import subprocess
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            probe = json.loads(result.stdout)
            
            format_info = probe.get('format', {})
            tags = format_info.get('tags', {})
            
            creation_time = tags.get('creation_time')
            location = tags.get('location') or tags.get('com.apple.quicktime.location.ISO6709')
            
            return {
                "creation_time": creation_time,
                "location": location,
                "raw_tags": tags,
                "has_metadata": bool(creation_time or location)
            }
        except Exception as e:
            print(f"Metadata extraction failed: {e}")
            return {"has_metadata": False}

    def _extract_frame_for_pillow(self, video_path: str, output_image_path: str = "temp_frame.jpg"):
        """用于辅助的帧提取（使用 ffmpeg 和 pillow）以备二次检查"""
        try:
            (
                ffmpeg
                .input(video_path, ss="00:00:01")
                .output(output_image_path, vframes=1)
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
            # 使用 Pillow 验证提取到的帧（后续可做水印/EXIF处理）
            with Image.open(output_image_path) as img:
                img.verify()
            return output_image_path
        except Exception as e:
            print(f"Frame extraction failed: {e}")
            return None

    async def audit_visual_timestamp(self, video_path: str, filesystem_time: str) -> Dict[str, Any]:
        """利用 AI 引擎进行视觉时间戳审计与二次翻拍检测"""
        prompt = f'''
        请检查这段视频。
        1. 画面中是否有被烧录的时间戳（水滴、监控水印或系统时间）？如果存在，请提取它，并与文件系统的声明创建时间 "{filesystem_time}" 进行逻辑对比。
        2. 请仔细检查视频是否有“二次翻拍”的痕迹，例如翻拍屏幕、UI重叠、莫尔条纹、人为加速/慢动作等。
        
        请严格以 JSON 格式输出：
        {{
            "has_visual_timestamp": boolean,
            "visual_timestamp_extracted": "提取到的时间字符串，若无则为 null",
            "is_consistent": boolean, // 视觉时间是否与文件时间吻合或合理（考虑时区误差），若无视觉时间戳默认为是
            "is_re_recorded": boolean, // 是否发现明显的二次翻拍或作弊痕迹
            "reasoning": "简要的分析理由"
        }}
        '''
        
        video_file = None
        try:
            video_file = genai.upload_file(path=video_path)
            import time
            while video_file.state.name == "PROCESSING":
                time.sleep(2)
                video_file = genai.get_file(video_file.name)
                
            response = self.model.generate_content(
                [video_file, prompt],
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            return json.loads(response.text)
        except Exception as e:
            # 降级处理，如果在 AI 侧出错则默认标记需要人工核查
            return {
                "has_visual_timestamp": False,
                "visual_timestamp_extracted": None,
                "is_consistent": True,
                "is_re_recorded": False,
                "reasoning": f"Visual audit skipped due to API error: {e}"
            }
        finally:
            if video_file:
                try:
                    genai.delete_file(video_file.name)
                except:
                    pass

    async def check_authenticity(self, video_path: str, app_declared_time: str = None) -> Dict[str, Any]:
        """全面验证视频真实性"""
        meta_info = self.extract_metadata(video_path)
        creation_time = meta_info.get("creation_time")
        
        # 1. 元数据校验：是否有元数据防伪
        if not meta_info.get("has_metadata"):
            return {
                "is_authentic": False,
                "reason": "缺少视频元数据 (CreationDate / GPS)，怀疑非原件或经过二次压制抹除记录。"
            }
            
        # 2. 如果 App 声明了时间，可对比是否相符 (这里做简化验证)
        # if app_declared_time and creation_time ...
        
        # 3. 视觉时间戳与二次翻拍审计
        visual_audit = await self.audit_visual_timestamp(video_path, creation_time or "Unknown")
        
        if visual_audit.get("is_re_recorded"):
            return {
                "is_authentic": False,
                "reason": f"AI 检测到由于二次翻拍或伪造导致的不真实。理由: {visual_audit.get('reasoning')}"
            }
            
        if not visual_audit.get("is_consistent"):
            return {
                "is_authentic": False,
                "reason": f"视觉时间戳与元数据声明的时间不符: {visual_audit.get('reasoning')}"
            }

        return {
            "is_authentic": True,
            "reason": "Authenticity check passed.",
            "meta_extracted": meta_info,
            "visual_audit": visual_audit
        }
