#!/bin/bash
# =============================================
# Park Monitor — 一键部署到 GitHub Pages
# 用法：bash deploy_to_github.sh <你的GitHub用户名> <仓库名>
# 示例：bash deploy_to_github.sh markhuang park-monitor
# =============================================

set -e

GITHUB_USER="${1:-MarkHuang0625}"
REPO_NAME="${2:-park-monitor}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "📦 初始化 Git 仓库..."
git init
git config user.email "huangjinhe666@gmail.com"
git config user.name "Mark Huang"
git branch -M main

echo "📝 添加文件..."
git add -A
git commit -m "feat: initial park monitor dashboard with auto-refresh workflow" 2>/dev/null || \
  echo "（已有提交，跳过）"

echo ""
echo "🌐 请先在 GitHub 上创建仓库："
echo "   👉 https://github.com/new"
echo "   - Repository name: ${REPO_NAME}"
echo "   - 选择 Public"
echo "   - 不要勾选任何初始化选项（README、.gitignore 等）"
echo ""
read -p "创建好了吗？按回车继续..."

echo ""
echo "🚀 推送到 GitHub..."
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
git push -u origin main

echo ""
echo "⚙️  开启 GitHub Pages..."
echo "   请手动操作（需要登录验证，只需 30 秒）："
echo "   1. 打开 https://github.com/${GITHUB_USER}/${REPO_NAME}/settings/pages"
echo "   2. Source → Deploy from a branch"
echo "   3. Branch → main，文件夹 → / (root)"
echo "   4. 点击 Save"
echo ""
echo "✅ 部署完成！等 1~2 分钟后，任何人都可以用这个链接访问："
echo ""
echo "   🔗 https://${GITHUB_USER}.github.io/${REPO_NAME}/"
echo ""
echo "🔄 仪表盘会每小时自动刷新一次数据（GitHub Actions 已配置好）"
echo "   也可以手动触发：https://github.com/${GITHUB_USER}/${REPO_NAME}/actions"
