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
| 对比度 | 暗色最浅灰字 4.5:1（重构前 2.9:1）；emerald-light 主色白字 5.0:1（原 4.1:1） | **已全量达标 WCAG AA（2026-09-22）**：`scripts/contrast_audit.py` 覆盖 24 对 × 8 主题 = **192 项全过**（可读文本 ≥4.5、非文本控件边界 ≥3.0），脚本解析值与真实浏览器 `getComputedStyle` 逐项一致（192/192），并已接进双入口测试与 CI。本次修掉原 21 项严格档差距（亮色 `--text-3/--text-4`、亮色四个语义色、emerald-dark `--text-4`、全部主题的 `--border-strong`）；取色按「护眼」原则——只压/提到刚过线的第一档、保留各主题色相、不追求更高对比，且只让**控件轮廓**变强，`--border` 分隔线保持轻。语义色的正文主要落在自己的 `-soft` 芯片上（`.badge-running` / `.test-result` / `.callout-*` / `.diff-view` 的 `dl-add`、`dl-hunk`），芯片比纯白面板更严，已一并核对两种底座。**上界收紧（2026-09-22）**：「全过 AA」只保证了看得清，深色主题的正文主色却仍是 16.6:1、语义色 9.3:1、主按钮填色 10.7:1，满屏刺眼（用户反馈）。故给上界也定线：深色正文主色 ≤ 约 12:1（16.63→12.07）、次级正文 ≈ 6.8:1（8.21→6.82）、语义色 ≈ 7:1（ok 9.30→7.04 / warn 9.23→7.02 / danger 6.37→5.61 / info 7.28→6.21），四个深色强调色降饱和约 8–10%（翡翠 `#3ddc97`→`#2bc280`、海洋 `#4d9ef5`→`#4597ef`、琥珀 `#f0a53c`→`#e59320`、玫瑰 `#f4728c`→`#ec6983`）并把主按钮填色档 10.70→8.22；浅色只把正文主色 16.51→14.11 与次级正文 7.21→6.88。**不动**的部分同样是有依据的：灰字三/四档与全部 `--border-strong` 本就贴 AA 下限（深色 `border-strong/bg-raised` 3.16–3.18），浅色语义色被自身 `-soft` 芯片压到 4.65，都没有下压空间。改后 192 项仍全过（退出码 0），`ui.js` 色板色块同步为新的强调色，双入口测试 118 例全绿 | ✅ 全面达标（AA，含严格项） |
| 危险操作防护 | 保存前自动备份 + `nginx -t` + `409 saved:true` 警告条 + 一键回滚；路径穿越校验 9 处；全仓 `shell=` 命中 0（无 shell=True）；写接口强制 `X-Requested-With` | 同上；另加 `--port` 入口范围校验（越界或非整数以退出码 2 拒绝，不在 bind 阶段才抛错）、单行 `location` 拒改写、未建模配置拒改写；备份改为**用户显式选择**（保存并备份 / 仅保存），未启用备份时「回滚」入口自动退化为回滚到最新备份（不留死按钮）；**日志读取路径校验放宽到配置声明的候选**（2026-09-22：`/api/logs/access` 的 `path` 原先只认 prefix/confDir 之内，导致「下拉里选中刚刚展示的同一个文件」被判 403——发行版把日志写到 `/var/log/nginx` 时必现；现允许用户自己 nginx.conf 声明的候选路径，其余任意路径仍 403，e2e 与单测都锁住这条边界） | ✅ 达标 |
| 阻塞式原生弹窗 | `alert(` / `confirm(` / `prompt(` 命中 0（全部为自绘弹窗） | 复测仍为 0 | ✅ 达标 |
| 减少动效 | `prefers-reduced-motion` 已有处理 | 未改动 | ✅ 达标 |
| 键盘可达 | 仅 Ctrl/Cmd+S、编辑器 Tab 缩进、Esc 关弹窗。**无弹窗焦点陷阱、弹窗打开时背景未 `inert`**（Tab 会跑到遮罩后）；页签无方向键切换 | 弹窗焦点陷阱 + 背景 `inert` + 关闭归还焦点；页签 roving tabindex + 方向键；编辑器以 Esc 交出焦点（Tab 被缩进占用）；真实浏览器纯键盘走查「切页签 → 选文件 → 编辑 → 保存 → 处理 409 → 回滚」一遍通过 | ✅ 达标 |
| 无障碍语义 | `aria-label` 24、`role=` 9、`aria-expanded`（HTML 1 + JS 4）；**`aria-selected` / `aria-live` / `aria-labelledby` / `tabindex` 均为 0** | `aria-label` 35（+JS 2）、`role=` 25、`aria-labelledby` 8、`aria-selected` 6（+JS 3）、`aria-live` 2（+JS 1）、`tabindex` 6（+JS 5）、`aria-modal` 9 | ✅ 达标 |
| 焦点样式 | `:focus-visible` 1 处、`:focus` 6 处（键盘专用焦点样式偏少） | `:focus-visible` 10 处、`:focus` 16 处（自带底色控件给醒目焦点环） | ✅ 达标 |
| 编辑器能力 | **无行号**、无 Ctrl+Z。nginx 报错精确给出行号，编辑器却不提供行号定位 | 行号栏随行数与滚动同步；Ctrl/Cmd+Z 撤销 + Ctrl+Y 重做（自建快照历史、连续输入合并）；校验失败跳转后居中滚动并高亮该行。**布局修复（2026-09-22）**：窗口不高、且 `nginx -t` 结果条与「已保存但校验失败」提示条同时出现时，编辑器卡片会被挤到 100px，而卡片内子元素各有最小高度、卡片又不裁切，提示条遂顶出卡片 231px 压在「配置备份」面板上（左下角露在卡片外）。修法：卡片 `overflow:hidden` 自裁切 + 编辑器区可收缩让位 + 提示条不参与压缩 + 停靠区保留让位下限 + 结果条封顶 30vh 条内滚动；并把这条布局契约加进 `scripts/dom_contract.py` 静态门禁（删掉任一条即失败） | ✅ 达标 |
| 日志查看 | **只有手动刷新**：点一次拉一次尾部快照并整段替换文本（无实时跟随、无暂停、看长日志刷新后跳回顶部；切换日志文件不重载） | **实时跟随 + 可暂停（2026-09-22）**：后端按字节偏移增量下发（`offset` 只在完整行边界前进，尾部半行不下发、轮转/清空置 `reset`、单次 256 KiB 上限配 `hasMore` 续取），前端 1s 轮询只做追加——跟随时贴底滚动、暂停时保持阅读位置并显示「已暂停 · 新日志 N 行」、往上滚自动暂停、再点按钮跳回最新；缓冲区按 5000 行封顶；仅当前可见的停靠页签且页面在前台才轮询。**验收**：真实浏览器走查（跟随→暂停→暂停期间追加不跳位→过滤→继续）全通过；真实 nginx e2e 新增 5 项增量读取用例（38 项全通过）；`tests/test_logs_follow.py` 12 例覆盖尾部/增量/半行/轮转/多字节/上限续取/越界拒绝 | ✅ 达标 |
| 自动化测试 | **`tests/` 不存在**；CI（`build.yml`）只有 build + release，**无任何测试任务** | `tests/` 118 用例（真实子进程 HTTP 全链路、备份/回滚含最坏失败模式门禁、`nginx -t` 行号解析、DOM 契约、启动参数边界、工作区 nginx 双布局探测、备选单选落盘、单行 location 拒改写、upstream 手写指令守卫、引号内花括号与 CRLF 保持、API 契约与版本一致性、版本派生三处断言（spec 的 exe 名、打包产物名与版本属性、exe 前端持久化目录 `frontend/v{版本}/` 及其清理）、对比度 AA 门禁（192 项，冻结配色对清单 + 阈值不得低于 AA）、日志实时跟随的增量读取语义（12 例，含「配置声明但位于 prefix 之外的日志路径显式传参须可读」的回归））；`scripts/dom_contract.py` 静态契约检查（id/class/CSS 变量/主题 token 四项 + 布局契约门禁）；pytest 与标准库 `unittest` 双入口都能跑通（118 用例 / 64 subtests）；GitHub Actions 测试门禁（`tags-ignore: v*.*.*`，不干扰发布）；`.gitea/` 侧：2026-09-24 起该实例已有 runner（`win-runner`/`wsl-runner`），`build.yml` 已收敛为 Windows 单平台打包并实测存活（热跑 25 秒出 exe，见第五节当日打包记录）；`tests.yml` 因 `wsl-runner` 未接单（20 次运行排队中）尚未产生门禁效果；**2026-09-23 增至 169 例**（新增 `tests/test_config_cache.py` 18 例锁缓存有效性与失效正确性、`tests/test_http_layer.py` 11 例锁连接复用与静态条件请求，日志跟随补 1 例锁「增量读取触发行数上限时不得把行粘成一行」；其中三处关键断言与一处既有缺陷修复均做过变异验证）；**2026-09-23 发布测试批次后增至 194 例**（新增 `tests/test_nginxctl_unit.py` 8 例锁进程名解析与单行 `stub_status`/单行 server 块/块头换行 的识别、`tests/test_server_api.py` 3 例锁「只用 `--nginx-path/--conf-dir` 启动也算已配置」、`tests/test_http_layer.py` 4 例锁 HEAD 与 GET 同路由且无正文、`tests/test_proxymgr_unit.py` 2 例锁「已存在同名 location 时返回 already 且不改动」、`tests/test_dom_contract.py` 3 例静态守卫锁日志面板空态占位在追加前被清除）；**首轮测试发现的 6 项缺陷（含 1 项本批次引入的界面回归）已全部修复并复测**，复现证据、修法与复测结果见 `RELEASE_TEST_REPORT.md` 第六、七节 | ✅ 达标 |
| 契约与版本一致性 | 未度量 | `API.md` 与实现端点集合 34/34 双向一致；**27 个端点的成功响应顶层字段双向核对**（12 个 GET 全覆盖 + 15 个写端点，含 restore/备份删除/地址池/upstream/代理）；剩 7 个（nginx 启停重载重启、重启服务、选择路径、改设置）需真实 nginx 运行或图形界面，未纳入自动化、仅人工核对字段；版本单一来源 `server_version` 且不落后最新 tag | ✅ 达标 |
| 运行性能（空闲轮询 / 渲染） | 未度量（此前无基准，只有「未度量」） | **2026-09-23 新增基准 `scripts/bench_hotpath.py` 并实测**（11 个 include 文件 / 24 个 location / 12 条池地址；`repeat=60`）。**口径**：`opens` = 一次调用实际打开配置文件/日志文件的次数（`os.stat`/`scandir` 不计），即「这次调用要不要把配置重读一遍」。**根因修复**：配置读取与四类派生扫描（错误/访问日志路径、stub_status、listen 端口）改为**内容身份签名**（`mtime_ns+size+ino`，含 glob 目录 mtime）门控的缓存——命中即证明磁盘未变，绝不是时间窗缓存。稳态每次调用：`find_stub_status` 1.600ms/14 opens → **0.020ms/0 opens（81.7×）**、`find_error_log_paths` 0.833→0.023ms（36.3×）、`find_access_log_paths` 0.805→0.021ms（38.4×）、`collect_included_files` 0.529→0.024ms（21.8×）、`detect_listen_port` 0.467→0.031ms（15.3×）。真实 HTTP 单请求 CPU（单条 keep-alive 连接）：`/api/metrics` 2901→**168µs（17.2×）**、`/api/logs/error` 1546→237µs（6.5×）、`/api/logs/access` 2110→402µs（5.3×）、`/api/proxies` 2225→1051µs（2.1×）、`/api/status` 293→127µs（2.3×）。一次代理切换（新建→列代理→切换→commit→再列）由 **5 读 5 解析** 降到 **2 读 2 解析**（4.26→2.22ms）。传输层：开 HTTP/1.1 keep-alive 后 60 次请求由 60 条连接降为**单连接复用**（每请求一次线程创建随之消失）；静态资源加 `ETag`/`Last-Modified` 条件请求，未变回 304 不重传正文（前端资源合计约 200KB）。前端：切离「配置文件」页签后日志面板**实测 4.5s 内 0 次 `/api/logs/*` 请求**（原来 1 次/秒且渲染进隐藏子树）；缓冲满 5000 行后不再整段重排——持续写入实测封顶 5000 行 / **26 个文本节点** / 约 110KB / 末行恒为最新且换行完整；状态条一个 10s 轮询周期内 DOM 变更 **0 次**（原每次十余处写入）。**本轮同时发现并修复 2 个既有缺陷**（详见第五节登记） | ✅ 达标 |

**综合评估（G1–G6 落地后）**：三层目标的可验收项均已达标——界面完成度约 0.95，发布成熟度约 0.9。差距从「可验证」转到「真实环境验证」。

**本表未覆盖的剩余风险**（发布正式版前应另行确认）：① ~~端到端只跑过替身脚本~~ **已补：真实 nginx 二进制走查落地为 `scripts/e2e_real_nginx.py`（38 项全通过，含真实启动/重载/停止、`nginx -t` 真实报错行号、代理生效与回滚、stub_status 抓取、日志读取、**日志增量跟随（尾部快照/只取新增/偏移用尽不重复/半行不下发/补齐后整行取到）**、安全边界；需工作区 `nginx-1.30.4/`，故不进 CI，发布前手动跑）；② 未在 Windows 实机验证（CI 测试任务跑 ubuntu，替身相关用例在 Windows 自动跳过）；③ ~~对比度为公式换算值，未做真机取色复核~~ **已补（2026-09-21）** 且 ~~WCAG AA 严格档仍有 21 项差距~~ **已修（2026-09-22）**：192 项全部达标 AA（24 对 × 8 主题，含灰字四档、控件轮廓对四个表面、语义色对面板与自身芯片两种底座），脚本值与真实渲染逐项一致，门禁已进双入口测试。**剩下的是 ②（Windows 实机走查）。**

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
  - **已达成（2026-09-21）**：`tests/test_api_contract.py` 双向比对端点集合（34/34）与成功响应顶层字段（差异时列出「仅文档 / 仅实现」）；版本单一来源校验＝`build.py` 提取值一致 + 白名单外不得硬编码 + 不得落后最新 tag。本轮修掉真实漂移：`GET /api/settings` 的 `dataDir / dataDirLocked / settingsFile`、`PUT /api/settings` 的 `dataDir`、`--data-dir` 与 `--port` 参数说明。**派生落地已补断言（2026-09-22）**：`VersionConsistencyTest` 新增三例，逐项把「三处派生」钉成门禁——① 打包 spec 自带版本正则必须从当前 `server.py` 提取出同一版本，且 exe 名表达式换一组假版本求值必须随之变化（挡硬编码）；② `build.py` 的产物名与 `generate_version_info()` 的 `OriginalFilename / FileVersion / ProductVersion / filevers` 必须与版本一致；③ `stage_frontend()` 的 `frontend/v{版本}/` 持久化目录由 `server_version` 派生、旧版本与 `.tmp` 残留被清理而无关目录不受影响。三例都做过变异验证（把派生改成硬编码字面量即失败）。
- [x] **G6 无已知 P0/P1**，且 `aoci check` 无待作者化的语义维护积压。
  - **已达成（2026-09-21）**：`SECURITY_AUDIT.md` 无未处置 P0/P1（P1 CSRF、P3 `--port` 范围校验已落地；P1「敏感操作二次确认」记为**已决策不做**，理由写在该文件）；`aoci check` 返回「✓ 可提交（五净）」、`aoci_maintain` 返回 `aligned`（维护批次口径：46 条起，两批后 48 条，第三批补 `scripts/contrast_audit.py` 后为 **49 条 / 源码 49 个**）。

## 四、明确不做（写进文档，避免反复讨论）

| 项 | 理由 |
|---|---|
| 移动端 / 触屏适配 | 定位是本机单用户桌面工具，适配成本高、收益低 |
| 登录鉴权 / 多用户 / RBAC | 用户已决策：仅绑 `127.0.0.1`，无鉴权 |
| 打印样式 | 无使用场景 |
| i18n / 多语言界面 | 单用户中文场景 |
| 自动更新 / 联网检查新版本 | 用户已决策「不搞自动更新」：升级 = 用新版 exe 替换旧 exe（配置与备份在数据目录里不受影响）。本产品不做联网检查、下载或静默升级 |
| 前端引入 CDN / 构建工具 / 框架 | 离线可用与可移植是硬约束（见附录） |

## 五、路线与阶段目标

| 版本 | 目标 | 允许的内容 | 状态（2026-09-22） |
|---|---|---|---|
| `0.7.x` | 保命补丁：G4 行号 + Ctrl+Z、G3 焦点陷阱 / `aria-live` | 只做小改动、纯前端、低风险 | ✅ 已落地（源码） |
| `0.8.0` | 测试基建：G1 + G2 落地并接入 CI | **功能冻结版**：本阶段不新增任何功能 | ✅ G1/G2 已落地并接入 CI；冻结期现已结束（见下） |
| `0.9.x` | 冻结候选：G5 + G3 全键盘走查 + G6 | 只修 bug，不加功能 | ✅ G5/G6 已落地、G3 全键盘走查通过 |
| `1.0.0-rc.1` | 发布候选版：把门禁全绿的源码交到真实环境验收 | 只做发布动作，不夹带新功能 | ✅ **已发布（2026-09-22）**：`server_version` 提为 `1.0.0-rc.1`、`release-notes/v1.0.0-rc.1.md` 已写；tag `v1.0.0-rc.1` 推 GitHub 与 Gitea；GitHub 流水线 `Build & Release` **成功**（run 35683165934），Release 资产 `nginx-manager-v1.0.0-rc.1.exe`（12,324,307 字节，HTTP 200 可下载）；main 上的 `Tests` 门禁同时**成功**（38 个提交首次过 CI） |
| `1.0.0-rc.2` | 第二个候选版：把 rc.1 之后的改动（页面版本号 + 更新历史、暗夜对比度上界收紧）交真实环境验收 | 只做发布动作，不夹带新功能 | ✅ **已发布并核实（2026-09-22）**：`server_version` 提为 `1.0.0-rc.2`、`release-notes/v1.0.0-rc.2.md` 已写；tag `v1.0.0-rc.2` 与 main 推 GitHub 与 Gitea；GitHub 流水线 `Build & Release` **成功**（run 35693399802），Release 资产 `nginx-manager-v1.0.0-rc.2.exe`（12,342,236 字节，HTTP 200 可下载）；main 上的 `Tests` 与 `Sync Release Notes` 同时**成功**（提交 `e6be93e`） |
| `1.0.0` | 正式版：三层目标全部达标 + rc 验收通过 | 验收通过后转正 | ✅ **已发布（2026-09-23）**：转正动作已执行——`server_version` → `1.0.0`、`release-notes/v1.0.0.md` 已写、README 功能列表核对一致、`release: v1.0.0` 提交（`ea79bba`）、annotated tag `v1.0.0` 已推 GitHub；main 上的 `Tests` 门禁在该提交上**成功**，`Build & Release` 已触发（v1.0.0）；Gitea 侧推送与 Release 资产核对待网络恢复后补（详见下方发布记录） |

**关键约束：先把保障建起来，再谈正式版。** 0.8.0 必须是功能冻结版。

**功能冻结已于 2026-09-22 由用户指令解除一次（有边界）**：用户要求「日志做成实时滚动 + 可暂停，加完这个功能再谈发布」。因此本轮**在冻结期外**新增了一项功能（日志实时跟随 + 暂停），并同步补齐契约、双入口测试、真实 nginx e2e 用例与真实浏览器走查。**只此一项**：其余改动仍按「只修缺陷」处理（本轮顺带修掉一个真实缺陷：`/api/logs/access` 显式传回配置声明的日志路径被判 403）。冻结恢复后如需再增功能，仍须先取得用户明确指令并在此处登记。

**功能冻结第二次解除（2026-09-22，用户指令「页面添加版本号显示，和更新历史。不搞自动更新」）**：rc.1 已发布、正等真实环境验收，本轮又**在冻结期外**新增一项功能——顶栏常显 manager 自身版本号 + 「更新历史」弹窗（数据源就是仓库 `release-notes/*.md`），并明确**不做自动更新**（无联网检查、无下载、无静默升级）。该功能落在 rc.1 之后，故正式版 `v1.0.0` 会**包含**它。

**功能冻结第三次解除（2026-09-23，用户指令「做一次性能优化」）**：rc.2 已发布、正等真实环境验收，本轮**在冻结期外**做一次前后端性能优化（用户选定范围：前后端一起做；力度档位：A 档 + HTTP 层）。**不新增功能、不改任何端点与响应字段**——`tests/test_api_contract.py` 的顶层字段集合断言原样通过；改动只落在「重复读取/重复解析的消除」「前端 DOM 增量更新」「传输层 keep-alive 与条件请求」三处，实测值与口径见第二节新增行。本轮的次级停点与两个顺带修掉的既有缺陷：

1. **配置读取缓存只做内容身份门控，不做时间窗**：缓存键是文件的 `(mtime_ns, size, ino)`（外加 glob 扫过的目录 mtime），命中即证明磁盘未变，因此**不可能返回与磁盘不一致的日志路径或 stub_status**；配置一变（含「glob 目录里新增/删除了被 include 的文件」这种没有任何已知文件被改动的形态）立即重新解析。管理端自己的写路径（配置保存、备份恢复、代理/池/upstream 改动、状态页开启）另外**显式失效**缓存，把「改完还读到旧值」这一整类顾虑清零。`tests/test_config_cache.py` 18 例同时锁两侧：有效性（稳态重复调用不得再打开任何配置文件、不得再逐行重扫、不得再重走 include 图）与正确性（六种配置变更形态都必须立刻可见）。三处关键断言做过变异验证（缓存永不命中 / include 图永不命中 / 缓存永不失效，均被捕获）。
2. **顺带修掉的既有缺陷 ①（日志被粘成一行）**：`read_log_since` 在增量读取触发行数上限时用 `"".join` 重新拼接，把保留的行**全部粘成一行**（0 个换行）——前端表现为「日志突然变成一整条」，并让按换行计数的新行统计失真。既有用例只覆盖了尾部读取的上限分支，没覆盖增量分支。已改为 `"\n".join` 并补 `test_incremental_lines_cap_keeps_line_breaks`（含首行/末行/行数/offset 四项断言，并做过变异验证）。
3. **顺带修掉的既有缺陷 ②（keep-alive 下请求体残留）**：开启 HTTP/1.1 复用连接后暴露——提前返回的写请求（如缺 `X-Requested-With` 的 403）原先不读走请求体，残留字节会被当成**下一个请求**的请求行（表现为莫名 501 且整条连接后续全乱）。HTTP/1.0 每请求一条连接，残留字节随连接丢弃，所以从未暴露。已在响应汇聚点（`_send_json`）与静态响应对请求体做排空，并加 `tests/test_http_layer.py`（11 例：连接复用、404/403/304 之后连接仍可用、ETag 与 If-Modified-Since 优先级、非法日期头不 500、接口仍为 `no-store`）。
4. **试过又撤回的一项**：曾把「贴底滚动」合并到 `requestAnimationFrame` 以省掉每次追加后的强制重排，实测引入两个真实回归——追加后到下一帧之间面板尚未贴底，滚动锚定派发的 `scroll` 被「往上滚自动暂停」误判成用户行为，**跟随中的日志在持续写入时把自己暂停掉**；且标签页不可见时 rAF 不触发，排队标记永久卡住、之后再也不贴底。已撤回，贴底滚动保持与 rc.2 一致的同步语义（省下的那次重排不值得改变既有跟随/暂停契约）。该判断由真实浏览器持续写入实测得出（修复后实测：增长全程 `follow=true` 且贴底，缓冲封顶 5000 行、末行恒为最新）。
5. **交付后自查又发现并修掉的两处自身缺陷**（同日的复查轮次，均已补回归或对照实测）：
   - **空 watch 被当成有效缓存**（`backend/nginxctl.py`）：watch 集为空只可能是主配置读不到（能读到就必进 watch），而空签名恒等于自身，于是「启动时还没有 nginx.conf、之后才创建」会一直命中那份**空结果**，配置树永远空着。已改为「watch 为空即不认为缓存有效」，并补 `test_main_conf_created_after_first_lookup_is_picked_up`（含变异验证）。同时把 `st_mode` 计入签名，使「先无权限读、事后 chmod 可读」也能触发重新解析（此项属加固，未观察到对应缺陷）。
   - **状态条写入守卫对数字型字段静默失效**（`frontend/js/ui.js`）：`setText` 直接拿 `textContent`（永远是字符串）与值比较，而 `pid`、活跃连接数是数字，恒不相等 → 每隔 10s 照写。第一轮「一个轮询周期 0 次 DOM 变更」的实测之所以是 0，是因为当时 nginx 处于停止态、这些字段是字符串，**没覆盖到运行态**。已改为统一按字符串比较，并用「伪装成 nginx 运行中（返回数字 pid）」的桩后端实测对照：修正前 2 个周期内 5 次写入（`stPid`/`stConn` 每轮各一次），修正后 0 次。
   - 另把 `switchTab` 里的日志停表提到串行加载**之前**：原来停在加载之后，那几百毫秒里已隐藏的面板仍在轮询与渲染（实测切走后立即停表，隐藏 4.5s 内 0 次请求）。

**发布记录（2026-09-22，用户指令「发布一个版本 我要到真实环境测试」）**：以 **`v1.0.0-rc.1`** 作为发布候选版交到真实环境验收；正式版 `v1.0.0` 待验收通过后只改版本号转正。本次发布的动作与产物：

1. **先打通预发布号**：此前 `server_version` 只被 `([\d.]+)` 接受，直接打 `-rc.1` 会让版本门禁崩在 `int("0-rc")`，exe 名也带不上 rc。已改 `build.py` / `nginx-manager.spec` / `tests/test_api_contract.py` 三处提取正则接受 `-` 后缀；Windows 资源版本（`filevers`）取数字前缀（`1.0.0-rc.1 → 1.0.0.0`，资源版本没有预发布位），完整字符串仍写进 `FileVersion` / `ProductVersion`；tag 排序改为预发布感知（同号下正式版 > rc，rc.2 > rc.1），并新增 `test_prerelease_version_supported` 防回归。
2. **修 Gitea 构建流水线的产物路径漂移**：`.gitea/workflows/build.yml` 仍在上传 `dist/nginx-manager` 与 `dist/nginx-manager.exe`，而 spec 实际产出 `nginx-manager-v{版本}[.exe]`——Release 上传会因找不到文件失败。已按真实产物名修正上传路径与 Release 文件清单；该实例虽未配 runner（见第 5 条），修它是为了让两处配置保持一致。
3. **产物与文档**：`release-notes/v1.0.0-rc.1.md`（覆盖 v0.7.0 之后的全部改造，供真实环境按项验收）；README 中英功能列表同步「日志实时跟随」。
4. **本批次的代码改动**：日志实时跟随 + 可暂停（契约 `since/offset/reset/hasMore` → 后端只在完整行边界推进的增量读取 → 前端跟随/暂停/未读计数），并顺带修掉 `/api/logs/access` 把配置声明的日志路径显式传回却被判 403 的缺陷。
5. **通道与执行体限制（环境事实，非代码问题）**：GitHub 的 22 端口被透明代理劫持（解析到 `198.18.0.55`，SSH banner 超时），HTTPS 又无凭据，故推送改用 **`ssh.github.com:443`** 备用通道；Gitea（`origin`，`http://192.168.5.10:3020`）推送正常，但**该实例未配置 Actions runner（2026-09-22 用户确认）**——`.gitea/workflows/` 下的 build 与 tests 两条流水线都不会执行，CI 门禁与发布产物**只来自 GitHub Actions**，Gitea 仅作代码与 tag 镜像；两条 `.gitea` 工作流文件已加注说明，配置保持与 `.github` 镜像一致，将来配上 runner 即可生效。日后本机再发版，先确认这两条通道与执行体是否仍可用。

**发布记录（2026-09-22，版本号显示 + 更新历史）**：用户点名「页面添加版本号显示，和更新历史。不搞自动更新」。落地点：

1. **契约先行**：`API.md` 给 `GET /api/status` 增加 `managerVersion`（与 nginx 自己的 `version` 是两个字段，界面顶栏显示的是前者），新增 `GET /api/changelog` → `{version, releases[], notesAvailable}`，并在「前端行为约定」写明版本号入口与「无自动更新」。
2. **数据源就是发布说明**：`/api/changelog` 直接解析仓库 `release-notes/*.md`（版本号取文件名；`## ` 标题去掉开头的版本号前缀；`### ` 小节收 `-` 列表项与散文段落，围栏代码块/引用/表格/分隔线不进历史；文末 `Full Changelog` 链接），按版本号倒序（同号下正式版排在预发布版之前）。**不新增第二份「更新日志」文件**——两处维护必然漂移。
3. **打包与持久化**：`nginx-manager.spec` 把 `release-notes/` 一并打入 exe；`stage_frontend` 的复制/清理逻辑抽成 `_stage_versioned`，新增 `stage_release_notes` 把发布说明持久化到数据目录 `release-notes/v{版本}/`（与前端同一套规则）——临时解压目录被系统清理时，更新历史不会变空。
4. **界面**：顶栏版本号 chip（点击打开弹窗）+ 更新历史弹窗（`details/summary` 折叠、键盘可用；当前版本带「当前版本」徽章且默认展开；要点文本先转义再套白名单行内标记 `**粗体**` / `` `代码` ``，对比链接只作纯文本展示）。所有新元素只复用已审计的配色对。
5. **门禁**：`tests/test_changelog.py` 新增 21 例（解析规则、排序与「独立实现」比对、打包与持久化目录、界面入口与「前端零外部请求」）；双入口 **139 例全绿**（原 118 例）、`contrast_audit` 192/192、`dom_contract` 通过；真实浏览器对 8 套主题实测新元素最低对比度 **5.09:1**（版本 chip 的 text-3 / bg-raised；AA 下限 4.5），弹窗内文本 6.82–7.03、版本徽章 5.44–6.21。
6. **「不搞自动更新」的可验证边界**：没有 `/api/update` 之类端点、前端脚本零外部请求（均有测试断言），弹窗只说明「升级 = 用新版 exe 替换旧 exe，配置与备份在数据目录里，替换后继续可用」。

**发布记录（2026-09-22，用户指令「完善一下 aoci资产 然后打个tag 发布一版」）**：以 **`v1.0.0-rc.2`** 作为第二个候选版交真实环境验收（rc.1 的验收载体作废，验收项并入本版）。本次动作与产物：

1. **版本与说明**：`server_version` 由 `1.0.0-rc.1` 提为 `1.0.0-rc.2`；`release-notes/v1.0.0-rc.2.md` 覆盖 rc.1 之后的两批改动（页面版本号 + 更新历史、暗夜对比度上界收紧）并列出验收重点；README 中英功能列表同步「页面版本号 + 更新历史」。
2. **发布通道**：GitHub 的 22 端口仍被透明代理劫持（SSH 不可用），本次**改用 HTTPS 推送**（`https://github.com/lutao043/nginx-manager.git`，凭据走 macOS 钥匙串）；tag `v1.0.0-rc.2` 与 main 推两个远端。Gitea 该实例仍未配 runner，CI 门禁与 exe 产物只来自 GitHub Actions。
3. **AOCI 收尾**：本批次的受管对象（`backend/server.py`、`README.md`、`README.en.md`、`API.md`、`ROADMAP.md`，以及新增的 `release-notes/v1.0.0-rc.2.md`）在提交前完成认知维护，`aoci check` 返回「✓ 可提交（Entries漂移/待策展/字典/格式/草稿五净）」、`aoci_maintain` 返回 `aligned`（`code_drift` 全空），索引条目由 50 增至 51（新增 rc.2 说明条目）。
4. **门禁**：双入口 139 例、`contrast_audit` 192/192、`dom_contract`、`node --check` 与「版本号不落后于最新 tag」全部通过（复现命令见第六节）。
5. **实测回写（同日补记）**：GitHub Actions `Build & Release` **成功**（run 35693399802：`build-windows` 与 `release` 两个 job 全绿），Release `nginx-manager v1.0.0-rc.2` 已创建（正文即 `release-notes/v1.0.0-rc.2.md`），资产 `nginx-manager-v1.0.0-rc.2.exe` 12,342,236 字节、`content-length` 与下载探测均为 HTTP 200；`Sync Release Notes` 与 main 上的 `Tests` 门禁在提交 `e6be93e` 上同时**成功**。
6. **面向使用者的说明口径**：发布说明按用户视角写（只讲能感知到的变化、升级方式与需要验证的点），不放内部门禁数字与用例数；内部口径留在本文件与提交记录里。

**验收通过后的转正动作**（**已于 2026-09-23 执行**，见下方发布记录）：`server_version` 由 `1.0.0-rc.2` 改为 `1.0.0` → 写 `release-notes/v1.0.0.md` → 同步 README 中英功能列表 → `release: v1.0.0` 提交 → 打 tag `v1.0.0` 推两个远端。**验收范围** = rc.1 的全部项目 + rc.2 的新增项 + 本轮性能优化与发布测试修复。

**发布记录（2026-09-23，用户指令「补充一下 然后发布正式版」）**：以 **`v1.0.0`** 作为正式版发布。本次动作与已核实的产物：

1. **发布前先补齐两道欠账**（同日的发布测试批次，见本文件第二节与 `RELEASE_TEST_REPORT.md`）：首轮测试发现的 **6 项缺陷全部修复并补齐 24 条回归**（D1 macOS 进程识别、D2 单行 `stub_status`、D3 日志面板空态占位、D4 CLI 路径不计入 configured、D5 「开启统计」提示语义、D6 HEAD 支持），双入口用例由 170 增至 **194**；性能批次与修复批次因改同一批函数（拆开会让中间提交测试不过）合并为一个提交 `7296a6d`。
2. **转正动作**：`server_version` → `1.0.0`；新增 `release-notes/v1.0.0.md`（面向使用者：本版变化、rc 已交付内容、升级方式与重点试项）；README 中英功能列表核对与交付能力一致（本版无新增功能，无需增删）；`release: v1.0.0` 提交 `ea79bba`；annotated tag `v1.0.0`。
3. **推送与门禁**：main 与 tag 已推 GitHub（`git@github.com:lutao043/nginx-manager.git`，本次 SSH 22 端口可用；HTTPS 通道仍报 HTTP/2 framing 错误，未使用）。main 上的 `Tests` 在 `ea79bba` 上**成功**、`Sync Release Notes` 成功，`Build & Release`（`v1.0.0`）**已触发**。
4. **未核实项（环境限制，非代码问题）**：本机网络当时不可达，**Release 资产 `nginx-manager-v1.0.0.exe` 尚未核对**（rc.1/rc.2 各约 12.3MB）；Gitea（`origin`，`http://192.168.5.10:3020`）当时连接超时，main 与 tag 未推送、该实例也未配 runner。网络恢复后需：`git push origin main && git push origin v1.0.0`，并确认 GitHub Release 页面出现 `nginx-manager-v1.0.0.exe` 可下载。

**打包记录（2026-09-24，用户指令「gitea 上有 windows 的打包运行器了，我目前只需要打包 windows 的，多余的去掉，处理一下然后触发打包一次试试」）**：把自建 Gitea 变成可用的 Windows 打包机。**结果：`Build Windows` 手动试跑全绿——热跑 25 秒产出 `nginx-manager-v1.0.0.exe`（11,303,704 字节，SHA-256 `585dc632…2df24`），产物落在 runner 主机 `%USERPROFILE%\nginx-manager-build\dist\`；冷启动（含装 Python）约 2 分钟。**

1. **实例事实（推翻 2026-09-22 的「无 runner」结论）**：该实例（Gitea 1.27.0）现有两个在线 runner——`win-runner`（label `windows-latest`，宿主 Windows）与 `wsl-runner`（label `ubuntu-latest`）；Actions 已启用，`workflow_dispatch` 与 `inputs` 均可用（dispatches API 返回 204）。
2. **流水线收敛为 Windows 单平台**：`.gitea/workflows/build.yml` 去掉 macOS 构建与 release 任务，只留 `build-windows`（触发：tag `v*.*.*` 或手动 dispatch），产物落固定目录并打印大小与 SHA-256。Gitea 侧不发布 Release——发布通道仍由 GitHub Actions 承担，本实例只解决「在局域网里快速打包」。
3. **该机器上标准 action 用不了**：`win-runner` 是宿主模式（Windows 无 Docker），主机上没有 Node.js，`actions/checkout|setup-python|upload-artifact` 在任务准备阶段就失败（`Cannot find: node in PATH`，run #25）。故全程只用宿主已有的 git/curl/cmd：git clone 私有仓库（临时 `GITHUB_TOKEN`）、python.org 官方安装包静默装到用户目录（`InstallAllUsers=0`、`PrependPath=0`、含 tcl/tk 与 pip，不动系统 PATH、不需要管理员）。
4. **实测速度**：Python 安装包走国内镜像 3 秒（27.5MB，约 7.5MB/s；python.org 直连约 30KB/s、要十几分钟，故镜像优先、官方兜底）；PyInstaller 走清华 PyPI 镜像。
5. **踩到并已修的四个坑（结论写进工作流注释）**：① **cmd 步骤必须纯 ASCII**——runner 按 UTF-8 写脚本、cmd 按本地代码页（cp936）读，中文字节会吃掉后续字符（实测 `'OKEN' not recognized`、token 被当命令执行）；② **括号块内的 echo 文案不能带圆括号**——`)` 会提前闭合 `if (...)`，cmd 报「此时不应有 xxx」并以 255 中断；③ **残缺解释器会伪装成成功**——缺 DLL 的 `python.exe` 退出码 `0xC0000135`，`if errorlevel 1` 按负数判定为「未失败」，且加载器报错不经进程 stdout/stderr，步骤表现为「零输出 + 成功」，直到产物步骤才以 `no exe produced` 暴露；现于复用前用 `python -c "print('PYOK')"` + `findstr` 校验，不可运行即删目录重装；④ **同版本重装会被 MSI 注册项拦成空目录**——删掉目标目录后静默安装走「维护/无操作」不落文件，需接 `/repair`（实测能恢复解释器与 tkinter；repair 后 pip 需 `ensurepip` 自举，已装回 pip 25.2）。
6. **一个容易误判的现象**：PyInstaller 产物字节数每轮略有差异（11,303,704 / 11,304,225 / 11,304,439），是嵌入时间戳所致，不代表构建不稳定。
7. **未做/未验证**：Gitea 侧的 Artifact 上传（`actions/upload-artifact` 本身是 JS action）留待装上 Node.js 后恢复标准写法；Release 发布见下方「发布接线记录」；`tests.yml` 在 Gitea 侧**20 次运行全部卡在 queued**——`ubuntu-latest` 没被 `wsl-runner` 接单（该 runner 在 Gitea 显示在线，属 runner 侧配置，通常是改标签后未重启 agent），故 Gitea 侧测试门禁尚未生效，GitHub 的 `Tests` 仍是实际门禁。
8. **操作入口**：手动试跑不改任何发布物——

   ```bash
   curl -sS -u "<user>:<token>" -X POST -H 'Content-Type: application/json' \
     -d '{"ref":"main"}' \
     http://192.168.5.10:3020/api/v1/repos/lutao/nginx-manager/actions/workflows/build.yml/dispatches
   # 强制重装解释器：-d '{"ref":"main","inputs":{"force_python_reinstall":"true"}}'
   # 查运行与取日志：GET .../actions/runs/{id}、GET .../actions/jobs/{job_id}/logs
   ```


**发布接线记录（2026-09-24，用户指令「接上发布版本一起 做到gitea和github一样」）**：Gitea 侧现在与 GitHub 一样——推 tag 即自动建/更新 Release 并挂上 exe。**结果：v1.0.0、v1.0.0-rc.2、v1.0.0-rc.1、v0.7.0 四个版本已在 Gitea 补发完成（各带对应版本 exe、预发布号标 prerelease），重跑同一 tag 走更新分支并覆盖同名资产（幂等实测通过）。**

1. **实现**：新增 `release` job（needs build-windows、runs-on windows-latest），全程走 Gitea 自己的 REST API——curl 传 JSON 与 multipart 资产，PowerShell 5.1 只做 JSON 读写与流程控制（该主机无 Node.js，action 方案不可行）；`permissions` 由 read 提为 write。发布说明优先 `release-notes/<tag>.md`，缺失时由提交记录生成并附 Gitea compare 链接；tag 含 `-` 标 prerelease。
2. **补发入口**：`workflow_dispatch` 新增 `release_tag` 输入（填了它即按该 tag 取源码打包并发布），这是给旧 tag 补发的唯一可行方式（原因见下条）；手动不带该输入时 release job 打印 skip 并正常退出。
3. **「推 v1.0.0 却直接死掉」的根因（实测，非新流水线缺陷）**：tag 推送时 Gitea 用的是**该 tag 提交内的 workflow**，而 `ea79bba`（v1.0.0）处还是旧版双平台配置——`build-macos` 无 runner 可派（永久排队）、`build-windows` 在拉 `actions/checkout@v4` 时撞上 runner 主机访问 github.com 的超时（日志为 TLS handshake timeout / dial timeout），三个 job 全废。已发布的 tag 不重写；该次运行（run 77）仍在排队，Gitea API 没有取消入口，需在 Actions 页面手动取消或删除。
4. **PowerShell 5.1 的四个坑（都写进工作流注释）**：① 用 `ConvertTo-Json` 拼请求体时 Gitea 回 422「/body 是对象」——改用 runner 自带 Python 的 `json.dump(ensure_ascii=False)` 生成请求体后一次通过（同一分支在生成说明路径上曾成功，未再深究 PS 侧差异，直接换掉序列化实现）；② `ConvertFrom-Json` 输出数组时不展开，`@(管道)` 会把它当**单个元素**（`$a.id` 变成 `"12 13"`，DELETE 的 URL 带空格而失败）——先赋值再 `foreach`；③ `curl -o $null` 会把空参数传给 curl，DELETE 静默失败（曾留下两个同名资产）——改为写临时文件并校验 HTTP 码，上传后再复查同名资产恰好一个；④ git 的原生输出必须用 .NET Process 以 UTF-8 读——把 `[Console]::OutputEncoding` 设为 UTF-8 在本宿主（stdout 被重定向、无控制台）静默无效，发布说明会按 cp936 解成乱码。
5. **两个发布页的差异（核实于今日）**：Gitea 现有 10 个 Release（其中 v0.2.2/0.2.1/0.2.0/0.1.1/0.1 是当年手工发布的，GitHub 侧没有）；GitHub 另有 **12 个旧版本（v0.3.0–v0.6.2）Gitea 侧尚未补**——补发命令见下条记录的操作入口（`release_tag` 一次一个 tag），其中 v0.3.0 的产物名是早期的 `nginx-manager.exe`（不含版本号），补它需要先把 release job 的资产匹配放宽。
6. **核实口径**：以「run 全绿 + Release 资产核对（名称/字节数）」为准（run 81/86/87/88/89）；exe 字节数每轮略有差异是 PyInstaller 嵌入时间戳所致（见上一条记录第 6 点）。


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
#   含日志增量跟随的 5 项：尾部快照 / 只取新增 / 偏移用尽不重复 / 半行不下发 / 补齐后整行取到
NM_E2E_DIR=/tmp/nm-e2e python3 scripts/e2e_real_nginx.py

# 日志跟随：增量读取语义（curl 可复现；offset 只在完整行边界前进）
curl -s "http://127.0.0.1:8310/api/logs/error?lines=20"        # 尾部快照：reset=true、offset=size
curl -s "http://127.0.0.1:8310/api/logs/error?since=<offset>"  # 增量：reset=false，只含新增行
#   前端「跟随/暂停/继续 + 暂停期间不跳位 + 未读计数」是浏览器行为，e2e 覆盖不到，
#   需人工走查：切到日志页签 → 往日志文件追加内容 → 暂停 → 再追加（位置应不动、提示计数）
#   → 点继续（跳回最新）→ 往上滚（应自动暂停）

# 更新历史：版本号 + 逐版发布说明（curl 可复现；纯本地解析 release-notes/*.md，无联网检查）
curl -s http://127.0.0.1:8310/api/changelog | python3 -m json.tool
curl -s http://127.0.0.1:8310/api/status | python3 -c "import json,sys; print(json.load(sys.stdin)['managerVersion'])"
#   界面：顶栏版本号 chip → 打开「更新历史」弹窗（当前版本默认展开、带「当前版本」徽章）

# 对比度：8 主题 × 24 对 = 192 项，WCAG AA 门禁（退出码即结论；--quiet 只看结论）
python3 scripts/contrast_audit.py
#   --strict 是兼容别名（2026-09-22 起与默认档同一标准），配色的邻接与阈值说明见脚本 docstring

# 运行性能：热点路径基准（纯标准库，自建临时配置与替身 nginx，不触碰用户数据目录）
python3 scripts/bench_hotpath.py            # 人类可读表（cold=冷调用 / steady=稳态每次调用）
python3 scripts/bench_hotpath.py --json     # 机器可读（第二节实测值即由此回写）
#   读法：opens 列是「这次调用打开了几次配置文件」——steady 命中缓存时必须为 0，
#   非 0 即说明缓存失效或退化；conns 列是服务端为一个批次的请求接受的 TCP 连接数，
#   单条 keep-alive 连接复用时应为 0（连接在预热阶段已建立）
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

aoci 二进制：`~/.local/bin/aoci`（`aoci version 0.1.0-rc12`，布局 `volumes-v1`，MCP 工具 9 个；二进制与三个宿主的项目级配置都是**机器绑定**的，不入库——`.mcp.json`、`.codex/config.toml`、`.zcode/` 均在 `.gitignore` 里）。

| 宿主 | 配置文件 | 状态 |
|---|---|---|
| Claude Code | `.mcp.json`（项目级） | ✓ 已配置（`aoci doctor` 确认） |
| Codex | `.codex/config.toml`（项目级） | ✓ 已配置（首次在本仓运行 Codex 时需信任本项目） |
| Hermes | `~/.hermes/config.yaml` → `mcp_servers.aoci` | ✓ 已配置（`hermes mcp test aoci` 连通 813ms / 9 工具） |

宿主配置由 `aoci init --agent claude\|codex` 写入，属机器绑定内容，`.gitignore` 已由 aoci 自动排除（`.mcp.json`、`.codex/config.toml` 及其 backup），**不要提交**。换机器或升级二进制后重跑该命令即可，验证用 `aoci doctor`（「Agent 接入」段应显示 `已配置`）。

自检命令：`aoci --repo . doctor`、`hermes mcp test aoci`、`hermes mcp list`。

### B2 认知维护状态与提交口径（2026-09-21 已闭环，2026-09-22 复核）

认知维护积压清零：`aoci.code.txt` **51 条 / 源码 51 个**，`aoci check` 返回「✓ 可提交（Entries漂移/待策展/字典/格式/草稿五净）」，`aoci_maintain` 返回 `aligned`（`governance_aligned: true`、`code_drift` 全空）。批次沿革：G1/G2 测试与契约基建（新增 3 + 更新 9）→ 发布前复查（新增 2：`LICENSE`、`scripts/e2e_real_nginx.py`；更新 13）→ 补版本派生断言（更新 `ROADMAP.md`、`tests/test_api_contract.py`）→ 配色修 AA 与对比度门禁（更新 `frontend/css/style.css`、`scripts/contrast_audit.py`、`ROADMAP.md`，`tests/test_contrast.py` 落观察集）→ **rc.1 发布批次**（8 条，含新增 `release-notes/v1.0.0-rc.1.md` 条目，49→50）→ **Gitea 无 runner 批次**（3 条重创作）→ **对比度上界 + 版本号/更新历史批次**（9 条 update：`API.md`、`ROADMAP.md`、`VIBE_CODING_GUIDE.md`、`backend/server.py`、`frontend/css/style.css`、`frontend/index.html`、`frontend/js/api.js`、`frontend/js/app.js`、`nginx-manager.spec`；`frontend/js/ui.js` 的色板调整随该批前移基线）→ **rc.2 发布批次**（更新 `backend/server.py`、`README.md`、`README.en.md`、`API.md`、`ROADMAP.md`，新增 `release-notes/v1.0.0-rc.2.md` 条目，50→51）。测试类文件（`tests/*.py`，含新增的 `tests/test_changelog.py`）落在**观察集**而非索引集——改动只前移基线、不产生新条目；新增受管文件后若要继续维护，先按机器下发的 next_command 跑 `aoci scope acknowledge --repo . --reviewed-by <agent>` 完成作用域复核前移。

**提交口径（rc12 实测）**：MCP 的 `aoci_update_entry` 批量 `entries` 入口在本机被客户端 Schema 校验拦下（该字段发布的 `oneOf` 分支互斥为空，任何对象都判 `not valid under any of the given schemas`），正式写入零发生。可用传输是同一管线的 CLI：

```bash
aoci update-entry --repo . --json --path <仓库相对路径> \
  --source-sha256 <aoci_maintain 返回的该候选 sha> --stdin < 条目文件
```

逐候选提交与维护批次等价：`--source-sha256` 就是候选绑定，机器校验（Impact/S 字段配额）与原子写入完全一致。

正式流程仍以 `AGENTS.md` 与机器签发的 Plan/Guide 为准：新会话先 `aoci_rules` → `aoci_overview`（跟随 `next_cursor` 直至 `completed=true`）→ 受管理对象稳定后 `aoci_maintain` → 依据机器签发候选创作完整 F/R/A/S → 提交；`remaining` 非零时重新 `aoci_maintain` 取下一批。**不得手写索引语义绕过。**

**条目硬约束**：F ≤160 字、R ≤360 字/8 项、A ≤400 字/6 项；S 的 rune 上限按 C 档位——C7-4 ≤200、C3-1 ≤50（超限会被 `fras_s_too_long` 拒绝，只改 S 重提）。条目须陈述「文件此刻是什么」，变更历史归 Git，写成演进叙事会被 Validator 提醒。

**重要**：Hermes 的 MCP 工具在会话启动时加载，接入后必须**开新会话**才会出现 `aoci_*` 工具；当前会话内无法直接调用。

### B3 上下文压缩后的重载与八个实测坑（2026-09-21 起记录）

- **压缩后必须重载认知**：宿主注入压缩摘要后，此前模型认知一律不作数。用 `aoci_overview` 携带 `refresh_reasons=["context_compaction"]` 与**新的** `refresh_event_id` 请求完整 Whole-Index（不要设 `check_only`），原样跟随 `next_cursor` 到 `completed=true`，再提交交付确认与 Attestation（本次 43/43 条、Challenge 10/10 通过后 `cognition_verified`）。摘要里不得携带正式 Header / Entry / Challenge 正文。
- **坑 1（会丢正文，本会话实际踩到）**：**不要**在跟随 `next_cursor` 的那次调用里附带 `host_delivery_confirmation`。服务端会据此认定「完整正文已交付」而直接进入 attestation 模式，**剩余分块正文不再下发**（表现为只返回元数据 + Challenge、`delivery_integrity: incomplete`）。正确顺序：先取完所有分块（最后一块含 `<<<AOCI_OVERVIEW_BODY_END/v1>>>` 结束标记），再单独一次调用同时提交 `host_delivery_confirmation`（`version: overview-delivery-receipt/v1`、`body_sha256`/`body_bytes` 取自分块回执、`end_marker_observed: true`）与 `model_cognition_attestation`。已误传时：用新的 `refresh_event_id` 重开一次整链（正文重发，不猜写、不用 `aoci_get_entries` 补缺块）。
- **坑 2（提交口径再次复现）**：MCP 的 `aoci_update_entry` 批量 `entries` 入口仍被客户端 Schema 拦下（发布的 `oneOf` 分支互斥为空），与会话无关；继续用上面的 CLI `--source-sha256` 逐候选提交。另：顶层 `max_entries` 是**返回字段**不是入参，传入会被 `additionalProperties: false` 拒绝。
- **坑 3（门禁不可放过偶发失败）**：测试套件曾间歇性失败（一次 4 失败、一次 2 失败），根因是替身脚本用 `case "$*" in *-v*)` 做子串匹配、被 `mkdtemp` 生成的含 `-v` 目录名命中，把 `nginx -t` 误判成版本查询。**测试基建的假失败会直接摧毁门禁可信度**：复现→定位→修根因→补一条用固定前缀 `-v` 稳定复现的回归用例，并连跑 40 轮确认零失败后才提交。
- **坑 4（离线复算颜色会算错）**：`frontend/css/style.css` 里兜底 `:root` 块写在「日」色阶块**之后**，若按文件顺序把同选择器声明逐个叠加，亮色主题会被暗色值覆盖（本次写 `scripts/contrast_audit.py` 时实际踩到，第一版把亮色 text-1 算成 1.10:1）。真实成因是层级而非顺序：`:root` 落在 `<html>`，对 `<body>` 只能是继承值，而 `[data-theme…]` 落在 `<body>`，自身声明恒胜。改为「两趟解析」后与真实浏览器 `getComputedStyle` 取色逐项核对一致（88 项全对齐）。
- **坑 5（对比度的「配对」不能想当然，两层都要按真实邻接取）**：判断某色是否达标，必须先确认它实际落在哪个表面上，而不是挑一个看起来合理的表面。本次修 AA 时两处都靠 `grep` 证据纠正：① 灰字四档与语义色正文的真实底座是**面板/画布**，但语义色正文主要落在自己的 `-soft` **芯片**上（`.badge-running` / `.test-result` / `.callout-*` / `.diff-view` 的 `dl-add`、`dl-hunk`），芯片比纯白面板更严（旧值 3.63–4.39 vs 面板 4.10）；② `--border-strong` 在页面上从不落在 `--bg-hover` 上，而是 `--bg-raised`（输入框/按钮/徽章）+ `--bg-sunken`（编辑器/日志/diff）+ `--bg-panel` + 画布四者，最严的一档是「浅色 `--bg-sunken` / 深色 `--bg-raised`」——只核对画布会漏判。**结论：新配色对进脚本前，先用 `grep -n` 把该 token 与背景 token 同现的规则列出来当证据。**
- **坑 6（变异测试别在工作区改文件再用 `git checkout` 还原）**：为验证新门禁真的会红，本次先把 `style.css` 的色值改回旧值跑测试（确实 2 处失败），随后用 `git checkout -- frontend/css/style.css` 复原——**连同尚未提交的配色改动一起被抹掉**（只保住了报告里的结论，色值靠记忆重打一遍）。稳妥做法有两种：把待测文件先 commit 再变异；或**在 `scratch/` 里复制一份改坏的文件**、用 `--css` 参数/替换模块常量跑审计（本次补做后采用后者）。
- **坑 7（A 的 400 rune 是硬计数，超了只准压 A）**：Entry 的 A 字段上限 400 个 Unicode 字符，超限报 `fras_a_too_long`（`expected: max_runes=400`、`formal_writes_started=false`，零写入可安全重提）。本次给 6 个受管文件补新语义时，A 由 383–400 涨到 430–513 被拒；机器的 `safe_repair_action` 明确：**只压缩 A，F/R/S 逐字节不变**。同时 S 还受 token 档位约束（≈UTF-8 字节/3；C9 ≤200、C8 ≤140、C7-4 ≤80/40 token），C7 档的 `ROADMAP.md` S 已顶到 80 token——新事实只能进 A。写作时先把 A 的 `len()` 打印出来核对（≤400）再提交，比「先提交再被拒」省一整轮。
- **坑 8（E 规模档位要随文件生长更新，校验器提醒不是拒绝）**：`ROADMAP.md` 长到 209 行后，`--preview` 带回「E规模档位错配：文件209行按字典应为M，条目标注S；文件生长跨档属正常，请顺手更新E位」。这类提醒不影响写入，但标签会长期失真，改 tag 的 E 位（`SQ7S` → `SQ7M`）即消除。同理，`--preview` 全程零写入，先逐候选 preview 再 apply，能把全部 finding 提前看完。
