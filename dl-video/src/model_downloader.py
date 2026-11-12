"""兼容旧路径的 Whisper 模型下载入口 — 实际逻辑位于 src/tools/model_downloader.py

用法:
  python src/model_downloader.py medium
  python src/model_downloader.py --backend mlx large-v3-turbo

详见 src/tools/model_downloader.py 头部注释
"""

from tools.model_downloader import *  # noqa: F401,F403
from tools.model_downloader import main


if __name__ == "__main__":
    main()
