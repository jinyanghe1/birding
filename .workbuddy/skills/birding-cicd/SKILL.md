# CI/CD 自动化测试

每次 commit 后自动运行测试，确保代码质量。

## 触发场景

- `git commit` 后
- `git push` 前
- 手动调用：`./scripts/ci_check.sh`

## 检测内容

1. **版本管理**：commit 树是否清晰
2. **语法检查**：`python -m py_compile`
3. **单测**：`pytest tests/`
4. **服务器实测**：Playwright E2E 测试

## 使用方式

```bash
# 手动运行
./scripts/ci_check.sh

# 或加入 git hook（commit 后自动运行）
echo './scripts/ci_check.sh' >> .git/hooks/post-commit
chmod +x .git/hooks/post-commit
```

## 失败处理

- 语法错误 → 立即修复
- 单测失败 → 检查业务逻辑
- E2E 失败 → 回滚到上一个版本

## 回滚流程

```bash
# 本地回滚
git reset --hard HEAD~1

# 服务器回滚
ssh -i ~/Downloads/birding.pem ubuntu@124.223.171.149
cd ~/birding
git reset --hard HEAD~1
sudo systemctl restart birding
```
