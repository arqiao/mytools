"""终端友好的进度输出工具。"""

import sys
import threading
import time


_active_progress = None
_progress_lock = threading.RLock()


def clear_active_progress():
    """在普通日志输出前结束当前单行进度，避免两类输出粘连。"""
    global _active_progress
    with _progress_lock:
        if _active_progress and _active_progress.inline_active:
            sys.stderr.write("\n")
            sys.stderr.flush()
            _active_progress.inline_active = False
        _active_progress = None


class InlineProgress:
    """将高频进度压缩为单行刷新；非 TTY 环境按时间节流输出。"""

    def __init__(self, logger, interval_sec: float = 5.0):
        self.logger = logger
        self.interval_sec = interval_sec
        self.last_emit_at = 0.0
        self.inline_active = False
        self.last_width = 0

    def update(self, message: str, force: bool = False):
        global _active_progress
        now = time.time()
        if not force and now - self.last_emit_at < self.interval_sec:
            return
        with _progress_lock:
            if _active_progress is not None and _active_progress is not self:
                clear_active_progress()
            self.last_emit_at = now
            text = message[:180]
            padding = " " * max(self.last_width - len(text), 0)
            sys.stderr.write("\r" + text + padding)
            sys.stderr.flush()
            self.inline_active = True
            self.last_width = len(text)
            _active_progress = self

    def done(self, message: str = ""):
        global _active_progress
        with _progress_lock:
            if self.inline_active:
                sys.stderr.write("\n")
                sys.stderr.flush()
                self.inline_active = False
            if _active_progress is self:
                _active_progress = None
        if message:
            self.logger.info(message)
