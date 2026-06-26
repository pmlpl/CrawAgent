import os
import re
import subprocess
import tempfile
import shutil

from crawagent.config.settings import get_logger
logger = get_logger(__name__)


def find_ffmpeg() -> str | None:
    """在系统路径中查找 ffmpeg 可执行文件"""
    import shutil
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    candidates = [
        "C:/ffmpeg/bin/ffmpeg.exe",
        "D:/ffmpeg/bin/ffmpeg.exe",
        "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
        os.path.join(os.environ.get("USERPROFILE", ""), "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def remux_to_mp4(video_in: str, audio_in: str | None, out_mp4: str) -> bool:
    """调用 ffmpeg 将 fMP4 片段拼接文件重编码为标准 MP4（强制重编码，确保时间戳正确）"""
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        return False
    if not os.path.exists(video_in):
        return False

    try:
        logger.debug("强制重编码模式（确保时间戳正确）...")
        cmd = [ffmpeg_path, "-y", "-loglevel", "warning",
               "-fflags", "+genpts+igndts+discardcorrupt",
               "-err_detect", "ignore_err",
               "-i", video_in]
        if audio_in and os.path.exists(audio_in):
            cmd += ["-i", audio_in]
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-g", "48", "-keyint_min", "48",
            "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_mp4
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        logger.debug(f"ffmpeg 返回码: {result.returncode}")
        if result.stderr and result.returncode != 0:
            # 只打印前几行错误
            errors = [l for l in result.stderr.split('\n') if 'error' in l.lower() or 'fail' in l.lower()]
            if errors:
                logger.error(f"ffmpeg 错误: {errors[:3]}")

        if (result.returncode == 0 and
                os.path.exists(out_mp4) and
                os.path.getsize(out_mp4) > 1024):
            size_mb = os.path.getsize(out_mp4) / (1024 * 1024)
            logger.info(f"完成，文件大小: {size_mb:.2f} MB")
            return True
        logger.error("失败")
        return False
    except subprocess.TimeoutExpired:
        logger.error("超时")
        return False
    except Exception as e:
        logger.error(f"异常: {e}")
        return False


def encode_from_local_segments(video_segs: list[str], audio_segs: list[str],
                                out_mp4: str, headers: dict, page=None,
                                m3u8_body_video: str = "", m3u8_body_audio: str = "") -> bool:
    """下载所有 fMP4 片段到本地，基于原始 m3u8 生成本地 m3u8，让 ffmpeg 直接读取重编码"""
    import requests
    logger.info("========== 开始本地 m3u8 模式 ==========")
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        logger.error("ffmpeg 未找到")
        return False
    logger.info(f"ffmpeg 路径: {ffmpeg_path}")
    logger.info(f"视频片段数: {len(video_segs)}, 音频片段数: {len(audio_segs)}")
    logger.debug(f"m3u8 body 存在: video={bool(m3u8_body_video)}, audio={bool(m3u8_body_audio)}")

    temp_dir = tempfile.mkdtemp(prefix="youku_segs_")
    logger.debug(f"临时目录: {temp_dir}")
    try:
        url_to_file = {}
        
        logger.info("下载视频片段...")
        for i, seg_url in enumerate(video_segs):
            seg_name = f"video_{i:04d}.m4s"
            seg_path = os.path.join(temp_dir, seg_name)
            try:
                if page is not None:
                    resp = page.session.get(seg_url, headers=headers, timeout=30)
                    if resp.status_code in (200, 206):
                        with open(seg_path, "wb") as f:
                            f.write(resp.content)
                        if os.path.getsize(seg_path) > 100:
                            url_to_file[seg_url] = seg_name
                if seg_url not in url_to_file:
                    resp = requests.get(seg_url, headers=headers, timeout=30)
                    if resp.status_code in (200, 206):
                        with open(seg_path, "wb") as f:
                            f.write(resp.content)
                        if os.path.getsize(seg_path) > 100:
                            url_to_file[seg_url] = seg_name
            except Exception as e:
                logger.error(f"片段 {i} 下载失败: {e}")
            
            if (i + 1) % 50 == 0:
                pct = int((i + 1) / len(video_segs) * 50)
                bar = "=" * pct + ">" + " " * (50 - pct)
                print(f"\r    [{bar}] {(i+1)*100//len(video_segs)}%", end="", flush=True)
        print()
        logger.info(f"成功下载 {len(url_to_file)}/{len(video_segs)} 视频片段")

        if not url_to_file:
            logger.error("没有下载到视频片段")
            return False

        if audio_segs:
            logger.info("下载音频片段...")
            for i, seg_url in enumerate(audio_segs):
                seg_name = f"audio_{i:04d}.m4s"
                seg_path = os.path.join(temp_dir, seg_name)
                try:
                    if page is not None:
                        resp = page.session.get(seg_url, headers=headers, timeout=30)
                        if resp.status_code in (200, 206):
                            with open(seg_path, "wb") as f:
                                f.write(resp.content)
                            if os.path.getsize(seg_path) > 100:
                                url_to_file[seg_url] = seg_name
                    if seg_url not in url_to_file:
                        resp = requests.get(seg_url, headers=headers, timeout=30)
                        if resp.status_code in (200, 206):
                            with open(seg_path, "wb") as f:
                                f.write(resp.content)
                            if os.path.getsize(seg_path) > 100:
                                url_to_file[seg_url] = seg_name
                except Exception as e:
                    logger.error(f"音频片段 {i} 下载失败: {e}")

                if (i + 1) % 50 == 0:
                    pct = int((i + 1) / len(audio_segs) * 50)
                    bar = "=" * pct + ">" + " " * (50 - pct)
                    print(f"\r    [{bar}] {(i+1)*100//len(audio_segs)}%", end="", flush=True)
            print()
            logger.info(f"成功下载 {len(url_to_file) - len(video_segs)}/{len(audio_segs)} 音频片段")

        video_m3u8_path = os.path.join(temp_dir, "video.m3u8")
        logger.info("基于下载片段生成本地 m3u8...")
        _generate_clean_m3u8(video_segs, url_to_file, video_m3u8_path)
        
        with open(video_m3u8_path, "r", encoding="utf-8") as f:
            m3u8_content = f.read()
            line_count = len(m3u8_content.split("\n"))
            logger.info(f"生成视频 m3u8 ({line_count} 行): {video_m3u8_path}")

        audio_m3u8_path = None
        if audio_segs:
            audio_m3u8_path = os.path.join(temp_dir, "audio.m3u8")
            _generate_clean_m3u8(audio_segs, url_to_file, audio_m3u8_path)
            logger.debug(f"生成音频 m3u8: {audio_m3u8_path}")

        logger.info("ffmpeg 读取本地 m3u8 重编码中...")
        
        cmd = [ffmpeg_path, "-y", "-loglevel", "warning",
               "-fflags", "+genpts+igndts",
               "-i", video_m3u8_path]
        if audio_m3u8_path and os.path.exists(audio_m3u8_path):
            cmd += ["-i", audio_m3u8_path]
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+discardcorrupt",
            out_mp4
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                                cwd=temp_dir)
        logger.debug(f"ffmpeg 返回码: {result.returncode}")
        if result.stderr:
            if "error" in result.stderr.lower() or result.returncode != 0:
                logger.error(f"ffmpeg stderr: {result.stderr[:2000]}")
            else:
                logger.warning(f"ffmpeg 警告: {result.stderr[:500]}")

        if result.returncode == 0 and os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 1024:
            size_mb = os.path.getsize(out_mp4) / (1024 * 1024)
            logger.info(f"完成，文件大小: {size_mb:.2f} MB")
            return True
        logger.error("失败")
        return False

    except Exception as e:
        import traceback
        logger.error(f"异常: {e}")
        traceback.print_exc()
        return False
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _replace_m3u8_urls(m3u8_body: str, url_map: dict[str, str], output_path: str):
    """将 m3u8 中的远程 URL 替换为本地文件名"""
    lines = m3u8_body.split("\n")
    new_lines = []
    replaced_count = 0
    total_urls = 0
    
    def _find_mapping(url: str) -> str | None:
        if url in url_map:
            return url_map[url]
        for full_url, local_name in url_map.items():
            if full_url.endswith(url):
                return local_name
            if url.endswith(os.path.basename(full_url)):
                return local_name
        return None
    
    for line in lines:
        if line.startswith("#EXT-X-MAP:URI="):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                old_url = m.group(1)
                total_urls += 1
                mapping = _find_mapping(old_url)
                if mapping:
                    line = line.replace(old_url, mapping)
                    replaced_count += 1
            new_lines.append(line)
        elif line and not line.startswith("#") and line.strip():
            old_url = line.strip()
            total_urls += 1
            mapping = _find_mapping(old_url)
            if mapping:
                new_lines.append(mapping)
                replaced_count += 1
            else:
                new_lines.append(old_url)
        else:
            new_lines.append(line)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))
    
    logger.info(f"替换统计: {replaced_count}/{total_urls} 个 URL")


def _generate_local_m3u8(segments: list[str], url_map: dict[str, str], m3u8_path: str):
    """生成本地 m3u8 文件，引用本地片段文件（备用方案）"""
    with open(m3u8_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write("#EXT-X-VERSION:7\n")
        f.write("#EXT-X-TARGETDURATION:10\n")
        f.write("#EXT-X-MEDIA-SEQUENCE:0\n")
        f.write("#EXT-X-PLAYLIST-TYPE:VOD\n")
        
        for i, seg_url in enumerate(segments):
            if seg_url in url_map:
                seg_name = url_map[seg_url]
                if i == 0:
                    f.write(f"#EXT-X-MAP:URI=\"{seg_name}\"\n")
                else:
                    f.write("#EXTINF:2.000,\n")
                    f.write(f"{seg_name}\n")
        
        f.write("#EXT-X-ENDLIST\n")


def _generate_clean_m3u8(segments: list[str], url_map: dict[str, str], m3u8_path: str, 
                          discontinuity_positions: list[int] | None = None):
    """基于下载的片段列表生成干净的本地 m3u8
    
    fMP4 格式结构：
    - init segment（初始化段）：URL 不包含数字后缀，包含编解码信息
    - data segments（数据段）：URL 包含数字后缀，包含实际数据
    
    每个正片区域都有自己的 init segment。在连续的两个 init segment 之间需要添加 DISCONTINUITY。
    """
    def _is_init_segment(url: str) -> bool:
        basename = url.split("?")[0].split("/")[-1].lower()
        if "_video_" in basename or "_audio_" in basename:
            return False
        if "video_" in basename and any(c.isdigit() for c in basename.split("video_")[-1]):
            return False
        if "audio_" in basename and any(c.isdigit() for c in basename.split("audio_")[-1]):
            return False
        return True
    
    with open(m3u8_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write("#EXT-X-VERSION:7\n")
        f.write("#EXT-X-TARGETDURATION:10\n")
        f.write("#EXT-X-MEDIA-SEQUENCE:0\n")
        
        written = 0
        discontinuity_count = 0
        first_init_written = False
        
        for i, seg_url in enumerate(segments):
            if seg_url not in url_map:
                continue
            seg_name = url_map[seg_url]
            
            is_init = _is_init_segment(seg_url)
            
            # 如果当前是 init segment，且不是第一个被写入的 init segment，添加 DISCONTINUITY
            if is_init:
                if first_init_written:
                    f.write("#EXT-X-DISCONTINUITY\n")
                    discontinuity_count += 1
                else:
                    first_init_written = True
                f.write(f"#EXT-X-MAP:URI=\"{seg_name}\"\n")
            else:
                f.write("#EXTINF:2.000,\n")
                f.write(f"{seg_name}\n")
            
            written += 1
        
        f.write("#EXT-X-ENDLIST\n")
    
    logger.info(f"写入 {written} 条目, DISCONTINUITY: {discontinuity_count}")


def _download_single_segment(url: str, out_path: str, headers: dict, page=None) -> bool:
    """下载单个片段文件"""
    import requests
    try:
        content = None
        if page is not None:
            resp = page.session.get(url, headers=headers, timeout=30)
            if resp.status_code in (200, 206):
                content = resp.content
        if content is None:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code in (200, 206):
                content = resp.content
        if content:
            with open(out_path, "wb") as f:
                f.write(content)
            return True
    except Exception:
        pass
    return False
