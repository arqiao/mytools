"""ffmpeg 公共工具 — 查找、音频提取、HLS 下载、TS→MP4 转封装"""

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件路径"""
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in [r"D:\tools\ffmpeg\bin\ffmpeg.exe",
                      r"C:\tools\ffmpeg\bin\ffmpeg.exe"]:
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"


FFMPEG = find_ffmpeg()


def extract_audio(video_path: Path, audio_path: Path,
                  err_detect: bool = True) -> bool:
    """从视频提取音频为 MP3（-vn -acodec libmp3lame -q:a 2）"""
    if audio_path.exists():
        log.info(f"音频已存在，跳过: {audio_path}")
        return True
    if not video_path.exists():
        log.warning(f"视频不存在，无法提取音频: {video_path}")
        return False
    log.info(f"提取音频: {audio_path.name}")
    cmd = [FFMPEG]
    if err_detect:
        cmd += ["-err_detect", "ignore_err"]
    cmd += [
        "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        str(audio_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except FileNotFoundError:
        log.warning("ffmpeg 未安装或不在 PATH 中")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg 提取音频失败: {result.stderr[-500:]}")
        return False
    size_mb = audio_path.stat().st_size / 1024 / 1024
    log.info(f"音频提取完成: {audio_path} ({size_mb:.1f} MB)")
    return True


def download_hls(m3u8_url: str, output_path: Path,
                 referer: str = None, headers: str = None) -> bool:
    """ffmpeg 下载 HLS 视频流（-c copy -bsf:a aac_adtstoasc）

    referer: 自动构建 Referer+Origin 请求头
    headers: 自定义请求头字符串（优先于 referer）
    """
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"视频已存在，跳过: {output_path}")
        return True
    log.info(f"ffmpeg 下载 HLS: {output_path.name}")
    cmd = [FFMPEG]
    if headers:
        cmd += ["-headers", headers]
    elif referer:
        cmd += ["-headers",
                f"Referer: {referer}\r\nOrigin: {referer.rstrip('/')}\r\n"]
    cmd += [
        "-i", m3u8_url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(output_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=1800)
    except FileNotFoundError:
        log.error("ffmpeg 未安装或不在 PATH 中")
        return False
    except subprocess.TimeoutExpired:
        log.error("ffmpeg 下载超时（30分钟）")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg HLS 下载失败: {result.stderr[-500:]}")
        return False
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"HLS 下载完成: {output_path} ({size_mb:.1f} MB)")
    return True


def remux_ts_to_mp4(ts_path: Path, mp4_path: Path) -> bool:
    """ffmpeg 将 TS 转封装为 MP4"""
    cmd = [
        FFMPEG, "-i", str(ts_path),
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(mp4_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=300)
    except FileNotFoundError:
        log.error("ffmpeg 未安装或不在 PATH 中")
        return False
    if result.returncode != 0:
        log.error(f"remux 失败: {result.stderr[-500:]}")
        return False
    size_mb = mp4_path.stat().st_size / 1024 / 1024
    log.info(f"MP4 转封装完成: {mp4_path} ({size_mb:.1f} MB)")
    return True


def concat_ts(concat_list: Path, output_path: Path,
              timeout: int = 600) -> bool:
    """ffmpeg concat 合并 TS 分片为视频"""
    cmd = [
        FFMPEG, "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy", str(output_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout)
    except FileNotFoundError:
        log.error("ffmpeg 未安装或不在 PATH 中")
        return False
    except subprocess.TimeoutExpired:
        log.error(f"ffmpeg 合并超时（{timeout}秒）")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg 合并失败: {result.stderr[-500:]}")
        return False
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"TS 合并完成: {output_path} ({size_mb:.1f} MB)")
    return True


def mp3_to_wav(audio_path: Path, wav_path: Path = None,
               sample_rate: int = 16000) -> Path:
    """MP3 转 16kHz 单声道 WAV（Whisper 预处理用）

    优先用 PyAV，不可用时回退 ffmpeg。
    """
    if wav_path is None:
        wav_path = audio_path.with_suffix(".16k.wav")
    if wav_path.exists():
        log.info(f"预处理音频已存在，跳过: {wav_path}")
        return wav_path

    try:
        import av
        import wave

        log.info("预处理音频为 16kHz WAV（使用 PyAV）...")
        container = av.open(str(audio_path))
        resampler = av.AudioResampler(format="s16", layout="mono",
                                      rate=sample_rate)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for frame in container.decode(audio=0):
                resampled = resampler.resample(frame)
                for r in resampled:
                    wf.writeframes(r.to_ndarray().tobytes())
        container.close()

    except ImportError:
        cmd = [
            FFMPEG, "-y", "-i", str(audio_path),
            "-ar", str(sample_rate), "-ac", "1",
            str(wav_path),
        ]
        log.info("预处理音频为 16kHz WAV（使用 ffmpeg）...")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 转换失败: {result.stderr.decode(errors='replace')}")

    log.info(f"预处理完成: {wav_path} "
             f"({wav_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return wav_path


def mp3_to_pcm(audio_path: Path, pcm_path: Path = None,
               sample_rate: int = 16000) -> Path:
    """MP3 转 16kHz 单声道 PCM（Sherpa 预处理用）

    优先用 PyAV，不可用时回退 ffmpeg。
    """
    if pcm_path is None:
        pcm_path = audio_path.with_suffix(".pcm")
    if pcm_path.exists():
        log.info(f"PCM 文件已存在，跳过转换: {pcm_path}")
        return pcm_path

    try:
        import av

        log.info(f"转换音频为 PCM（使用 PyAV）: {audio_path}")
        container = av.open(str(audio_path))
        resampler = av.AudioResampler(format="s16", layout="mono",
                                      rate=sample_rate)
        with open(pcm_path, "wb") as f:
            for frame in container.decode(audio=0):
                resampled = resampler.resample(frame)
                for r in resampled:
                    f.write(r.to_ndarray().tobytes())
        container.close()

    except ImportError:
        cmd = [
            FFMPEG, "-y", "-i", str(audio_path),
            "-ar", str(sample_rate), "-ac", "1", "-f", "s16le",
            str(pcm_path),
        ]
        log.info(f"转换音频为 PCM（使用 ffmpeg）: {audio_path}")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 转换失败: {result.stderr.decode(errors='replace')}")

    log.info(f"PCM 转换完成: {pcm_path} "
             f"({pcm_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return pcm_path
