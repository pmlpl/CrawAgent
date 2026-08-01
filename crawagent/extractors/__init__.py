"""提取器模块

- VideoExtractor: 从 HTML 中提取视频 URL（<video>/<source>/iframe）
- AdRemover: DOM 广告移除
"""
from crawagent.extractors.video_extractor import VideoExtractor, VideoInfo
from crawagent.extractors.ad_remover import AdRemover

__all__ = ["VideoExtractor", "VideoInfo", "AdRemover"]
