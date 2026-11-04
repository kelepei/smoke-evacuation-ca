#!/bin/bash
# ============================================================
#  smoke-evacuation-ca 仿真平台启动器 (macOS)
#  双击本文件即可在 Terminal 中启动本地服务并打开浏览器页面。
#  等价于 Windows 下的 start_platform.bat。
# ============================================================
set -e

# 1) 无论从哪里双击，都先切换到脚本所在目录
cd "$(dirname "$0")"

echo "======================================================"
echo "  烟雾疏散仿真平台 - macOS 启动器"
echo "======================================================"

# 2) 定位可用的 Python 解释器
PY=""
if command -v python3 >/dev/null 2>&1; then
  PY="python3"
elif command -v python >/dev/null 2>&1; then
  PY="python"
else
  echo "[错误] 未找到 Python。请先安装 Python 3："
  echo "       https://www.python.org/downloads/"
  echo "按回车键退出…"
  read -r _
  exit 1
fi
echo "[信息] 使用 Python：$($PY --version 2>&1)"

# 3) 检查运行所需的依赖包（缺失时给出安装提示，不自动改动系统环境）
MISSING="$($PY - <<'PYEOF'
import importlib.util
mods = ["numpy", "matplotlib", "networkx", "cv2", "PIL", "yaml"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
print(" ".join(missing))
PYEOF
)"

if [ -n "$MISSING" ]; then
  echo "[错误] 缺少依赖包：$MISSING"
  echo "请手动安装后重试，例如："
  echo "  $PY -m pip install numpy matplotlib networkx opencv-python pillow pyyaml"
  echo "按回车键退出…"
  read -r _
  exit 1
fi

# 4) 启动平台（关闭此窗口或按 Ctrl+C 停止服务）
echo "[信息] 正在启动本地仿真平台…"
echo "       启动成功后浏览器会自动打开页面。"
echo "       关闭本窗口 / 按 Ctrl+C 即可停止服务。"
echo ""
"$PY" -m experiments.platform_launcher
RESULT=$?

echo ""
if [ "$RESULT" -ne 0 ]; then
  echo "[错误] 平台未能启动（退出码 $RESULT）。请阅读上方报错信息。"
  echo "按回车键退出…"
  read -r _
fi
exit $RESULT
