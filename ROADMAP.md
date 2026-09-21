# ROADMAP.md — 前端优化目标与 1.0 发布门槛

> **本文是「页面优化到什么程度算完」的唯一判定依据。**
> 基线：**v0.7.0**（2026-09-21，界面整体重构已发布并打 tag）。G1–G6 落地后的复测值见第二节第三列。
> 修改本文必须同步更新下面「实测基线」表里的数字，否则判定失效。

## 一、优化目标（三层，每层都可验收）

| 层 | 目标（对用户的价值） | 验收方式 |
|---|---|---|
| A 可用性 | 别人不用敲命令行也敢用：一眼看清当前状态、改配置不会失手、失手能立刻退回 | 手工走读完「首次配置 → 改配置 → 校验失败 → 回滚 → 重载」全流程无卡点 |
| B 一致性 / 可达性 | 键盘能走完全流程；状态变化对读屏可见；对比度达标 | 见 G2：机器可判定的属性断言 + 纯键盘走查 |
| C 可靠性 | 最坏的失败模式是「把用户的 nginx.conf 写坏」，必须由机制而非人工保证 | 见 G1：自动化回归用例覆盖备份/校验/回滚 |

**判定原则**：1.0 ≠ 功能更多，而是「敢推荐给不用命令行的人」。三层都达标的版本才叫正式版。

## 二、实测基线（v0.7.0，2026-09-21）

| 维度 | v0.7.0 基线实测值 | G1–G6 落地后复测（2026-09-21） | 状态 |
|---|---|---|---|
| 视觉层级 / 主题 | 边框界定层级；8 主题（4 配色 × 日夜）；零外部请求 | 未改动 | ✅ 达标 |
| 对比度 | 暗色最浅灰字 4.5:1（重构前 2.9:1）；emerald-light 主色白字 5.0:1（原 4.1:1） | **真实渲染取色复核（2026-09-21）**：暗色 `--text-4` 对画布 4.77–4.82、emerald-light 主色白字 5.01，原声明值成立；并固化为 `scripts/contrast_audit.py`（脚本解析值与真实浏览器渲染逐项一致，声明档 96/96 通过）。WCAG AA 严格档另有 21 项差距（亮色 `--text-4` 3.49–3.74、亮色 `--ok` 作正文 4.10、emerald-dark `--text-4` 对面板 4.48、全部主题 `--border-strong` 1.38–1.62）；改色属设计变更，未擅动 | ✅ 声明档达标（严格档有差距） |
| 危险操作防护 | 保存前自动备份 + `nginx -t` + `409 saved:true` 警告条 + 一键回滚；路径穿越校验 9 处；全仓 `shell=` 命中 0（无 shell=True）；写接口强制 `X-Requested-With` | 同上；另加 `--port` 入口范围校验（越界或非整数以退出码 2 拒绝，不在 bind 阶段才抛错）、单行 `location` 拒改写、未建模配置拒改写；备份改为**用户显式选择**（保存并备份 / 仅保存），未启用备份时「回滚」入口自动退化为回滚到最新备份（不留死按钮） | ✅ 达标 |
| 阻塞式原生弹窗 | `alert(` / `confirm(` / `prompt(` 命中 0（全部为自绘弹窗） | 复测仍为 0 | ✅ 达标 |
| 减少动效 | `prefers-reduced-motion` 已有处理 | 未改动 | ✅ 达标 |
| 键盘可达 | 仅 Ctrl/Cmd+S、编辑器 Tab 缩进、Esc 关弹窗。**无弹窗焦点陷阱、弹窗打开时背景未 `inert`**（Tab 会跑到遮罩后）；页签无方向键切换 | 弹窗焦点陷阱 + 背景 `inert` + 关闭归还焦点；页签 roving tabindex + 方向键；编辑器以 Esc 交出焦点（Tab 被缩进占用）；真实浏览器纯键盘走查「切页签 → 选文件 → 编辑 → 保存 → 处理 409 → 回滚」一遍通过 | ✅ 达标 |
| 无障碍语义 | `aria-label` 24、`role=` 9、`aria-expanded`（HTML 1 + JS 4）；**`aria-selected` / `aria-live` / `aria-labelledby` / `tabindex` 均为 0** | `aria-label` 35（+JS 2）、`role=` 25、`aria-labelledby` 8、`aria-selected` 6（+JS 3）、`aria-live` 2（+JS 1）、`tabindex` 6（+JS 5）、`aria-modal` 9 | ✅ 达标 |
| 焦点样式 | `:focus-visible` 1 处、`:focus` 6 处（键盘专用焦点样式偏少） | `:focus-visible` 10 处、`:focus` 16 处（自带底色控件给醒目焦点环） | ✅ 达标 |
| 编辑器能力 | **无行号**、无 Ctrl+Z。nginx 报错精确给出行号，编辑器却不提供行号定位 | 行号栏随行数与滚动同步；Ctrl/Cmd+Z 撤销 + Ctrl+Y 重做（自建快照历史、连续输入合并）；校验失败跳转后居中滚动并高亮该行 | ✅ 达标 |
| 自动化测试 | **`tests/` 不存在**；CI（`build.yml`）只有 build + release，**无任何测试任务** | `tests/` 95 用例（真实子进程 HTTP 全链路、备份/回滚含最坏失败模式门禁、`nginx -t` 行号解析、DOM 契约、启动参数边界、工作区 nginx 双布局探测、备选单选落盘、单行 location 拒改写、upstream 手写指令守卫、引号内花括号与 CRLF 保持、API 契约与版本一致性）；`scripts/dom_contract.py` 静态契约检查；pytest 与标准库 `unittest` 双入口都能跑通（95 用例 / 61 subtests）；GitHub/Gitea 双宿主测试门禁（`tags-ignore: v*.*.*`，不干扰发布） | ✅ 达标 |
| 契约与版本一致性 | 未度量 | `API.md` 与实现端点集合 34/34 双向一致；**27 个端点的成功响应顶层字段双向核对**（12 个 GET 全覆盖 + 15 个写端点，含 restore/备份删除/地址池/upstream/代理）；剩 7 个（nginx 启停重载重启、重启服务、选择路径、改设置）需真实 nginx 运行或图形界面，未纳入自动化、仅人工核对字段；版本单一来源 `server_version` 且不落后最新 tag | ✅ 达标 |

**综合评估（G1–G6 落地后）**：三层目标的可验收项均已达标——界面完成度约 0.95，发布成熟度约 0.9。差距从「可验证」转到「真实环境验证」。

**本表未覆盖的剩余风险**（发布正式版前应另行确认）：① ~~端到端只跑过替身脚本~~ **已补：真实 nginx 二进制走查落地为 `scripts/e2e_real_nginx.py`（33 项全通过，含真实启动/重载/停止、`nginx -t` 真实报错行号、代理生效与回滚、stub_status 抓取、日志读取、安全边界；需工作区 `nginx-1.30.4/`，故不进 CI，发布前手动跑）；② 未在 Windows 实机验证（CI 测试任务跑 ubuntu，替身相关用例在 Windows 自动跳过）；③ ~~对比度为公式换算值，未做真机取色复核~~ **已补（2026-09-21）：真实浏览器渲染取色复核完成（8 主题 × 11 对 = 88 项），并固化为 `scripts/contrast_audit.py`（脚本解析值与真实渲染逐项一致；声明档 96/96 通过）。原声明的两项值成立（暗色 `--text-4` 对画布 4.77–4.82、emerald-light 主色白字 5.01）；WCAG AA 严格档仍有 21 项差距，逐条列在第二节对比度行与 `--strict` 输出里，改色属设计变更、待决策（其中 `--border-strong` 提到 3:1 会明显改变「边框定义层级」的整体观感）。**

## 三、1.0 硬性门槛（逐条全部满足才可发正式版）

- [x] **G1 自动化测试并接入 CI**
  - 后端：以临时数据目录 + 临时配置目录启动服务，覆盖 `/api/status`、配置文件 CRUD、备份/回滚、`nginx -t` 失败路径。
  - 必含回归用例：**保存一份校验失败的配置后，备份仍存在且可回滚回原内容**（这是本工具最坏的失败模式）。
  - 验收：CI 中有测试任务，且本地 `python -m pytest tests/`（或等价命令）可一键跑通。
  - **已达成（2026-09-21）**：`tests/` 43 用例，走真实服务子进程 + 真实 HTTP（只把 nginx 换成替身脚本）；`.github/workflows/tests.yml` 与 `.gitea/workflows/tests.yml` 在任意分支 push/PR 触发并 `tags-ignore: v*.*.*`（不重复触发发布）；双入口 `python3.12 -m unittest discover -s tests -t .` 与 `uv run --with pytest python -m pytest tests/ -q` 均通过；最坏失败模式门禁在 `tests/test_backup_restore.py`。
- [x] **G2 DOM 契约测试**
  - 断言 JS 引用的 `#id` / class 在 `index.html` / `style.css` 中真实存在（重构期间靠临时脚本做过，必须固化为 CI 断言）。
  - 验收：故意改坏一个按钮 id，CI 必须失败。
  - **已达成（2026-09-21）**：`scripts/dom_contract.py`（纯标准库，无参数即按仓库布局运行，免个人脚本目录依赖）+ CI 步骤 + `tests/test_dom_contract.py` 门禁；验收试验：改坏按钮 id 即失败、改回即通过。
- [x] **G3 键盘走完全流程**
  - 弹窗焦点陷阱 + 打开时背景 `inert`；页签支持方向键；`aria-selected` 跟随当前页签；保存/校验结果用 `aria-live` 播报。
  - 验收：不碰鼠标完成「切页签 → 选文件 → 编辑 → 保存 → 处理校验失败 → 回滚」。
  - **已达成（2026-09-21）**：焦点陷阱 + 背景 `inert`（豁免读屏播报区/toast）+ 关闭归还焦点（`frontend/js/ui.js`）；页签与停靠页签 roving tabindex + 方向键 + `aria-selected` 跟随；`toast` 同步 `#srStatus`（`aria-live=polite`）；真实浏览器纯键盘走查通过。**编辑器 Tab 用于缩进，键盘出口是 Esc**（设计决定，非缺陷）。
- [x] **G4 编辑器补齐行号 + Ctrl+Z/Ctrl+Y**
  - 与「校验错误一键跳转行」联动（跳转后高亮该行）。
  - **已达成（2026-09-21）**：行号栏随行数与滚动同步；Ctrl/Cmd+Z 撤销 + Ctrl+Y 重做（自建快照历史，连续输入合并）；跳转后居中滚动并高亮该行；回滚后备忘条与高亮一并清理。**联动的前提是后端真能给出行号**：`nginx -t` 失败输出是多行（`[alert]` 前缀行 + 出错行 + `test failed` 汇总），原正则只匹配行尾，逐行解析并取最后一次命中后才真正可用（`backend/nginxctl.py::test_config`）。
- [x] **G5 契约与版本一致性**
  - `API.md` 与实现零漂移；版本号单一来源 `backend/server.py::server_version` 与 tag、exe 文件名、前端持久化目录 `frontend/v{版本}/` 一致。
  - **已达成（2026-09-21）**：`tests/test_api_contract.py` 双向比对端点集合（34/34）与成功响应顶层字段（差异时列出「仅文档 / 仅实现」）；版本单一来源校验＝`build.py` 提取值一致 + 白名单外不得硬编码 + 不得落后最新 tag。本轮修掉真实漂移：`GET /api/settings` 的 `dataDir / dataDirLocked / settingsFile`、`PUT /api/settings` 的 `dataDir`、`--data-dir` 与 `--port` 参数说明。**未单独断言**：exe 文件名与 `frontend/v{版本}/` 持久化目录的派生（记为待补）。
- [x] **G6 无已知 P0/P1**，且 `aoci check` 无待作者化的语义维护积压。
  - **已达成（2026-09-21）**：`SECURITY_AUDIT.md` 无未处置 P0/P1（P1 CSRF、P3 `--port` 范围校验已落地；P1「敏感操作二次确认」记为**已决策不做**，理由写在该文件）；`aoci check` 返回「✓ 可提交（五净）」、`aoci_maintain` 返回 `aligned`（当时 `aoci.code.txt` 46 条 / 源码 46 个，发布前复查一批维护后为 48 条 / 源码 48 个）。

## 四、明确不做（写进文档，避免反复讨论）

| 项 | 理由 |
|---|---|
| 移动端 / 触屏适配 | 定位是本机单用户桌面工具，适配成本高、收益低 |
| 登录鉴权 / 多用户 / RBAC | 用户已决策：仅绑 `127.0.0.1`，无鉴权 |
| 打印样式 | 无使用场景 |
| i18n / 多语言界面 | 单用户中文场景 |
| 前端引入 CDN / 构建工具 / 框架 | 离线可用与可移植是硬约束（见附录） |

## 五、路线与阶段目标

| 版本 | 目标 | 允许的内容 | 状态（2026-09-21） |
|---|---|---|---|
| `0.7.x` | 保命补丁：G4 行号 + Ctrl+Z、G3 焦点陷阱 / `aria-live` | 只做小改动、纯前端、低风险 | ✅ 已落地（源码） |
| `0.8.0` | 测试基建：G1 + G2 落地并接入 CI | **功能冻结版**：本阶段不新增任何功能 | ✅ G1/G2 已落地并接入 CI；功能冻结未被破坏 |
| `0.9.x` | 冻结候选：G5 + G3 全键盘走查 + G6 | 只修 bug，不加功能 | ✅ G5/G6 已落地、G3 全键盘走查通过 |
| `1.0.0` | 正式版：三层目标全部达标 | 通过门槛后发布 | ⏳ 门槛已全部转绿（见第三节），**尚未发布** |

**关键约束：先把保障建起来，再谈正式版。** 0.8.0 必须是功能冻结版。

**本轮实际状态（2026-09-21）**：G1–G6 已全部落地并逐项提交（见 `git log`：编辑器、无障碍、后端测试基建、DOM 契约、契约与版本一致性、安全遗留项收口），但**未提升 `server_version`、未打 tag、未写 release-notes**——按用户明确要求「干完一步提交一次，不要发布版本」。发布动作（改版本号 → 写 `release-notes/<tag>.md` → `release: vX.Y.Z` 提交 → 打 tag 推送）留给下一次明确的发布指令；`build.yml` 只在 `v*.*.*` tag 推送时触发，不打 tag 就不会发版。

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

# C 层：契约校验（G2 已固化：仓库内脚本 + CI 步骤，不依赖个人脚本目录）
python3 scripts/dom_contract.py

# G1：测试（双入口；标准库入口不依赖 pytest）
python3 -m unittest discover -s tests -t . -v
uv run --python 3.13 --with pytest python -m pytest tests/ -q
#   门禁位置：.github/workflows/tests.yml 与 .gitea/workflows/tests.yml（tags-ignore: v*.*.*）

# 真机走查（发布前手动跑；需工作区 nginx-1.30.4/ 真实二进制，不进 CI，退出码即结论）
NM_E2E_DIR=/tmp/nm-e2e python3 scripts/e2e_real_nginx.py

# 对比度：8 主题关键配色对（声明档＝门禁；--strict 按 WCAG AA 严格核对并列出差距）
python3 scripts/contrast_audit.py
python3 scripts/contrast_audit.py --strict
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

认知维护积压清零：`aoci.code.txt` 48 条 / 源码 48 个（两批维护：G1/G2 测试与契约基建落地后＝新增 3 条 + 更新 9 条；发布前复查后＝新增 2 条（`LICENSE`、`scripts/e2e_real_nginx.py`）+ 更新 13 条），`aoci check` 返回「✓ 可提交（Entries漂移/待策展/字典/格式/草稿五净）」，`aoci_maintain` 返回 `aligned`（`governance_aligned: true`、`code_drift` 全空）。测试类文件（`tests/*.py`）落在**观察集**而非索引集；新增受管文件后若要继续维护，先按机器下发的 next_command 跑 `aoci scope acknowledge --repo . --reviewed-by <agent>` 完成作用域复核前移。

**提交口径（rc12 实测）**：MCP 的 `aoci_update_entry` 批量 `entries` 入口在本机被客户端 Schema 校验拦下（该字段发布的 `oneOf` 分支互斥为空，任何对象都判 `not valid under any of the given schemas`），正式写入零发生。可用传输是同一管线的 CLI：

```bash
aoci update-entry --repo . --json --path <仓库相对路径> \
  --source-sha256 <aoci_maintain 返回的该候选 sha> --stdin < 条目文件
```

逐候选提交与维护批次等价：`--source-sha256` 就是候选绑定，机器校验（Impact/S 字段配额）与原子写入完全一致。

正式流程仍以 `AGENTS.md` 与机器签发的 Plan/Guide 为准：新会话先 `aoci_rules` → `aoci_overview`（跟随 `next_cursor` 直至 `completed=true`）→ 受管理对象稳定后 `aoci_maintain` → 依据机器签发候选创作完整 F/R/A/S → 提交；`remaining` 非零时重新 `aoci_maintain` 取下一批。**不得手写索引语义绕过。**

**条目硬约束**：F ≤160 字、R ≤360 字/8 项、A ≤400 字/6 项；S 的 rune 上限按 C 档位——C7-4 ≤200、C3-1 ≤50（超限会被 `fras_s_too_long` 拒绝，只改 S 重提）。条目须陈述「文件此刻是什么」，变更历史归 Git，写成演进叙事会被 Validator 提醒。

**重要**：Hermes 的 MCP 工具在会话启动时加载，接入后必须**开新会话**才会出现 `aoci_*` 工具；当前会话内无法直接调用。

### B3 上下文压缩后的重载与四个实测坑（2026-09-21 记录）

- **压缩后必须重载认知**：宿主注入压缩摘要后，此前模型认知一律不作数。用 `aoci_overview` 携带 `refresh_reasons=["context_compaction"]` 与**新的** `refresh_event_id` 请求完整 Whole-Index（不要设 `check_only`），原样跟随 `next_cursor` 到 `completed=true`，再提交交付确认与 Attestation（本次 43/43 条、Challenge 10/10 通过后 `cognition_verified`）。摘要里不得携带正式 Header / Entry / Challenge 正文。
- **坑 1（会丢正文，本会话实际踩到）**：**不要**在跟随 `next_cursor` 的那次调用里附带 `host_delivery_confirmation`。服务端会据此认定「完整正文已交付」而直接进入 attestation 模式，**剩余分块正文不再下发**（表现为只返回元数据 + Challenge、`delivery_integrity: incomplete`）。正确顺序：先取完所有分块（最后一块含 `<<<AOCI_OVERVIEW_BODY_END/v1>>>` 结束标记），再单独一次调用同时提交 `host_delivery_confirmation`（`version: overview-delivery-receipt/v1`、`body_sha256`/`body_bytes` 取自分块回执、`end_marker_observed: true`）与 `model_cognition_attestation`。已误传时：用新的 `refresh_event_id` 重开一次整链（正文重发，不猜写、不用 `aoci_get_entries` 补缺块）。
- **坑 2（提交口径再次复现）**：MCP 的 `aoci_update_entry` 批量 `entries` 入口仍被客户端 Schema 拦下（发布的 `oneOf` 分支互斥为空），与会话无关；继续用上面的 CLI `--source-sha256` 逐候选提交。另：顶层 `max_entries` 是**返回字段**不是入参，传入会被 `additionalProperties: false` 拒绝。
- **坑 3（门禁不可放过偶发失败）**：测试套件曾间歇性失败（一次 4 失败、一次 2 失败），根因是替身脚本用 `case "$*" in *-v*)` 做子串匹配、被 `mkdtemp` 生成的含 `-v` 目录名命中，把 `nginx -t` 误判成版本查询。**测试基建的假失败会直接摧毁门禁可信度**：复现→定位→修根因→补一条用固定前缀 `-v` 稳定复现的回归用例，并连跑 40 轮确认零失败后才提交。
- **坑 4（离线复算颜色会算错）**：`frontend/css/style.css` 里兜底 `:root` 块写在「日」色阶块**之后**，若按文件顺序把同选择器声明逐个叠加，亮色主题会被暗色值覆盖（本次写 `scripts/contrast_audit.py` 时实际踩到，第一版把亮色 text-1 算成 1.10:1）。真实成因是层级而非顺序：`:root` 落在 `<html>`，对 `<body>` 只能是继承值，而 `[data-theme…]` 落在 `<body>`，自身声明恒胜。改为「两趟解析」后与真实浏览器 `getComputedStyle` 取色逐项核对一致（88 项全对齐）。
