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
| 对比度 | 暗色最浅灰字 4.5:1（重构前 2.9:1）；emerald-light 主色白字 5.0:1（原 4.1:1） | **已全量达标 WCAG AA（2026-09-22）**：`scripts/contrast_audit.py` 覆盖 24 对 × 8 主题 = **192 项全过**（可读文本 ≥4.5、非文本控件边界 ≥3.0），脚本解析值与真实浏览器 `getComputedStyle` 逐项一致（192/192），并已接进双入口测试与 CI。本次修掉原 21 项严格档差距（亮色 `--text-3/--text-4`、亮色四个语义色、emerald-dark `--text-4`、全部主题的 `--border-strong`）；取色按「护眼」原则——只压/提到刚过线的第一档、保留各主题色相、不追求更高对比，且只让**控件轮廓**变强，`--border` 分隔线保持轻。语义色的正文主要落在自己的 `-soft` 芯片上（`.badge-running` / `.test-result` / `.callout-*` / `.diff-view` 的 `dl-add`、`dl-hunk`），芯片比纯白面板更严，已一并核对两种底座 | ✅ 全面达标（AA，含严格项） |
| 危险操作防护 | 保存前自动备份 + `nginx -t` + `409 saved:true` 警告条 + 一键回滚；路径穿越校验 9 处；全仓 `shell=` 命中 0（无 shell=True）；写接口强制 `X-Requested-With` | 同上；另加 `--port` 入口范围校验（越界或非整数以退出码 2 拒绝，不在 bind 阶段才抛错）、单行 `location` 拒改写、未建模配置拒改写；备份改为**用户显式选择**（保存并备份 / 仅保存），未启用备份时「回滚」入口自动退化为回滚到最新备份（不留死按钮）；**日志读取路径校验放宽到配置声明的候选**（2026-09-22：`/api/logs/access` 的 `path` 原先只认 prefix/confDir 之内，导致「下拉里选中刚刚展示的同一个文件」被判 403——发行版把日志写到 `/var/log/nginx` 时必现；现允许用户自己 nginx.conf 声明的候选路径，其余任意路径仍 403，e2e 与单测都锁住这条边界） | ✅ 达标 |
| 阻塞式原生弹窗 | `alert(` / `confirm(` / `prompt(` 命中 0（全部为自绘弹窗） | 复测仍为 0 | ✅ 达标 |
| 减少动效 | `prefers-reduced-motion` 已有处理 | 未改动 | ✅ 达标 |
| 键盘可达 | 仅 Ctrl/Cmd+S、编辑器 Tab 缩进、Esc 关弹窗。**无弹窗焦点陷阱、弹窗打开时背景未 `inert`**（Tab 会跑到遮罩后）；页签无方向键切换 | 弹窗焦点陷阱 + 背景 `inert` + 关闭归还焦点；页签 roving tabindex + 方向键；编辑器以 Esc 交出焦点（Tab 被缩进占用）；真实浏览器纯键盘走查「切页签 → 选文件 → 编辑 → 保存 → 处理 409 → 回滚」一遍通过 | ✅ 达标 |
| 无障碍语义 | `aria-label` 24、`role=` 9、`aria-expanded`（HTML 1 + JS 4）；**`aria-selected` / `aria-live` / `aria-labelledby` / `tabindex` 均为 0** | `aria-label` 35（+JS 2）、`role=` 25、`aria-labelledby` 8、`aria-selected` 6（+JS 3）、`aria-live` 2（+JS 1）、`tabindex` 6（+JS 5）、`aria-modal` 9 | ✅ 达标 |
| 焦点样式 | `:focus-visible` 1 处、`:focus` 6 处（键盘专用焦点样式偏少） | `:focus-visible` 10 处、`:focus` 16 处（自带底色控件给醒目焦点环） | ✅ 达标 |
| 编辑器能力 | **无行号**、无 Ctrl+Z。nginx 报错精确给出行号，编辑器却不提供行号定位 | 行号栏随行数与滚动同步；Ctrl/Cmd+Z 撤销 + Ctrl+Y 重做（自建快照历史、连续输入合并）；校验失败跳转后居中滚动并高亮该行。**布局修复（2026-09-22）**：窗口不高、且 `nginx -t` 结果条与「已保存但校验失败」提示条同时出现时，编辑器卡片会被挤到 100px，而卡片内子元素各有最小高度、卡片又不裁切，提示条遂顶出卡片 231px 压在「配置备份」面板上（左下角露在卡片外）。修法：卡片 `overflow:hidden` 自裁切 + 编辑器区可收缩让位 + 提示条不参与压缩 + 停靠区保留让位下限 + 结果条封顶 30vh 条内滚动；并把这条布局契约加进 `scripts/dom_contract.py` 静态门禁（删掉任一条即失败） | ✅ 达标 |
| 日志查看 | **只有手动刷新**：点一次拉一次尾部快照并整段替换文本（无实时跟随、无暂停、看长日志刷新后跳回顶部；切换日志文件不重载） | **实时跟随 + 可暂停（2026-09-22）**：后端按字节偏移增量下发（`offset` 只在完整行边界前进，尾部半行不下发、轮转/清空置 `reset`、单次 256 KiB 上限配 `hasMore` 续取），前端 1s 轮询只做追加——跟随时贴底滚动、暂停时保持阅读位置并显示「已暂停 · 新日志 N 行」、往上滚自动暂停、再点按钮跳回最新；缓冲区按 5000 行封顶；仅当前可见的停靠页签且页面在前台才轮询。**验收**：真实浏览器走查（跟随→暂停→暂停期间追加不跳位→过滤→继续）全通过；真实 nginx e2e 新增 5 项增量读取用例（38 项全通过）；`tests/test_logs_follow.py` 12 例覆盖尾部/增量/半行/轮转/多字节/上限续取/越界拒绝 | ✅ 达标 |
| 自动化测试 | **`tests/` 不存在**；CI（`build.yml`）只有 build + release，**无任何测试任务** | `tests/` 118 用例（真实子进程 HTTP 全链路、备份/回滚含最坏失败模式门禁、`nginx -t` 行号解析、DOM 契约、启动参数边界、工作区 nginx 双布局探测、备选单选落盘、单行 location 拒改写、upstream 手写指令守卫、引号内花括号与 CRLF 保持、API 契约与版本一致性、版本派生三处断言（spec 的 exe 名、打包产物名与版本属性、exe 前端持久化目录 `frontend/v{版本}/` 及其清理）、对比度 AA 门禁（192 项，冻结配色对清单 + 阈值不得低于 AA）、日志实时跟随的增量读取语义（12 例，含「配置声明但位于 prefix 之外的日志路径显式传参须可读」的回归））；`scripts/dom_contract.py` 静态契约检查（id/class/CSS 变量/主题 token 四项 + 布局契约门禁）；pytest 与标准库 `unittest` 双入口都能跑通（118 用例 / 64 subtests）；GitHub Actions 测试门禁（`tags-ignore: v*.*.*`，不干扰发布）；`.gitea/` 侧为同内容镜像，但**该实例未配置 Actions runner（2026-09-22 用户确认）**，两个 `.gitea` 工作流都不会执行、不构成门禁 | ✅ 达标 |
| 契约与版本一致性 | 未度量 | `API.md` 与实现端点集合 34/34 双向一致；**27 个端点的成功响应顶层字段双向核对**（12 个 GET 全覆盖 + 15 个写端点，含 restore/备份删除/地址池/upstream/代理）；剩 7 个（nginx 启停重载重启、重启服务、选择路径、改设置）需真实 nginx 运行或图形界面，未纳入自动化、仅人工核对字段；版本单一来源 `server_version` 且不落后最新 tag | ✅ 达标 |

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
| 前端引入 CDN / 构建工具 / 框架 | 离线可用与可移植是硬约束（见附录） |

## 五、路线与阶段目标

| 版本 | 目标 | 允许的内容 | 状态（2026-09-22） |
|---|---|---|---|
| `0.7.x` | 保命补丁：G4 行号 + Ctrl+Z、G3 焦点陷阱 / `aria-live` | 只做小改动、纯前端、低风险 | ✅ 已落地（源码） |
| `0.8.0` | 测试基建：G1 + G2 落地并接入 CI | **功能冻结版**：本阶段不新增任何功能 | ✅ G1/G2 已落地并接入 CI；冻结期现已结束（见下） |
| `0.9.x` | 冻结候选：G5 + G3 全键盘走查 + G6 | 只修 bug，不加功能 | ✅ G5/G6 已落地、G3 全键盘走查通过 |
| `1.0.0-rc.1` | 发布候选版：把门禁全绿的源码交到真实环境验收 | 只做发布动作，不夹带新功能 | ✅ **已发布（2026-09-22）**：`server_version` 提为 `1.0.0-rc.1`、`release-notes/v1.0.0-rc.1.md` 已写；tag `v1.0.0-rc.1` 推 GitHub 与 Gitea；GitHub 流水线 `Build & Release` **成功**（run 35683165934），Release 资产 `nginx-manager-v1.0.0-rc.1.exe`（12,324,307 字节，HTTP 200 可下载）；main 上的 `Tests` 门禁同时**成功**（38 个提交首次过 CI） |
| `1.0.0` | 正式版：三层目标全部达标 + rc 验收通过 | 验收通过后转正 | ⏳ 门槛已全部转绿（见第三节）；等 rc.1 的真实环境验收结果——通过后**只提升 `server_version` 并打同名 tag，代码不动** |

**关键约束：先把保障建起来，再谈正式版。** 0.8.0 必须是功能冻结版。

**功能冻结已于 2026-09-22 由用户指令解除一次（有边界）**：用户要求「日志做成实时滚动 + 可暂停，加完这个功能再谈发布」。因此本轮**在冻结期外**新增了一项功能（日志实时跟随 + 暂停），并同步补齐契约、双入口测试、真实 nginx e2e 用例与真实浏览器走查。**只此一项**：其余改动仍按「只修缺陷」处理（本轮顺带修掉一个真实缺陷：`/api/logs/access` 显式传回配置声明的日志路径被判 403）。冻结恢复后如需再增功能，仍须先取得用户明确指令并在此处登记。

**发布记录（2026-09-22，用户指令「发布一个版本 我要到真实环境测试」）**：以 **`v1.0.0-rc.1`** 作为发布候选版交到真实环境验收；正式版 `v1.0.0` 待验收通过后只改版本号转正。本次发布的动作与产物：

1. **先打通预发布号**：此前 `server_version` 只被 `([\d.]+)` 接受，直接打 `-rc.1` 会让版本门禁崩在 `int("0-rc")`，exe 名也带不上 rc。已改 `build.py` / `nginx-manager.spec` / `tests/test_api_contract.py` 三处提取正则接受 `-` 后缀；Windows 资源版本（`filevers`）取数字前缀（`1.0.0-rc.1 → 1.0.0.0`，资源版本没有预发布位），完整字符串仍写进 `FileVersion` / `ProductVersion`；tag 排序改为预发布感知（同号下正式版 > rc，rc.2 > rc.1），并新增 `test_prerelease_version_supported` 防回归。
2. **修 Gitea 构建流水线的产物路径漂移**：`.gitea/workflows/build.yml` 仍在上传 `dist/nginx-manager` 与 `dist/nginx-manager.exe`，而 spec 实际产出 `nginx-manager-v{版本}[.exe]`——Release 上传会因找不到文件失败。已按真实产物名修正上传路径与 Release 文件清单；该实例虽未配 runner（见第 5 条），修它是为了让两处配置保持一致。
3. **产物与文档**：`release-notes/v1.0.0-rc.1.md`（覆盖 v0.7.0 之后的全部改造，供真实环境按项验收）；README 中英功能列表同步「日志实时跟随」。
4. **本批次的代码改动**：日志实时跟随 + 可暂停（契约 `since/offset/reset/hasMore` → 后端只在完整行边界推进的增量读取 → 前端跟随/暂停/未读计数），并顺带修掉 `/api/logs/access` 把配置声明的日志路径显式传回却被判 403 的缺陷。
5. **通道与执行体限制（环境事实，非代码问题）**：GitHub 的 22 端口被透明代理劫持（解析到 `198.18.0.55`，SSH banner 超时），HTTPS 又无凭据，故推送改用 **`ssh.github.com:443`** 备用通道；Gitea（`origin`，`http://192.168.5.10:3020`）推送正常，但**该实例未配置 Actions runner（2026-09-22 用户确认）**——`.gitea/workflows/` 下的 build 与 tests 两条流水线都不会执行，CI 门禁与发布产物**只来自 GitHub Actions**，Gitea 仅作代码与 tag 镜像；两条 `.gitea` 工作流文件已加注说明，配置保持与 `.github` 镜像一致，将来配上 runner 即可生效。日后本机再发版，先确认这两条通道与执行体是否仍可用。

**验收通过后的转正动作**（保持代码不动）：`server_version` 由 `1.0.0-rc.1` 改为 `1.0.0` → 写 `release-notes/v1.0.0.md` → `release: v1.0.0` 提交 → 打 tag `v1.0.0` 推两个远端。


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

# 对比度：8 主题 × 24 对 = 192 项，WCAG AA 门禁（退出码即结论；--quiet 只看结论）
python3 scripts/contrast_audit.py
#   --strict 是兼容别名（2026-09-22 起与默认档同一标准），配色的邻接与阈值说明见脚本 docstring
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

### B2 认知维护状态与提交口径（2026-09-21 已闭环，2026-09-22 复核）

认知维护积压清零：`aoci.code.txt` **49 条 / 源码 49 个**（四批维护：G1/G2 测试与契约基建落地后＝新增 3 条 + 更新 9 条；发布前复查后＝新增 2 条（`LICENSE`、`scripts/e2e_real_nginx.py`）+ 更新 13 条；补版本派生断言后＝更新 `ROADMAP.md` + `tests/test_api_contract.py`；配色修 AA 与对比度门禁后＝更新 `frontend/css/style.css` / `scripts/contrast_audit.py` / `ROADMAP.md`，`tests/test_contrast.py` 落在观察集），`aoci check` 返回「✓ 可提交（Entries漂移/待策展/字典/格式/草稿五净）」，`aoci_maintain` 返回 `aligned`（`governance_aligned: true`、`code_drift` 全空）。测试类文件（`tests/*.py`）落在**观察集**而非索引集——`tests/test_api_contract.py` 的改动只前移基线、不产生新条目；新增受管文件后若要继续维护，先按机器下发的 next_command 跑 `aoci scope acknowledge --repo . --reviewed-by <agent>` 完成作用域复核前移。

**提交口径（rc12 实测）**：MCP 的 `aoci_update_entry` 批量 `entries` 入口在本机被客户端 Schema 校验拦下（该字段发布的 `oneOf` 分支互斥为空，任何对象都判 `not valid under any of the given schemas`），正式写入零发生。可用传输是同一管线的 CLI：

```bash
aoci update-entry --repo . --json --path <仓库相对路径> \
  --source-sha256 <aoci_maintain 返回的该候选 sha> --stdin < 条目文件
```

逐候选提交与维护批次等价：`--source-sha256` 就是候选绑定，机器校验（Impact/S 字段配额）与原子写入完全一致。

正式流程仍以 `AGENTS.md` 与机器签发的 Plan/Guide 为准：新会话先 `aoci_rules` → `aoci_overview`（跟随 `next_cursor` 直至 `completed=true`）→ 受管理对象稳定后 `aoci_maintain` → 依据机器签发候选创作完整 F/R/A/S → 提交；`remaining` 非零时重新 `aoci_maintain` 取下一批。**不得手写索引语义绕过。**

**条目硬约束**：F ≤160 字、R ≤360 字/8 项、A ≤400 字/6 项；S 的 rune 上限按 C 档位——C7-4 ≤200、C3-1 ≤50（超限会被 `fras_s_too_long` 拒绝，只改 S 重提）。条目须陈述「文件此刻是什么」，变更历史归 Git，写成演进叙事会被 Validator 提醒。

**重要**：Hermes 的 MCP 工具在会话启动时加载，接入后必须**开新会话**才会出现 `aoci_*` 工具；当前会话内无法直接调用。

### B3 上下文压缩后的重载与六个实测坑（2026-09-21 起记录）

- **压缩后必须重载认知**：宿主注入压缩摘要后，此前模型认知一律不作数。用 `aoci_overview` 携带 `refresh_reasons=["context_compaction"]` 与**新的** `refresh_event_id` 请求完整 Whole-Index（不要设 `check_only`），原样跟随 `next_cursor` 到 `completed=true`，再提交交付确认与 Attestation（本次 43/43 条、Challenge 10/10 通过后 `cognition_verified`）。摘要里不得携带正式 Header / Entry / Challenge 正文。
- **坑 1（会丢正文，本会话实际踩到）**：**不要**在跟随 `next_cursor` 的那次调用里附带 `host_delivery_confirmation`。服务端会据此认定「完整正文已交付」而直接进入 attestation 模式，**剩余分块正文不再下发**（表现为只返回元数据 + Challenge、`delivery_integrity: incomplete`）。正确顺序：先取完所有分块（最后一块含 `<<<AOCI_OVERVIEW_BODY_END/v1>>>` 结束标记），再单独一次调用同时提交 `host_delivery_confirmation`（`version: overview-delivery-receipt/v1`、`body_sha256`/`body_bytes` 取自分块回执、`end_marker_observed: true`）与 `model_cognition_attestation`。已误传时：用新的 `refresh_event_id` 重开一次整链（正文重发，不猜写、不用 `aoci_get_entries` 补缺块）。
- **坑 2（提交口径再次复现）**：MCP 的 `aoci_update_entry` 批量 `entries` 入口仍被客户端 Schema 拦下（发布的 `oneOf` 分支互斥为空），与会话无关；继续用上面的 CLI `--source-sha256` 逐候选提交。另：顶层 `max_entries` 是**返回字段**不是入参，传入会被 `additionalProperties: false` 拒绝。
- **坑 3（门禁不可放过偶发失败）**：测试套件曾间歇性失败（一次 4 失败、一次 2 失败），根因是替身脚本用 `case "$*" in *-v*)` 做子串匹配、被 `mkdtemp` 生成的含 `-v` 目录名命中，把 `nginx -t` 误判成版本查询。**测试基建的假失败会直接摧毁门禁可信度**：复现→定位→修根因→补一条用固定前缀 `-v` 稳定复现的回归用例，并连跑 40 轮确认零失败后才提交。
- **坑 4（离线复算颜色会算错）**：`frontend/css/style.css` 里兜底 `:root` 块写在「日」色阶块**之后**，若按文件顺序把同选择器声明逐个叠加，亮色主题会被暗色值覆盖（本次写 `scripts/contrast_audit.py` 时实际踩到，第一版把亮色 text-1 算成 1.10:1）。真实成因是层级而非顺序：`:root` 落在 `<html>`，对 `<body>` 只能是继承值，而 `[data-theme…]` 落在 `<body>`，自身声明恒胜。改为「两趟解析」后与真实浏览器 `getComputedStyle` 取色逐项核对一致（88 项全对齐）。
- **坑 5（对比度的「配对」不能想当然，两层都要按真实邻接取）**：判断某色是否达标，必须先确认它实际落在哪个表面上，而不是挑一个看起来合理的表面。本次修 AA 时两处都靠 `grep` 证据纠正：① 灰字四档与语义色正文的真实底座是**面板/画布**，但语义色正文主要落在自己的 `-soft` **芯片**上（`.badge-running` / `.test-result` / `.callout-*` / `.diff-view` 的 `dl-add`、`dl-hunk`），芯片比纯白面板更严（旧值 3.63–4.39 vs 面板 4.10）；② `--border-strong` 在页面上从不落在 `--bg-hover` 上，而是 `--bg-raised`（输入框/按钮/徽章）+ `--bg-sunken`（编辑器/日志/diff）+ `--bg-panel` + 画布四者，最严的一档是「浅色 `--bg-sunken` / 深色 `--bg-raised`」——只核对画布会漏判。**结论：新配色对进脚本前，先用 `grep -n` 把该 token 与背景 token 同现的规则列出来当证据。**
- **坑 6（变异测试别在工作区改文件再用 `git checkout` 还原）**：为验证新门禁真的会红，本次先把 `style.css` 的色值改回旧值跑测试（确实 2 处失败），随后用 `git checkout -- frontend/css/style.css` 复原——**连同尚未提交的配色改动一起被抹掉**（只保住了报告里的结论，色值靠记忆重打一遍）。稳妥做法有两种：把待测文件先 commit 再变异；或**在 `scratch/` 里复制一份改坏的文件**、用 `--css` 参数/替换模块常量跑审计（本次补做后采用后者）。
