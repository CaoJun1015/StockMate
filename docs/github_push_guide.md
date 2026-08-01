# GitHub 仓库推送规范说明书

> 本文档面向所有 Agent 和对话窗口，确保推送行为一致、规范。

---

## 一、仓库信息

| 项目 | 内容 |
|------|------|
| **仓库地址** | https://github.com/CaoJun1015/diaohuo-assistant |
| **仓库Owner** | CaoJun1015 |
| **默认分支** | main |
| **开发分支** | dev |
| **项目本地路径** | `d:\素材\diaohuo\diaohuo-assistant` |
| **技术栈** | Python 3.11 + PyQt6 + SQLite |
| **打包工具** | PyInstaller |
| **exe输出目录** | `dist/` |

---

## 二、Git 账号配置

| 配置项 | 值 |
|--------|-----|
| **用户名** | CaoJun |
| **邮箱** | caojun021015@qq.com |
| **认证方式** | SSH Key |

> ⚠️ 重要：提交邮箱必须是 `caojun021015@qq.com`，否则贡献不计入 GitHub 热力图。
> 如果发现邮箱不对，用以下命令修正：
> ```bash
> git config user.email "caojun021015@qq.com"
> ```

---

## 三、推送流程

### 标准推送步骤

```
1. git add <文件列表>     — 只添加需要提交的文件
2. git commit -m "<message>" — 按下面的 commit message 规范
3. git push origin main   — 推送到 main 分支
4. git tag -a vX.YZ -m "描述"
5. git push origin vX.YZ — 推送标签
6. gh release create vX.YZ --title "调货助手 vX.YZ" --notes "<更新说明>" "dist/调货助手 vX.YZ.exe"
```

### Commit Message 规范

格式：`<type>: v<版本号> <简短描述>`

| type | 用途 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: v1.10 自动备份+模糊搜索+表格排序` |
| `fix` | Bug修复 | `fix: v1.08 修复出库SN保存问题` |
| `release` | 版本发布 | `release: v1.07 core code update` |
| `test` | 测试相关 | `test: 为database.py添加28个测试用例` |
| `docs` | 文档更新 | `docs: 更新README版本历史` |

### 标签规范

- 格式：`vX.YZ`（如 `v1.08`、`v1.09`、`v1.10`）
- 标签类型：annotated tag（`git tag -a`），不使用 lightweight tag
- 每个版本发一个 Release，附带 exe 文件

---

## 四、文件提交规则

### ✅ 提交的文件类型

| 类型 | 示例 |
|------|------|
| 源代码 | `src/**/*.py` |
| 配置文件 | `build.spec`, `.gitignore` |
| 文档 | `README.md` |
| 资源文件 | 图标、UI配置等 |

### ❌ 不提交的文件类型

| 类型 | 原因 |
|------|------|
| `docs/` 下的内部文档 | Issues、优化方案、测试报告等（不上传到 GitHub） |
| `tests/` 下的测试文件 | 内部测试，不上传到 GitHub |
| `data/` 下的数据库 | 用户数据不上传 |
| `dist/` 下的 exe | 通过 Release 发布，不提交到代码库 |
| `build/` 下的构建产物 | PyInstaller 临时文件 |
| `.idea/` | IDE 配置 |
| `__pycache__/` | Python 缓存 |

### .gitignore 规则

```
data/
dist/
build/
__pycache__/
*.pyc
.idea/
docs/issues/
tests/test_*.py
```

---

## 五、版本号规则

采用 `vX.YZ` 三位版本号：

| 位数 | 含义 | 示例 |
|------|------|------|
| X（主版本） | 大功能重构或架构变更 | 1 → 2 |
| Y（次版本） | 新功能添加 | 1.08 → 1.09 |
| Z（补丁版本） | Bug修复（通常不用） | 1.09.1 |

版本号需要同步修改的位置：
1. `src/main.py` — `self.setWindowTitle("调货助手 vX.YZ")`
2. `build.spec` — `name='调货助手 vX.YZ'`
3. `README.md` — 版本徽章 + 更新日志表格

---

## 六、备份与回滚

### 每次修改前必须创建备份标签

```bash
git tag -a vX.YZ-backup-before-<描述> -m "备份点：<描述>"
```

示例：
```bash
git tag -a v1.09-backup-before-1.10 -m "备份点：v1.10 体验优化修改前"
```

### 回滚命令

```bash
git reset --hard <备份标签名>
```

---

## 七、常见操作速查

### 创建 Release 并上传 exe

```bash
gh release create vX.YZ \
  --title "调货助手 vX.YZ" \
  --notes "## vX.YZ 更新内容

### 新增
- 功能A
- 功能B

### 修复
- BugA
- BugB" \
  "dist/调货助手 vX.YZ.exe"
```

### 修改最新 Release 的标签（force push 后）

```bash
git tag -d vX.YZ
git push origin :refs/tags/vX.YZ
git tag -a vX.YZ -m "描述"
git push origin vX.YZ
```

### 查看推送状态

```bash
git log --oneline -5          # 最近5次提交
git tag -l                    # 所有标签
gh release list               # 所有 Release
git remote -v                 # 远程仓库地址
```

---

## 八、注意事项

1. **推送前必须告知用户**：列出要提交的文件清单，让用户确认后再执行 `git push`
2. **不要自动 push**：先 commit，展示内容，等用户确认后再 push
3. **exe 文件通过 Release 发布**：不要提交到代码仓库，用 `gh release create` 上传
4. **邮箱一致性**：永远使用 `caojun021015@qq.com`
5. **分支策略**：通常直接在 main 分支开发（个人项目）。如果改动较大，可以在 dev 分支开发，测试通过后合并到 main
