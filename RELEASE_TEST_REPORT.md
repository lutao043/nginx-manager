# nginx-manager 1.0.0 发布测试报告

- **测试日期**：2026-09-23
- **被测对象**：工作树 = `HEAD fb81277`（= tag `v1.0.0-rc.2` 之后的 AOCI 维护提交）+ **未提交的性能优化批次**（10 改 3 新，见第二节）
- **被测版本号**：`server_version = nginx-manager/1.0.0-rc.2`（正式版 `1.0.0` 尚未提版）
- **测试角色**：测试负责人（自动化门禁复跑 + 独立复核 + 真实浏览器黑盒走查）
- **测试环境**：macOS（darwin 27.0.0 arm64）、Python 3.8.10（标准库入口）/ Python 3.13（pytest 入口，uv 0.12.9）、Node v26.9.0、真实 nginx `1.30.4`（工作区自带构建：`--without-http_rewrite_module --without-http_gzip_module --with-http_stub_status_module`）

---

## 一、结论摘要

**自动化门禁全部通过，独立复核未发现门禁漏网的功能性问题；实测发现 6 项缺陷（1 项本批次引入、2 项既有中危），随后全部修复并逐条复测通过。当前建议：完成第八节的转正动作即可发布 `v1.0.0`。**

本报告分两阶段，请连着看：**首次测试**（第一节至第六节，含 6 项缺陷的复现与证据）→ **缺陷处置与复测**（第七节，含每项的修法、新增回归与复测结果）。

首次测试已通过的部分（每一项都有实测输出，命令见附录 A）：

| 验证面 | 结果 |
|---|---|
| 后端双入口测试 | unittest **全通过**；pytest **全通过（含 66 subtests）**（例数见第七节：修复前 170，修复后 194） |
| 前端语法与契约门禁 | `node --check` 3 文件通过；`dom_contract.py` 通过；`contrast_audit.py` **192/192 项全过** |
| 真实 nginx 端到端走查 | **38 项全通过，退出码 0**（含最坏失败模式：保存坏配置→备份仍在→可回滚） |
| 独立复核探针（不复用项目测试代码） | **38 项全通过** |
| 性能基准 | 稳态派生扫描 **每次调用打开配置文件 0 次**；HTTP 单请求 CPU 105–1101 µs；keep-alive 连接复用生效 |
| 三个前端门禁的变异验证 | 改坏 DOM/布局/对比度均**如实失败（exit 1）**，门禁不是摆设 |
| 契约与版本一致性 | `API.md` 与实现端点集合双向零漂移；版本号不落后于最新 tag；发布说明与版本匹配 |

发现的缺陷及其处置状态（详见第六、七节）：

| 编号 | 严重级别 | 摘要 | 是否本批次引入 | 处置 |
|---|---|---|---|---|
| D1 | 中（P2） | macOS 上进程识别误判：nginx 实际在跑却显示「未运行」；点「启动」会**删掉活实例的 pid 文件**再起第二个实例 | 否（rc.2 已存在） | ✅ 已修并复测 |
| D2 | 中（P2） | 单行写法的 `stub_status` 不被识别 → 实时指标永久不可用，且提示「配置已写入」与事实不符 | 否（rc.2 已存在） | ✅ 已修并复测 |
| D3 | 中低（P2/P3） | 日志面板的空状态占位文本在日志增长后**不被清除**，与真实日志粘连 | **是（本批次引入的回归）** | ✅ 已修并复测 |
| D4 | 低（P3） | 用 `--nginx-path/--conf-dir` 启动时 Web 界面仍走「首次配置向导」，与 README「跳过首次选择对话框」不符 | 否 | ✅ 已修并复测 |
| D5 | 提示（P4） | 「开启统计」在已存在同名 location 时提示「配置已写入并通过校验」，实际未写入、也未产生确认框所承诺的备份 | 否 | ✅ 已修并复测 |
| D6 | 提示（P4） | `HEAD` 请求返回 501（不支持） | 否 | ✅ 已实现并复测 |

---

## 二、被测改动范围

工作树相对 `HEAD` 的改动即本批次（性能优化，用户第三、四次解除功能冻结后的改动）：

```
 .aoci/baseline.json       |  74 +++++-----      backend/nginxctl.py       | 243 +++++++++++-----
 .gitignore                |   3 +              backend/proxymgr.py       |  71 ++++++-
 ROADMAP.md                |  21 ++-             backend/server.py         | 102 ++++++++-
 aoci.code.txt             |  15 +-             frontend/js/app.js        | 283 ++++++++++++----
 tests/test_logs_follow.py |  19 +++             frontend/js/ui.js         |  27 +++
 新增：scripts/bench_hotpath.py、tests/test_config_cache.py、tests/test_http_layer.py
```

改动性质：消除重复读取/重复解析（配置内容身份缓存、代理解析去重）、前端 DOM 增量更新、HTTP/1.1 keep-alive 与静态条件请求。**不新增端点、不改响应字段**（`tests/test_api_contract.py` 顶层字段断言原样通过）。

---

## 三、门禁与自动化结果（首轮复跑实测）

> 本节记录的是**首轮测试**（缺陷修复前）的原始输出；修复后的数字见第七节复测总览（双入口由 170 增至 194 例）。

### 3.1 双入口测试

```
$ python3 -m unittest discover -s tests -t .        # Python 3.8.10，标准库入口
Ran 170 tests in 5.080s
OK

$ uv run --python 3.13 --with pytest python -m pytest tests/ -q
170 passed, 66 subtests passed in 4.07s
```

两条入口都真实启动服务子进程、走真实 HTTP，只把 nginx 换成替身脚本。**无一例失败、无一例跳过**（本次在 macOS 上运行，POSIX 替身用例全部生效）。

### 3.2 前端门禁

```
$ for f in frontend/js/*.js; do node --check "$f"; done
OK frontend/js/api.js / app.js / ui.js

$ python3 scripts/dom_contract.py
html:id 173 | html:class 116 | js:id 154 | js:class 57 | css:class 168 | css:var 50
契约检查通过                                      # 退出码 0

$ python3 scripts/contrast_audit.py --quiet
[WCAG AA] 共 192 项：通过 192，未达标 0，无法解析 0   # 退出码 0
```

### 3.3 门禁契约真实性（变异验证，副本上做，不动工作区）

| 变异 | 期望 | 实测 |
|---|---|---|
| 把 `index.html` 里被 JS 引用的 `btnVersion` 改成别的 id | 失败 | exit 1，报「JS 引用了不存在的 id #btnVersion」 |
| 删掉 `.editor-panel` 的 `overflow:hidden` 与 409 提示条的 `flex:none` | 失败 | exit 1，报「布局契约被破坏」 |
| 把浅色主题 `--text-3` 改淡到不达 AA | 失败 | exit 1，报 8 项未达标（emerald/ocean/amber/rose-light，1.47–1.58 : 1） |

三个门禁都真的会红，不是常量通过。

### 3.4 真实 nginx 端到端走查

```
$ NM_E2E_DIR=/tmp/nm-e2e-release-test python3 scripts/e2e_real_nginx.py
共 38 项，通过 38，失败 0        # 退出码 0（单独复跑确认过退出码）
```

覆盖：真实 nginx 启动/重载/优雅停止、`nginx -t` 真实报错行号解析、代理新增生效（透传 stub_status）、坏配置 `409 + saved:true`、备份与 diff、回滚后真实生效、stub_status 抓取、错误/访问日志读取、日志增量跟随 5 项（尾部快照/只取新增/偏移用尽不重复/半行不下发/补齐后整行取到）、路径穿越与越界拒绝、缺 `X-Requested-With` 拒绝、重启不叠加实例。

### 3.5 性能基准

```
$ python3 scripts/bench_hotpath.py
函数                          cold ms  cold opens  steady ms  steady opens
collect_included_files         12.136      r7/w0      0.029       r0/w0
find_error_log_paths            7.681      r7/w0      0.025       r0/w0
find_access_log_paths           6.171      r7/w0      0.022       r0/w0
find_stub_status                7.665      r7/w0      0.020       r0/w0
detect_listen_port              6.123      r7/w0      0.019       r0/w0
read_error_log_since(已到尾部)   6.401      r8/w0      0.041       r1/w0
代理切换序列 1.911 ms  r2/w1  parse_proxies 2 次
HTTP（200 次请求，单条 keep-alive 连接）：/api/status 105µs、/api/metrics 151µs、
  /api/logs/error 210µs、/api/logs/access 350µs、/api/proxies 1102µs，连接数 0
```

稳态 `opens = 0` 是关键结论：派生扫描命中缓存时**一次都不再打开配置文件**（`read_error_log_since` 的 r1 是读日志文件本身，属预期）。数字与 ROADMAP 回写值同量级（机器噪声内一致）。

### 3.6 ROADMAP 自查命令（原始口径）

```
shell= 命中                    0        （全仓 backend/，无 shell=True）
alert(/confirm(/prompt( 命中   0        （前端全部自绘弹窗）
aria-label HTML:39 JS:2 | aria-selected 6/3 | aria-live 2/1
aria-labelledby 9/0 | tabindex 6/5 | role= 28/0
前端 http(s) 引用              仅 data-URI SVG 与用户输入的示例串，无外部资源请求
```

---

## 四、独立复核（不复用项目自带测试代码）

为避免"用自己的测试证明自己"，另写了一份独立探针（`/tmp/nm_probe.py`，真实 nginx 二进制 + 真实服务进程 + 原始 HTTP 连接），**38 项全通过**；另做 CLI 级直调与裸 socket 探测。

关键复核结论：

1. **HTTP/1.1 连接复用确实生效**：同一 TCP 连接（文件描述符不变）连续请求正常；空闲 2s 后仍可用。
2. **请求体残留问题已真正修好**：带 200 KB 请求体、缺 `X-Requested-With` 的写请求返回 403 之后，同一连接上的下一个请求仍是正确的 200（不是 501/串位）。
3. **静态条件请求正确**：`ETag`/`Last-Modified` 齐备；`If-None-Match` 命中回 304 且无正文；`If-Modified-Since` 给未来时间回 304；**非法日期头不 500**；接口响应不因条件头改写语义。
4. **最坏失败模式（真机）**：保存语法错误的配置 → `409 + saved:true`、错误确已落盘、返回体带真实 nginx 错误输出；备份存在；`POST /api/backups/restore` 回滚成功、磁盘内容与原文逐字节一致、真实 `nginx -t` 通过。
5. **配置缓存对外可见的正确性**：外部（非管理端）新增被 include 的文件、外部修改 `error_log` 路径，下一次查询立即反映；改回后缓存随之回退（非单向累积）。
6. **边界安全**：配置路径穿越 400、日志路径越界 403、绝对路径读配置被拒；`lsof` 证实**只监听 127.0.0.1**；无 CORS 头。
7. **并发**：12 条并发连接 × 15 次轮询 = 180 次请求全部成功，无堆积假死。
8. **未路由方法**：`DELETE` 未知路径 → 404（JSON + Content-Length）；`PATCH`/`OPTIONS`/`HEAD` → 501 且带头 `Connection: close`；这些响应之后连接状态依然正确。
9. **契约与版本（离线复算）**：`API.md` 与实现端点集合双向无差异；`server_version` 与最新 tag `v1.0.0-rc.2` 一致；运行中服务上报的 `managerVersion` 与源码一致；`/api/changelog` 返回 16 个版本。
10. **HTTP 响应写出路径审计**：全仓仅 3 处响应写出点（`_send_json`、静态 200、静态 304），前两处显式带 `Content-Length`，全部路径都先做请求体排空——keep-alive 的前提成立。

---

## 五、真实浏览器黑盒走查（GUI）

**环境准备（与正式测试严格分开）**：为避免污染用户工作区，走查在隔离目录 `/tmp/nm-gui` 进行（真实 nginx 二进制 + 临时 conf/logs/html + 独立数据目录），并预置 `settings.json` 使界面进入主界面（原因见 D4）。**环境准备完成后才开始正式黑盒测试，测试期间未再用任何注入手段改变页面状态。**

| 测试点 | 结果 | 证据 |
|---|---|---|
| T1 主界面渲染 | 通过。顶栏品牌/版本 chip/主题/设置；状态栏「运行中 · 版本 1.30.4 · PID 53273 · 配置路径」；三页签 + 底部三停靠页签；无可见错误 | `t1_dashboard.png` |
| T2 版本号 → 更新历史弹窗 | 通过。弹窗 `aria-labelledby=changelogTitle`、`aria-modal=true`、**16 个版本折叠项**、当前版本带「当前版本」徽章并默认展开；点关闭按钮后弹窗关闭、**焦点归还到版本号 chip**、背景 inert 计数由 11 归 0 | 走查截图（会话临时） |
| T3 主题切换 | 通过。色板 8 套（4 配色 × 日夜）分组渲染；切到「海洋 · 日」后 `body[data-theme]=ocean-light`、`localStorage.nm-theme` 同步、CSS 变量实际生效（--bg #f5f7fa / --text-1 #212825），布局无破版 | `t3_theme_ocean_light.png` |
| T4 日志实时跟随 | 通过（主路径）。面板显示真实日志路径；文件新增 3 行后**无需刷新即出现**并贴底（scrollTop 4083 + 117 = scrollHeight 4200）；点暂停后 `aria-pressed=false`、按钮 title 变「继续实时跟随（跳到最新）」；暂停期间追加 50 行 → **scrollTop 保持 4083 不跳位**、提示「已暂停 · 新日志 50 行」；点继续 → 跳回最新、提示清除 | `t4_log_follow_bottom.png`、`t4_log_paused_nojump.png`、`t4_log_resumed_atbottom.png`、`t4_log_placeholder_bug.png` |
| T4 附属缺陷 | **失败（见 D3）**：面板首行显示「（错误日志为空或文件不存在）」粘在真实日志前面 | `t4_log_placeholder_bug.png` |
| T5 负载均衡页签 + 校验 | 通过。upstream 列表渲染正确（名称/上游地址/轮询徽章/编辑/删除/搜索）；点「nginx -t」后结果条（class `test-result ok`）显示真实 nginx 输出，toast 与 `#srStatus`（aria-live）同步播报「配置校验通过」 | 走查截图（会话临时） |
| T5b 写入确认链路 | 通过。点「开启统计」弹出确认框（说明文案 + 取消/确认，无原生弹窗）→ 写入并通过校验 → 再弹「是否立即重载」→ 确认后 toast 与读屏播报「nginx 配置已重载」 | 走查截图（会话临时） |

**本次未能执行的 GUI 项**（属运行环境能力限制，非页面失败）：

- **键盘可达性（Esc 关弹窗 / Tab 焦点陷阱）**：本次会话的内置浏览器不向页面投递键盘事件——实测 `document.hasFocus() === false`，连按 `Tab` 也不改变 `document.activeElement`（弹窗内确有 18 个可聚焦元素）。因此**没有独立验证 Esc 与焦点陷阱**；仅验证了「点关闭按钮 → 焦点归还触发元素 + inert 还原」，以及弹窗打开时背景元素确实被置 `inert`。ROADMAP 记录的纯键盘走查结论本次未被推翻，但也未被本次独立复测覆盖。
- **控制台错误收集**：该浏览器工具未提供控制台读取能力，未能收集 console 错误；以页面可见错误表现为替代（未观察到任何错误提示、空白或破版）。
- **配置编辑与保存的界面路径**：文本输入同样依赖键盘投递，无法在界面里改配置；该路径的等价能力已由"真机 e2e + 独立探针"在 API 层完整覆盖（改配置→备份→`nginx -t`→409→回滚）。

---

## 六、缺陷清单

> 本节按**发现时**的状态记录复现步骤与证据；每项的修法与复测结果见第七节。

### D1（中危 · P2）macOS 上 nginx 进程识别误判，且「启动」会删掉活实例的 pid 文件

- **位置**：`backend/nginxctl.py::_pid_is_nginx`（经 `_posix_nginx_pid` → `detect_process` → `is_running`）
- **是否本批次引入**：否，`HEAD`（rc.2）中同样存在
- **复现**（本次实测）：

```bash
# 用 -p 启动，使 nginx 命令行最后一个路径段不含 "nginx"
nginx -c /tmp/nm-pidtest/conf/nginx.conf -p /tmp/nm-pidtest

curl -s http://127.0.0.1:8310/api/status
# → {"running": false, "pid": null, "version": "1.30.4", ...}   而进程实际存活（ps 可见 master + worker）
```

- **根因**：macOS/BSD 的 `ps -p <pid> -o comm=` 返回的是**完整命令行（含参数）**，代码对整串取 `os.path.basename()`，等于取「最后一个 `/` 之后」的片段：

```
ps -p 50545 -o comm=  →  nginx: master process .../sbin/nginx -c .../nginx.conf -p /tmp/nm-pidtest
os.path.basename(...) →  nm-pidtest        # 不含 "nginx" → 判为「不是 nginx 进程」
```

是否命中取决于命令行最后一个路径段：`... -p /tmp/nm-gui` 判否；`-c .../nginx.conf`（以 nginx.conf 结尾）或 `-p .../nginx-1.30.4`（含 nginx）判是。**这也解释了为什么仓库自带的 e2e 一直没抓到**——它总是以工作区 `nginx-1.30.4` 前缀启动 nginx。Linux（`comm` 只给可执行名）与 Windows（走 `tasklist`）不受影响。

- **实测后果**（三条都验证过）：

1. 界面状态条显示「未运行」、`pid = null`，尽管 nginx 正在服务；
2. `stop()` / `reload()` 直接返回「nginx 未在运行」，用户无法通过界面停止或重载；
3. 点「启动」时先执行 `_clean_stale_pid_file()` → **实测活实例的 pid 文件被删除**（进程仍活着，但它从此失去 pid 文件），再尝试启动第二个实例 → 返回「nginx 启动失败：请检查端口占用或错误日志」。删掉 pid 文件后，nginx 自身的 `-s quit/-s reload` 也会失效，只能靠 `kill <pid>` 收拾。

- **建议修法**：不要对 `comm=` 整串取 basename。可移植的最小改法是对输出取**第一个 token**（去掉尾部冒号）判断是否等于 nginx 可执行名；或在 darwin 上改用 `ps -o ucomm=`。已在 macOS 实测：`ps -p <pid> -o ucomm=` 返回 `nginx`，而 `comm=` 返回完整命令行。修完请补一条 macOS 专用回归（用 `-p` 前缀启动 nginx 的形态）。

### D2（中危 · P2）单行写法的 `stub_status` 识别不到 → 实时指标永久不可用

- **位置**：`backend/nginxctl.py::_scan_stub_status`（逐行匹配 `^\s*stub_status\s*;`）与 `backend/proxymgr.py::enable_stub_status`（按 location 路径判「已存在」）判定口径不一致
- **是否本批次引入**：否，`HEAD` 中同样是逐行匹配
- **复现**（同一份配置的两种等价写法，实测）：

```
# 写法 A：单行
location /nginx_status { stub_status; allow 127.0.0.1; deny all; }
$ curl -s http://127.0.0.1:8310/api/metrics
{"available": false, "reason": "not_configured", "port": 53816}      # 直连该状态页实际返回 HTTP 200

# 写法 B：多行（管理端自己写入的形态）
location /nginx_status {
    stub_status;
    allow 127.0.0.1;
    deny all;
}
$ curl -s http://127.0.0.1:8310/api/metrics
{"available": true, "port": 53816, "stubPath": "/nginx_status",
 "metrics": {"active": 1, "accepts": 2, "handled": 2, "requests": 2, ...}}
```

- **影响**：手写紧凑写法的用户，界面「活跃连接 / 请求」永远是 `—`，实时指标功能区一直显示未开启，且点「开启统计」不会真正修好（因为 `enable_stub_status` 认出同名 location 已存在，直接返回 `unchanged`，见 D5）——形成"提示成功但功能仍不可用"的死循环。
- **建议修法**：让 `_scan_stub_status` 复用与 `enable_stub_status` 相同的 location 解析口径（同一行内出现 `stub_status;` 即认定该 location 是状态页），并补一条单行写法的回归；或至少在检测不到时把"配置里存在同名 location 但未识别为状态页"这一情形与"确实没有"区分开。

### D3（中低危 · P2/P3）日志面板空状态占位文本不清除 —— **本批次引入的回归**

- **位置**：`frontend/js/app.js`：`logRender()`（空内容时 `el.textContent = st.empty`）与 `logAppend()`（增量路径只 `appendChild` 新文本节点，不处理既有占位）
- **是否本批次引入**：**是**。`HEAD`（rc.2）的日志渲染每次都 `el.textContent = st.raw` 整体覆盖，占位会被新内容替换；本批次为减少重排改成"分块账本 + 仅追加文本节点"，空态占位不是 chunk，于是被留在最前面。
- **复现**：

```
1) 让日志文件为空或不存在（新建站点、日志刚轮转、或首次打开面板时无日志）
2) 打开「错误日志」停靠页签 → 面板显示「（错误日志为空或文件不存在）」
3) 向该文件追加任意一行
4) 面板变成：「（错误日志为空或文件不存在）2026/09/23 15:01:00 [error] 1#0: ...」
   —— 占位文案没有被移除，与真实日志首行粘连
```

- **实测证据**：`#errorLog` 的子节点为 `[文本节点"（错误日志为空或文件不存在）", 文本节点"2026/09/23 ..."]`，`textContent` 以占位开头；截图 `t4_log_placeholder_bug.png`。
- **影响**：面板对用户自相矛盾（一边说"为空或不存在"，一边显示日志）；复制日志时会带上一条伪首行；「访问日志」面板同一代码路径，存在同样问题。无数据风险。
- **建议修法**：追加前判断"当前展示的是空态占位"（例如 `st.chunks.length === 0 && el.firstChild` 且内容等于 `st.empty`）时改为覆盖写入，而不是追加。补一条「空文件 → 追加 → 不得残留占位」的回归。

### D4（低危 · P3）`--nginx-path/--conf-dir` 启动时 Web 界面仍走首次配置向导

- **位置**：`backend/server.py::main`（用 CLI 参数构造 controller，但**不写回 settings.json**）+ `_api_settings_get`（`configured` 只看 settings）+ `frontend/js/app.js::init`（`configured || preview` 决定是否进主界面）
- **是否本批次引入**：否（`HEAD` 中 `configured` 判定与 `already_configured` 逻辑相同）
- **实测**：以 `--nginx-path ... --conf-dir ... --data-dir <空目录>` 启动，`/api/status` 正确返回 `nginxPath/confDir/confPath/confFileExists: true`，但 `/api/settings` 返回 `nginxPath: null, confDir: null, configured: false` → 页面显示「先指定 nginx 的位置」向导，两个路径输入框为空，`POST` 之后才进入主界面。
- **影响**：README「启动参数」写明这两项的作用是「跳过首次选择对话框」，实际只跳过了后端的 tkinter 对话框，Web 端仍要求用户重新手填；自动化/脚本化启动（例如用 `--nginx-path` 拉起服务）也会得到这个向导，容易被误判为"服务没起来"。
- **建议**：要么把 CLI 传入的路径写入 settings（当作显式配置），要么让 `/api/settings` 的 `configured` 以「有效 controller 是否存在」为准；并同步 README 口径。

### D5（提示级 · P4）「开启统计」在已存在同名 location 时提示与实际不符

- **实测**：配置里已有 `/nginx_status` 的 location（写法 A）时点「开启统计」→ 确认框称「…自动备份并校验」→ 结果 toast 与读屏播报都是「状态页配置已写入并通过 nginx -t 校验」，但磁盘配置**无任何变化**、备份目录为空（`enable_stub_status` 返回 `unchanged`，`_proxy_apply` 对无变化跳过备份）。
- **影响**：提示语义与事实不符（未写入、未备份），叠加 D2 时会让用户以为已修好、实际指标仍不可用。
- **建议**：`unchanged` 分支单独给文案（如「状态页已存在，未做改动」），并让确认框的「自动备份」措辞与实际条件一致。

### D6（提示级 · P4）不支持 `HEAD` 等方法

- **实测**：`HEAD /api/status` → `501 Unsupported method ('HEAD')`，响应带 `Connection: close`、无正文残留；后续请求在同一连接上不受影响。`PATCH`/`OPTIONS` 同为 501。
- **影响**：本机单用户工具，浏览器不会对这些端点发 HEAD，**不构成发布阻塞**；若将来做探活/curl -I 脚本会觉得别扭。可留作后续优化。

---

## 七、缺陷处置与复测结果（2026-09-23，同日）

六项缺陷全部修复，每项都补充了回归用例；修改后**双入口 194 例、真机 e2e 38 项、独立探针 38 项、三门前端门禁全部复跑通过**（命令见附录 A）。

### D1 处置：按平台正确取进程名

- **改法**（`backend/nginxctl.py`）：新增模块级 `_ps_process_name()`，从 `ps -o comm=` 输出取**第一个 token**（去掉 macOS 的结尾冒号、再取末段），替换原来的 `os.path.basename(整串)`。一次改动同时覆盖「macOS 全命令行」「Linux 裸名」「带路径的 argv」三种形态。
- **回归**：新增 `PsProcessNameTest`（4 例：macOS 全命令行 / Linux 裸名 / 其它进程 / 空输出）与 `PidIsNginxTest`（4 例：`-p /tmp/nm-pidtest` 形态必须为真、Linux 形态为真、非 nginx 为假、ps 不可用时按未知处理）。
- **复测**：真实 nginx 以 `-c … -p /tmp/<不含 nginx 的目录>` 启动后，`/api/status` 返回 `running=true` 且 `pid` 与实际 master 一致，`POST /api/nginx/reload` 返回 200（修复前分别是 `running=false`、拒绝状态）。

### D2 处置：把 stub_status 判定从「按行」改为「按语句」

- **改法**（`backend/nginxctl.py`）：新增模块级 `_split_config_blocks()`，逐字符把配置行切成 `head / stmt / close / tail` 事件；`_scan_stub_status` 改为在事件流上维护块种类栈，`_scan_listen_port` 同样改为按语句识别（两者是同一类漏判）。块头与 `{` 分行（`location /x` 换行再 `{`）的情形一并支持——`tail` 事件作桥接，路径不再丢。
- **回归**：`StubStatusScanTest` 新增 4 例——单行 `location … { stub_status; … }` 必须被识别且端口取所属 server、整行 `http { server { … } }`、块头与 `{` 分行、单行嵌套 location 取内层路径；原有 4 例（多 server 端口配对、嵌套、引号内花括号、无状态页）全部保持通过。
- **复测**：同一份配置的两种等价写法都得到 `available: true`（单行写法此前是 `not_configured`，而状态页直连本来就返回 200）；浏览器里状态栏显示「活跃连接 1 · 请求 46（+0.1/s）」，且因为已识别到状态页，「开启统计」入口自动隐藏。
- **性能影响**：改后基准 `find_stub_status` 稳态 0.024ms / 打开 0 次、`detect_listen_port` 0.023ms / 0 次（改前 0.020/0.019），冷调用反而更快；稳态 opens 仍为 0。

### D3 处置：追加前清掉空态占位

- **改法**（`frontend/js/app.js`）：`logAppend()` 在 `appendChild` 之前，若面板当前显示的正是空态文案（`st.chunks` 为空且 `textContent === st.empty`）就先清空，再追加；错误日志与访问日志同一个代码路径。
- **回归**：前端没有 DOM 行为测试的运行环境，因此按仓库既有做法在 `tests/test_dom_contract.py` 增加 `LogPaneEmptyPlaceholderTest` 静态守卫（守卫存在、且在 `appendChild` 之前、两个面板的空态文案都在）。这是**守卫而非行为测试**，行为验证在下面的浏览器复测里；真正的 JS 行为测试需要引入运行环境，属后续改进项。
- **复测（真实浏览器）**：清空日志 → 打开「错误日志」页签看到占位 → 追加一行 → 面板只剩该行，`textContent` 中不再出现占位文案（子节点只有 1 个文本节点）。修复前该场景是「（错误日志为空或文件不存在）2026/09/23 …」粘连。

### D4 处置：`/api/settings` 以「当前生效」为准

- **改法**（`backend/server.py`）：`nginxPath` / `confDir` 改为「controller 优先、settings 兜底」，`configured` 改为「是否已建立 controller」；顺手删除因此变成死代码的 `SettingsStore.configured()`（它正是"只认 settings"这一错误判据的来源）。API.md 同步写明两个字段的新口径。
- **回归**：新增 `CliOnlyStartupTest`（3 例：只给 CLI 参数时 `configured` 为真且回填生效路径、`/api/status` 与 `/api/settings` 路径一致、配置树可用）；`ServerFixture.start()` 增加 `write_settings=False` 以模拟「settings.json 尚不存在」的首次运行。
- **复测（真实浏览器 + 无 settings.json 启动）**：界面**直接进主界面**（`dashboardVisible=true`、`wizardVisible=false`），状态栏显示「运行中 · PID 89661 · 配置文件路径」，不再出现要求重填路径的向导。

### D5 处置：写入端字段与契约对齐

- **改法**：`backend/proxymgr.py::enable_stub_status` 的返回值由自造字段 `unchanged` 改为契约里已有的 `already`（该字段原本没有任何消费方，`_proxy_apply` 把它原样透传、前端却在读 `res.already`，于是「什么都没做」被提示成「已写入并通过校验」）。前端提示改为按事实陈述：「`/nginx_status` 已存在，未做改动」；确认框文案同步写明「有实际改动时才自动备份并校验，已存在同名 location 则不做改动」。API.md 把 `already` 的两种情形（已有 stub_status / 已有同名 location）与「不产生备份」写清楚。
- **回归**：`test_proxymgr_unit.py` 新增 2 例——已存在同名 location 时必须返回 `already=True` 且配置逐字节不变、没有同名 location 时才写入且不带 `already`。
- **复测**：构造「有 `/nginx_status` location 但没有 stub_status」的配置，调用 `POST /api/metrics/enable` 返回 `200 + already:true`，配置内容未变、备份目录为空（修复前返回的响应里没有 `already`，前端因此提示「已写入」）。

### D6 处置：支持 HEAD

- **改法**（`backend/server.py`）：新增 `do_HEAD()`，复用 GET 路由并把 `_head_only` 标记（每个请求复位）传给两个正文写出点，头部与 GET 完全一致、正文跳过；API.md 的通用约定里写明 HEAD 与 GET 同路由。
- **回归**：`tests/test_http_layer.py` 新增 4 例——`HEAD /api/status` 返回 200/无正文/带 Content-Length 且头部与 GET 一致、HEAD 静态资源、HEAD 的 404 之后连接仍可复用、HEAD 条件请求 304。
- **复测**：`HEAD /api/status` → 200、正文 0 字节、`Content-Length: 394`，同一连接后续 GET 正常；`HEAD /` → 200、无正文。

### 复测总览

| 项 | 修复前 | 修复后 |
|---|---|---|
| 双入口测试 | 170 例 | **194 例全通过**（+24 条回归） |
| 真机 e2e | 38/38 | **38/38**（无回归） |
| 独立探针 | 38/38 | **38/38**（无回归） |
| 前端门禁 | 3 项通过 | 3 项通过（`dom_contract` 另含日志占位静态守卫） |
| 性能基准 | 稳态 opens 全 0 | 稳态 opens 全 0（`find_stub_status` 0.020→0.024ms） |
| 缺陷场景实测 | 6 项复现 | **7/7 修复验证通过**（`/tmp/nm_fixcheck.py`） |

---

## 八、未能验证的项与残留风险

1. **Windows 实机**：本次在 macOS 完成，Windows 相关分支（`tasklist` 进程识别、exe 资源版本、单文件 exe 行为、tkinter 选择框）无法覆盖；CI 的测试任务跑 ubuntu，替身相关用例在 Windows 会自动跳过。这是 ROADMAP 已登记的剩余风险 ②，本次未改变其状态。
2. **exe 打包链路**：`build.py` / `nginx-manager.spec` 只能在 Windows 上产出 exe（本机 Python 3.8 亦低于 build.py 要求），本次只做了**版本派生一致性**的离线复算（`build.py` 与 spec 的正则都能从 `server.py` 提取出 `1.0.0-rc.2`，产物名派生正确），未实际打包。
3. **本批次尚未经过 CI**：性能优化批次仍在工作树中（未提交），因此 GitHub Actions 未在"将要打 tag 的那个提交"上跑过门禁。本地双入口 + 三门前端门禁 + 真机 e2e 全绿是强证据，但不能替代 CI 结论。
4. **浏览器键盘行为**：见第五节，Esc/焦点陷阱本次无法独立验证。
5. **浏览器控制台**：工具不提供读取能力，未收集 console 错误。
6. **长期稳定性**：keep-alive + 30s 超时、空闲轮询成本已由基准与并发用例覆盖（12×15 请求、稳态 opens=0），但未做长时间（小时级）浸泡测试。
7. **测试副作用说明**：运行真机 e2e / 探针时，nginx 在配置解析早期失败的错误会写进工作区 `nginx-1.30.4/logs/error.log`（nginx 默认错误日志路径，位于 gitignore 的 vendored 目录内）。本次已确认它不影响仓库内容（`git status` 干净），但请知悉这类走查会在该文件追加行。
8. **前端行为测试缺口**：D3 这类「DOM 状态机」缺陷目前只能靠静态守卫 + 人工浏览器复测，CI 拦不住同类回归。建议后续给日志面板的缓冲/渲染逻辑补一个可断言的运行环境（或在 `scripts/` 下加一个纯 Node 的最小 DOM 替身）。

---

## 九、发布前必做清单（转正动作）

1. **提交本批次**：工作树现有改动（性能批次 + 本次缺陷修复 + 新增回归 + AOCI 资产 + 本报告）。新增测试文件必须入库，否则缓存/HTTP 层/本次六项修复在 CI 里都没有守卫。
2. **跑一次完整门禁并确认 CI 绿**：双入口测试（194 例）、`node --check`、`dom_contract.py`、`contrast_audit.py`、真机 `e2e_real_nginx.py`（发布前手动项），以及 GitHub Actions `Tests` 在**将要打 tag 的提交**上成功（本机无 `gh`，需在有凭据的环境核对）。
3. **转正动作**（ROADMAP 已写、此处逐条对齐）：`server_version` → `1.0.0`；新增 `release-notes/v1.0.0.md`（须覆盖 rc.1 之后的两批改动 + 本批次性能优化 + 本次六项缺陷修复）；README 中英功能列表同步；`release: v1.0.0` 提交；打 tag `v1.0.0` 推两个远端；确认 `Build & Release` 成功、Release 资产 `nginx-manager-v1.0.0.exe` 可下载。
4. **发布说明口径**：六项缺陷已修，可按「修了什么」的正常口径写；Windows 实机与键盘走查仍属未验证项（见第八节），说明里不要写成"全平台已验证"。

## 十、回归用例覆盖对照（本次已补）

| 用例 | 位置 | 守住的缺陷 |
|---|---|---|
| `PsProcessNameTest` ×4、`PidIsNginxTest` ×4 | `tests/test_nginxctl_unit.py` | D1 |
| `StubStatusScanTest` 新增 ×4 | `tests/test_nginxctl_unit.py` | D2 |
| `LogPaneEmptyPlaceholderTest` ×3（静态守卫） | `tests/test_dom_contract.py` | D3 |
| `CliOnlyStartupTest` ×3 | `tests/test_server_api.py` | D4 |
| `enable_stub_status` already/写入 ×2 | `tests/test_proxymgr_unit.py` | D5 |
| `HEAD` ×4 | `tests/test_http_layer.py` | D6 |

---

## 附录 A：可复现命令

```bash
# 双入口测试
python3 -m unittest discover -s tests -t .
uv run --python 3.13 --with pytest python -m pytest tests/ -q

# 前端门禁
for f in frontend/js/*.js; do node --check "$f"; done
python3 scripts/dom_contract.py
python3 scripts/contrast_audit.py --quiet

# 真机走查（需工作区 nginx-1.30.4/，退出码即结论）
NM_E2E_DIR=/tmp/nm-e2e python3 scripts/e2e_real_nginx.py

# 性能基准
python3 scripts/bench_hotpath.py            # 人类可读表
python3 scripts/bench_hotpath.py --json     # 机器可读

# 本次独立复核探针（会话临时文件，不入库）
python3 /tmp/nm_probe.py

# 修复验证探针（D1–D6 逐条复现场景、断言修复后的行为；真实 nginx + 真实服务进程）
python3 /tmp/nm_fixcheck.py

# 门禁变异验证（在副本上做，勿动工作区）
python3 scripts/dom_contract.py --html /tmp/nm-mut/frontend/index.html \
  --css /tmp/nm-mut/frontend/css/style.css --js "/tmp/nm-mut/frontend/js/*.js"
python3 scripts/contrast_audit.py --css /tmp/nm-mut3/css/style.css
```

## 附录 B：证据文件

- GUI 走查截图（本机临时目录，未入库，会话结束后可能被系统清理）：
  - `/tmp/nm-gui/screenshots/t1_dashboard.png` —— 主界面与状态栏
  - `/tmp/nm-gui/screenshots/t3_theme_ocean_light.png` —— 切换到「海洋 · 日」后的整页
  - `/tmp/nm-gui/screenshots/t4_log_follow_bottom.png` —— 跟随中贴底（205 行）
  - `/tmp/nm-gui/screenshots/t4_log_paused_nojump.png` —— 暂停期间不跳位 + 「已暂停 · 新日志 50 行」
  - `/tmp/nm-gui/screenshots/t4_log_resumed_atbottom.png` —— 继续后跳回最新
  - `/tmp/nm-gui/screenshots/t4_log_placeholder_bug.png` —— **D3 修复前**：占位文案粘在真实日志前
  - `/tmp/nm-gui/screenshots/t3_d3_fixed_after.png` —— **D3 修复后**：面板只剩日志行；同一张图里状态栏显示「运行中 · PID 89661 · 活跃连接 1 · 请求 46」，可一并作为 D1/D2 的界面证据
- 独立探针脚本：`/tmp/nm_probe.py`（38 项，退出码即结论）；修复验证脚本：`/tmp/nm_fixcheck.py`（7 项）
- 被隔离的测试环境：`/tmp/nm-gui`、`/tmp/nm-gui2`（修复后复测）、`/tmp/nm-pidtest`（D1 复现）、`/tmp/nm-e2e-*`（e2e 隔离副本）

---

**报告结论**：本批次的工程质量与自动化门禁证明了「可回归、可验证」这一层已经到位——修复后 194 例双入口测试、三门前端门禁（且经变异验证确实会红）、38 项真机走查、38 项独立探针、192 项对比度、契约零漂移、性能目标达成且缓存正确性经外部改动验证。首次测试发现的 6 项缺陷（含 1 项本批次引入的界面回归、2 项既有中危缺陷）已在同日全部修复、补齐回归用例并在真实浏览器/真实 nginx 上复测通过。**剩余差距只有两件与环境相关的事：本批次尚未提交（CI 未在将打 tag 的提交上跑过门禁）、Windows 实机与浏览器键盘走查仍属未验证项。** 按第九节执行转正动作即可发布 `v1.0.0`。
