"""Small, dependency-free launcher for the local Enhanced platform."""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEALTH_URL = "http://127.0.0.1:8765/api/health"
PAGE_URL = "http://127.0.0.1:8765/visualization/prototype/final_platform_map_editor.html"


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )


def _git_output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or result.stderr or "").strip()


def check_and_update_repository() -> None:
    """Safely fast-forward only a clean checkout currently on main."""
    if not (ROOT / ".git").exists():
        print("[Git] 当前目录不是 Git 工作区，使用当前本地代码启动。")
        return
    branch = _git_output(_run_git("branch", "--show-current"))
    head = _git_output(_run_git("rev-parse", "--short", "HEAD"))
    tracked_changes = _git_output(_run_git("status", "--porcelain", "--untracked-files=no"))
    print(f"[Git] branch={branch or 'detached'}  HEAD={head or 'unknown'}")
    if tracked_changes:
        print("[Git] 检测到已跟踪文件的未提交修改；不会覆盖、暂存或藏起它们，使用当前本地版本启动。")
        return
    if branch != "main":
        print("[Git] 当前不在 main；为保护正在开发的分支，不自动更新，使用当前本地版本启动。")
        return
    fetched = _run_git("fetch", "origin")
    if fetched.returncode:
        print("[Git] GitHub 暂时无法连接；不影响使用当前本地版本启动。")
        return
    local_is_ancestor = _run_git("merge-base", "--is-ancestor", "HEAD", "origin/main")
    if local_is_ancestor.returncode:
        print("[Git] 本地 main 并非可安全快进到 origin/main；不会合并或覆盖，使用当前本地版本启动。")
        return
    updated = _run_git("merge", "--ff-only", "origin/main")
    if updated.returncode:
        print("[Git] 自动快进未完成；不会覆盖本地代码，使用当前本地版本启动。")
        return
    print("[Git] main 已安全更新到 origin/main。")


def health_available() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.8) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def start() -> int:
    check_and_update_repository()
    if health_available():
        print("[Platform] 检测到本机已有平台服务，正在打开 Enhanced 页面。")
        webbrowser.open(PAGE_URL)
        return 0
    print("[Platform] 正在启动本地服务…")
    server = subprocess.Popen([sys.executable, "-m", "experiments.web_runtime_server"], cwd=ROOT)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if health_available():
            webbrowser.open(PAGE_URL)
            print("平台运行中，关闭此窗口 / Ctrl+C 可停止服务。")
            try:
                return server.wait()
            except KeyboardInterrupt:
                print("\n[Platform] 正在停止服务…")
                server.terminate()
                return server.wait(timeout=5)
        if server.poll() is not None:
            print("[Platform] 服务启动失败。请检查上方 Python 或依赖错误；启动器不会自动安装环境。")
            return server.returncode or 1
        time.sleep(0.25)
    print("[Platform] 20 秒内未检测到 /api/health；请查看服务输出。")
    server.terminate()
    return 1


if __name__ == "__main__":
    raise SystemExit(start())
