# ROADMAP.md — 前端优化目标与 1.0 发布门槛

> **本文是「页面优化到什么程度算完」的唯一判定依据。**
> 基线：**v0.7.0**（2026-09-21，界面整体重构已发布并打 tag）。
> 修改本文必须同步更新下面「实测基线」表里的数字，否则判定失效。

## 一、优化目标（三层，每层都可验收）

| 层 | 目标（对用户的价值） | 验收方式 |
|---|---|---|
| A 可用性 | 别人不用敲命令行也敢用：一眼看清当前状态、改配置不会失手、失手能立刻退回 | 手工走读完「首次配置 → 改配置 → 校验失败 → 回滚 → 重载」全流程无卡点 |
| B 一致性 / 可达性 | 键盘能走完全流程；状态变化对读屏可见；对比度达标 | 见 G2：机器可判定的属性断言 + 纯键盘走查 |
| C 可靠性 | 最坏的失败模式是「把用户的 nginx.conf 写坏」，必须由机制而非人工保证 | 见 G1：自动化回归用例覆盖备份/校验/回滚 |

**判定原则**：1.0 ≠ 功能更多，而是「敢推荐给不用命令行的人」。三层都达标的版本才叫正式版。

## 二、实测基线（v0.7.0，2026-09-21）

| 维度 | 实测值 | 状态 |
|---|---|---|
| 视觉层级 / 主题 | 边框界定层级；8 主题（4 配色 × 日夜）；零外部请求 | ✅ 达标 |
| 对比度 | 暗色最浅灰字 4.5:1（重构前 2.9:1）；emerald-light 主色白字 5.0:1（原 4.1:1） | ✅ 达标 |
| 危险操作防护 | 保存前自动备份 + `nginx -t` + `409 saved:true` 警告条 + 一键回滚；路径穿越校验 9 处；全仓 `shell=` 命中 0（无 shell=True）；写接口强制 `X-Requested-With` | ✅ 达标 |
| 阻塞式原生弹窗 | `alert(` / `confirm(` / `prompt(` 命中 0（全部为自绘弹窗） | ✅ 达标 |
| 减少动效 | `prefers-reduced-motion` 已有处理 | ✅ 达标 |
| 键盘可达 | 仅 Ctrl/Cmd+S、编辑器 Tab 缩进、Esc 关弹窗。**无弹窗焦点陷阱、弹窗打开时背景未 `inert`**（Tab 会跑到遮罩后）；页签无方向键切换 | ⚠️ 未达标 |
| 无障碍语义 | `aria-label` 24、`role=` 9、`aria-expanded`（HTML 1 + JS 4）；**`aria-selected` / `aria-live` / `aria-labelledby` / `tabindex` 均为 0**（读屏听不到「配置已保存」，也不知道当前页签） | ⚠️ 未达标 |
| 焦点样式 | `:focus-visible` 1 处、`:focus` 6 处（键盘专用焦点样式偏少） | ⚠️ 待收敛 |
| 编辑器能力 | **无行号**、无 Ctrl+Z。nginx 报错精确给出行号，编辑器却不提供行号定位 | ❌ 未达标 |
| 自动化测试 | **`tests/` 不存在**；CI（`build.yml`）只有 build + release，**无任何测试任务** | ❌ 未达标 |

**综合评估**：界面完成度约 0.8，发布成熟度约 0.5——差距集中在「可验证」这一侧。

## 三、1.0 硬性门槛（逐条全部满足才可发正式版）

- [ ] **G1 自动化测试并接入 CI**
  - 后端：以临时数据目录 + 临时配置目录启动服务，覆盖 `/api/status`、配置文件 CRUD、备份/回滚、`nginx -t` 失败路径。
  - 必含回归用例：**保存一份校验失败的配置后，备份仍存在且可回滚回原内容**（这是本工具最坏的失败模式）。
  - 验收：CI 中有测试任务，且本地 `python -m pytest tests/`（或等价命令）可一键跑通。
- [ ] **G2 DOM 契约测试**
  - 断言 JS 引用的 `#id` / class 在 `index.html` / `style.css` 中真实存在（重构期间靠临时脚本做过，必须固化为 CI 断言）。
  - 验收：故意改坏一个按钮 id，CI 必须失败。
- [ ] **G3 键盘走完全流程**
  - 弹窗焦点陷阱 + 打开时背景 `inert`；页签支持方向键；`aria-selected` 跟随当前页签；保存/校验结果用 `aria-live` 播报。
  - 验收：不碰鼠标完成「切页签 → 选文件 → 编辑 → 保存 → 处理校验失败 → 回滚」。
- [ ] **G4 编辑器补齐行号 + Ctrl+Z/Ctrl+Y**
  - 与「校验错误一键跳转行」联动（跳转后高亮该行）。
- [ ] **G5 契约与版本一致性**
  - `API.md` 与实现零漂移；版本号单一来源 `backend/server.py::server_version` 与 tag、exe 文件名、前端持久化目录 `frontend/v{版本}/` 一致。
- [ ] **G6 无已知 P0/P1**，且 `aoci check` 无待作者化的语义维护积压。

## 四、明确不做（写进文档，避免反复讨论）

| 项 | 理由 |
|---|---|
| 移动端 / 触屏适配 | 定位是本机单用户桌面工具，适配成本高、收益低 |
| 登录鉴权 / 多用户 / RBAC | 用户已决策：仅绑 `127.0.0.1`，无鉴权 |
| 打印样式 | 无使用场景 |
| i18n / 多语言界面 | 单用户中文场景 |
| 前端引入 CDN / 构建工具 / 框架 | 离线可用与可移植是硬约束（见附录） |

## 五、路线与阶段目标

| 版本 | 目标 | 允许的内容 |
|---|---|---|
| `0.7.x` | 保命补丁：G4 行号 + Ctrl+Z、G3 焦点陷阱 / `aria-live` | 只做小改动、纯前端、低风险 |
| `0.8.0` | 测试基建：G1 + G2 落地并接入 CI | **功能冻结版**：本阶段不新增任何功能 |
| `0.9.x` | 冻结候选：G5 + G3 全键盘走查 + G6 | 只修 bug，不加功能 |
| `1.0.0` | 正式版：三层目标全部达标 | 通过门槛后发布 |

**关键约束：先把保障建起来，再谈正式版。** 0.8.0 必须是功能冻结版。

## 六、怎样验证进度（可复现命令）

以下命令用于重新测量基线，避免凭印象判断（在仓库根执行）：

```bash
# B 层：可达性语义属性计数（G3 的目标是把后四项从 0 提上去）
for a in aria-label aria-selected aria-live aria-labelledby tabindex role=; do
  printf "%-16s HTML:%2s JS:%2s\n" "$a" \
    "$(grep -o "$a" frontend/index.html | wc -l | tr -d ' ')" \
    "$(grep -oh "$a" frontend/js/*.js | wc -l | tr -d ' ')"
done

# C 层：弹窗焦点陷阱相关（期望 grep 到 inert 或等价实现）
grep -nE "inert|focus-trap" frontend/index.html frontend/js/*.js

# C 层：危险调用与原生弹窗（期望均为 0）
grep -rn "shell=" backend/ | wc -l
grep -cE "alert\(|confirm\(|prompt\(" frontend/js/*.js

# C 层：契约校验脚本（G2 需固化为 CI 断言）
#   现有一次性脚本的思路：抽出 JS 中 " #id " 引用 → 与 index.html 的 id 集合求差集

# G1：测试是否存在
ls tests/ && (cd . && python -m pytest tests/ -q)

# 对比度：颜色值在 frontend/css/style.css 的各 [data-theme] 块中，
#   用相对亮度公式换算（暗色 --text-4 与亮色 --accent 是历史薄弱点）
```

## 附录 A：仓库固定约束（任何重构都不得违反）

1. **前端禁止引用任何外部资源**：不引 CDN、不引远程字体、不引外部图片（离线/内网可用是产品前提）。图标用内联 SVG sprite。
2. **DOM 契约不可静默破坏**：`app.js` 大量通过 `"#id"` / class 直接操作 DOM，改名即点坏按钮 —— 必须走 G2 的断言保护。
3. **契约纪律**：任何接口/字段变更先改 `API.md`（唯一权威源）→ 再改后端 → 再同步前端。
4. **`409 + saved:true` 是特殊语义**，不是普通错误：前端必须展示警告条 + 回滚入口。
5. **版本号单一来源**：`backend/server.py::server_version`（`build.py`、`nginx-manager.spec`、前端持久化目录均从它派生）。
6. **发布流程**：功能提交 → 改版本号 + 写 `release-notes/<tag>.md` → `release: vX.Y.Z` 提交 → 打 tag 推送 origin 与 github（GitHub Actions 自动构建 exe 并建 Release）。

## 附录 B：AOCI 接入与收尾状态（供后续会话接手）

### B1 接入现状（已完成）

aoci 二进制：`/Users/lutao/.local/bin/aoci`（`aoci version 0.1.0-rc12`，布局 `volumes-v1`，MCP 工具 9 个）。

| 宿主 | 配置文件 | 状态 |
|---|---|---|
| Claude Code | `.mcp.json`（项目级） | ✓ 已配置（`aoci doctor` 确认） |
| Codex | `.codex/config.toml`（项目级） | ✓ 已配置（首次在本仓运行 Codex 时需信任本项目） |
| Hermes | `~/.hermes/config.yaml` → `mcp_servers.aoci` | ✓ 已配置（`hermes mcp test aoci` 连通 813ms / 9 工具） |

宿主配置由 `aoci init --agent claude\|codex` 写入，属机器绑定内容，`.gitignore` 已由 aoci 自动排除（`.mcp.json`、`.codex/config.toml` 及其 backup），**不要提交**。换机器或升级二进制后重跑该命令即可，验证用 `aoci doctor`（「Agent 接入」段应显示 `已配置`）。

自检命令：`aoci --repo . doctor`、`hermes mcp test aoci`、`hermes mcp list`。

### B2 认知维护状态与提交口径（2026-09-21 已闭环）

认知维护积压清零：`aoci.code.txt` 43 条 / 源码 43 个，`aoci check` 返回「✓ 可提交（Entries漂移/待策展/字典/格式/草稿五净）」，`aoci_maintain` 返回 `aligned`。

**提交口径（rc12 实测）**：MCP 的 `aoci_update_entry` 批量 `entries` 入口在本机被客户端 Schema 校验拦下（该字段发布的 `oneOf` 分支互斥为空，任何对象都判 `not valid under any of the given schemas`），正式写入零发生。可用传输是同一管线的 CLI：

```bash
aoci update-entry --repo . --json --path <仓库相对路径> \
  --source-sha256 <aoci_maintain 返回的该候选 sha> --stdin < 条目文件
```

逐候选提交与维护批次等价：`--source-sha256` 就是候选绑定，机器校验（Impact/S 字段配额）与原子写入完全一致。

正式流程仍以 `AGENTS.md` 与机器签发的 Plan/Guide 为准：新会话先 `aoci_rules` → `aoci_overview`（跟随 `next_cursor` 直至 `completed=true`）→ 受管理对象稳定后 `aoci_maintain` → 依据机器签发候选创作完整 F/R/A/S → 提交；`remaining` 非零时重新 `aoci_maintain` 取下一批。**不得手写索引语义绕过。**

**条目硬约束**：F ≤160 字、R ≤360 字/8 项、A ≤400 字/6 项；S 的 rune 上限按 C 档位——C7-4 ≤200、C3-1 ≤50（超限会被 `fras_s_too_long` 拒绝，只改 S 重提）。条目须陈述「文件此刻是什么」，变更历史归 Git，写成演进叙事会被 Validator 提醒。

**重要**：Hermes 的 MCP 工具在会话启动时加载，接入后必须**开新会话**才会出现 `aoci_*` 工具；当前会话内无法直接调用。
