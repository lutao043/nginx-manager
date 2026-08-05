# nginx-manager API 契约

> 本文档是**前后端契约的唯一权威源**。任何接口/字段变更：先改本文档 → 再改后端实现 → 再同步前端渲染。
> 服务仅监听 `127.0.0.1`，无登录鉴权（本机单用户场景）。

## 通用约定

- Base URL：`http://127.0.0.1:<port>`，端口由启动参数 `--port` 指定，缺省随机空闲端口。
- 请求/响应体均为 JSON（`Content-Type: application/json`），UTF-8。
- 时间字段：`yyyy-MM-dd HH:mm:ss`（本地时区），内部比较用 ISO 字符串。
- 错误响应统一：`{ "error": "<中文错误描述>", "detail": "<可选的补充信息>" }`，配合非 2xx 状态码。
- 状态码：`200` 成功；`400` 参数错误；`404` 资源不存在；`409` 操作冲突（如校验失败拒绝保存）；`500` 服务端错误。
- 前端所有用户可见文案用中文；本文档中的英文 key 为程序内唯一标识，不可翻译。

## 资源模型

### Settings（设置，持久化到用户数据目录 settings.json）

| 字段 | 类型 | 说明 |
|---|---|---|
| nginxPath | string | nginx 可执行文件绝对路径 |
| confDir | string | nginx 配置目录（含 nginx.conf 的目录） |
| port | int | 本次服务监听端口（运行时动态，不持久化） |

### FileNode（配置文件树节点）

| 字段 | 类型 | 说明 |
|---|---|---|
| path | string | 相对于配置目录的路径（如 `nginx.conf`、`conf.d/foo.conf`） |
| name | string | 文件名 |
| isDir | boolean | 是否目录 |
| children | FileNode[] | 子节点（仅 isDir=true 时有意义） |

### BackupInfo（备份记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 备份目录名（时间戳，如 `20260804_193000`） |
| createdAt | string | 创建时间（本地时区格式化） |
| files | string[] | 本次备份的文件相对路径列表 |

## 端点定义

### GET /api/status

返回 nginx 运行状态与配置概要。

**成功响应 200**

```json
{
  "running": true,
  "version": "nginx/1.24.0",
  "pid": 12345,
  "nginxPath": "C:/nginx/nginx.exe",
  "confDir": "C:/nginx/conf",
  "confPath": "C:/nginx/conf/nginx.conf",
  "confFileExists": true
}
```

- `running`：nginx 进程是否在运行（Windows 按进程名 + 路径匹配，类 Unix 按 pid 文件/进程匹配）。
- `version`：`nginx -v` 输出解析；不可用时为 null。
- `pid`：主进程 pid；不可用时为 null。
- `confFileExists`：`confDir/nginx.conf` 是否存在。

### GET /api/config

返回配置文件树（解析 `nginx.conf` 及其 include 指令得到的实际文件集合 + 目录结构）。

**成功响应 200**

```json
{
  "tree": [ { "path": "nginx.conf", "name": "nginx.conf", "isDir": false } ],
  "included": [ "conf.d/default.conf", "conf.d/ssl.conf" ]
}
```

- `tree`：根为配置目录下的文件/目录列表，仅包含**实际被引用的文件**（nginx.conf + include 到的 .conf），避免展示无关文件；目录节点展示为可展开，其 children 仅含被引用文件。
- `included`：所有被 include 的文件相对路径（用于快速定位）。

### GET /api/config/file?path=nginx.conf

读取单个配置文件内容。

**参数**
- `path`（必填）：相对配置目录的文件路径。

**成功响应 200**

```json
{ "path": "nginx.conf", "content": "worker_processes  1;\n..." }
```

**错误**
- `400`：path 缺失或非法（路径穿越 `..` 被拒）。
- `404`：文件不存在。
- `403`：解析后路径越出配置目录（防御）。

### PUT /api/config/file

保存配置文件。**默认不自动备份**；仅当 `doBackup=true` 时才备份原文件到 `backups/<时间戳>/`（由用户显式确认，避免每次保存都产生时间戳备份）。

**请求体**

```json
{ "path": "nginx.conf", "content": "worker_processes  1;\n...", "runTest": true, "doBackup": false }
```

- `runTest`（可选，默认 true）：保存后是否执行 `nginx -t` 校验。
- `doBackup`（可选，默认 false）：是否在写入前备份当前原文件。前端在用户确认「保存并备份」时传 true。

**成功响应 200**（校验通过或未请求校验）

```json
{
  "ok": true,
  "backupId": "20260804_193000",
  "backedUp": true,
  "test": { "ok": true, "output": "nginx: configuration file ... test is successful" }
}
```

- `backedUp`：本次是否执行了备份；未备份时为 false、`backupId` 为 null。

**错误**
- `400`：path 缺失或非法。
- `404`：目标文件不存在（本工具只允许编辑已存在的文件，防止乱建文件）。
- `409`：`runTest=true` 且 `nginx -t` 失败 → **文件已写入但未生效**，返回：

```json
{
  "error": "nginx -t 校验失败，配置已保存但未应用，请修正后重试或回滚",
  "detail": "nginx: [emerg] unknown directive \"xxx\" in ...",
  "saved": true,
  "backupId": "20260804_193000",
  "test": { "ok": false, "output": "..." }
}
```

### POST /api/config/test

执行 `nginx -t` 校验（不修改文件）。

**成功响应 200**

```json
{ "ok": true, "output": "nginx: configuration file ... test is successful" }
```

**错误**
- `500`：nginx 不可执行（settings 未配置或路径错误），`error` 说明原因。

### POST /api/nginx/start

启动 nginx。

**成功响应 200**

```json
{ "ok": true, "message": "nginx 已启动" }
```

**错误**
- `409`：nginx 已在运行，`error: "nginx 已在运行"`。
- `500`：启动失败（如端口占用、配置错误），`detail` 附命令输出。

### POST /api/nginx/stop

停止 nginx（优雅退出 `-s quit`，失败回退 `-s stop`）。

**成功响应 200**

```json
{ "ok": true, "message": "nginx 已停止" }
```

**错误**
- `409`：nginx 未在运行。

### POST /api/nginx/reload

重载配置（`-s reload`）。

**成功响应 200**

```json
{ "ok": true, "message": "nginx 配置已重载" }
```

**错误**
- `409`：nginx 未在运行。
- `500`：重载失败。

### POST /api/nginx/restart

重启（先 stop 后 start）。nginx 未在运行时的 restart 等价于 start。

**成功响应 200**

```json
{ "ok": true, "message": "nginx 已重启" }
```

### GET /api/backups

列出所有备份（按时间倒序，最新的在前）。

**成功响应 200**

```json
{ "backups": [ { "id": "20260804_193000", "createdAt": "2026-08-04 19:30:00", "files": ["nginx.conf"] } ], "retention": 7 }
```

- `retention`：当前自动保留份数（来自设置 `backupRetention`，默认 7；`0` 表示不自动清理）。

### DELETE /api/backups

手动删除指定备份。**请求体**

```json
{ "id": "20260804_193000" }
```

**成功响应 200**

```json
{ "ok": true, "deleted": "20260804_193000", "backups": [ ...剩余列表... ] }
```

**错误**
- `400`：id 缺失或非法。
- `404`：备份不存在。

### 自动清理（prune）

每次创建新备份后，自动删除超过 `backupRetention` 份的最旧备份目录。
`backupRetention <= 0` 表示不自动清理（仍可手动删）。保留数量在设置页调整。

### POST /api/backups/restore

回滚到指定备份。**先执行 `nginx -t` 校验再写入**；校验失败则拒绝回滚。

**请求体**

```json
{ "id": "20260804_193000" }
```

**成功响应 200**

```json
{ "ok": true, "restored": ["nginx.conf"] }
```

**错误**
- `400`：id 缺失或非法（路径穿越）。
- `404`：备份不存在。
- `409`：回滚后 `nginx -t` 校验失败（未写入）。

### GET /api/logs/error

返回错误日志尾部（默认最后 200 行）。

**参数**
- `lines`（可选，默认 200）。

**成功响应 200**

```json
{ "logPath": "C:/nginx/logs/error.log", "content": "2026/08/04 19:00:00 [error] ..." }
```

- `logPath`：自动定位（confDir 同级 logs/error.log）；文件不存在时 `content` 为空字符串。

### GET /api/settings

返回当前设置（nginxPath、confDir、backupRetention）。

**成功响应 200**

```json
{ "nginxPath": "C:/nginx/nginx.exe", "confDir": "C:/nginx/conf", "backupRetention": 7, "configured": true }
```

- `configured`：nginxPath 与 confDir 是否均已配置。
- `backupRetention`：自动保留备份份数（默认 7；0 表示不自动清理）。

### PUT /api/settings

更新设置并持久化。

**请求体**

```json
{ "nginxPath": "C:/nginx/nginx.exe", "confDir": "C:/nginx/conf", "backupRetention": 7 }
```

- `backupRetention`（可选）：整数，范围 1~100（`0` 表示不自动清理）。未传则保持不变。

**成功响应 200**

```json
{ "ok": true, "nginxPath": "...", "confDir": "..." }
```

**错误**
- `400`：字段缺失。
- `409`：`nginxPath` 不存在或 `nginx -v` 无法执行。

## 代理管理（proxies）

### ProxyInfo（代理条目模型）

| 字段 | 类型 | 说明 |
|---|---|---|
| path | string | location 路径前缀（如 `/xxxxWeb`） |
| active | string | 当前激活目标地址（proxy_pass 未注释行的值） |
| targets | string[] | 全部备选目标地址（激活 + 注释备选，按配置顺序） |
| proxyHeaders | boolean | 是否包含标准三行 proxy_set_header 样板 |

**配置文件表示约定**（唯一权威，nginx 原生语法兼容）：
- 一个代理 = 一个 `location <path> { ... }` 块，块内含 `proxy_pass <url>;`。
- 激活目标：块内**唯一未注释**的 `proxy_pass` 行。
- 备选目标：块内 `#proxy_pass <url>;` 注释行（切换 = 互换激活行与目标行的注释状态）。
- 兼容 `^~` / `~` / `~*` / `=` 等 location 修饰符（`path` 为去掉修饰符后的路径）。
- 兼容历史遗留的裸地址备选（如 `# proxy_pass 10.1.2.3:8080;` 无 `http://` 前缀）：
  可显示/可尝试切换，但 nginx 不认裸地址，`nginx -t` 会拦截并回滚（提示补 `http://`）。
- **不含激活 proxy_pass 的块**（如 `alias` 静态目录）不进入代理列表。
- 本工具只识别**包含 proxy_pass 指令**的 location 块；静态资源/其他 location 不进入代理列表。
- 切换/更新仅改变 proxy_pass 行的注释状态与缩进，**不重排块内其他指令**。

### GET /api/proxies

返回所有代理条目（按配置文件出现顺序）。

**成功响应 200**

```json
{
  "proxies": [
    { "path": "/nginx-manager/", "active": "http://127.0.0.1:8310/", "targets": ["http://127.0.0.1:8310/"], "proxyHeaders": true }
  ],
  "sourceFile": "nginx.conf"
}
```

- `sourceFile`：解析来源文件路径（相对配置目录）。

### POST /api/proxies

添加新代理。在配置文件末尾的 server 块内追加 `location` 块（标准样板：proxy_pass + 三行 proxy_set_header）。添加后执行 `nginx -t` 校验。

**请求体**

```json
{ "path": "/xxxxWeb", "target": "http://192.168.1.10:8080/" }
```

**成功响应 200**

```json
{ "ok": true, "proxy": { "path": "/xxxxWeb", "active": "http://192.168.1.10:8080/", "targets": ["http://192.168.1.10:8080/"], "proxyHeaders": true }, "test": { "ok": true, "output": "..." } }
```

**错误**
- `400`：path/target 缺失、path 非法（必须以 `/` 开头、不含空白）。
- `409`：path 已存在（重复代理）。
- `409`：添加后 `nginx -t` 校验失败（已回滚写入，配置未改动）。

### PUT /api/proxies/switch

切换某代理的激活目标。修改配置文件 + 自动备份 + `nginx -t` 校验，校验失败回滚。
**若 target 不在该代理的备选列表中（如在目标地址池中），自动将其追加为备选后切换**（免去逐代理添加）。

**请求体**

```json
{ "path": "/xxxxWeb", "target": "http://192.168.1.11:8080/" }
```

**成功响应 200**

```json
{ "ok": true, "proxy": { "path": "/xxxxWeb", "active": "http://192.168.1.11:8080/", "targets": ["http://192.168.1.10:8080/", "http://192.168.1.11:8080/"], "proxyHeaders": true }, "test": { "ok": true, "output": "..." } }
```

**错误**
- `400`：path/target 缺失。
- `404`：path 对应的代理不存在。
- `409`：target 非合法目标地址（且不在该代理备选、不在目标池中）。
- `409`：校验失败（已回滚）。

### PUT /api/proxies/targets

更新某代理的备选目标列表（增删备选，激活目标不变；若移除当前激活目标则自动切换到列表第一个）。修改后自动备份 + 校验。

**请求体**

```json
{ "path": "/xxxxWeb", "targets": ["http://192.168.1.10:8080/", "http://192.168.1.11:8080/", "http://192.168.1.12:8080/"] }
```

**成功响应 200**

```json
{ "ok": true, "proxy": { "path": "/xxxxWeb", "active": "...", "targets": ["..."], "proxyHeaders": true }, "test": { "ok": true, "output": "..." } }
```

**错误**
- `400`：path/targets 缺失、targets 为空或含非法 URL。
- `404`：path 对应的代理不存在。
- `409`：校验失败（已回滚）。

### DELETE /api/proxies

删除代理（移除整个 location 块）。修改后自动备份 + 校验，失败回滚。

**请求体**

```json
{ "path": "/xxxxWeb" }
```

**成功响应 200**

```json
{ "ok": true, "deleted": "/xxxxWeb", "test": { "ok": true, "output": "..." } }
```

**错误**
- `400`：path 缺失。
- `404`：path 对应的代理不存在。
- `409`：校验失败（已回滚）。

## 目标地址池（proxy pool）

统一管理常用目标地址，供所有代理的下拉切换复用，避免逐代理添加备选。

**持久化**：目标池独立存储于用户数据目录 `targets.json`（不写入 nginx 配置），
后端启动时加载，增删即持久化。兼容旧版纯字符串列表（自动迁移为带别名的结构）。

### PoolTarget（池条目模型）

| 字段 | 类型 | 说明 |
|---|---|---|
| target | string | 目标地址（nginx 合法 proxy_pass 参数，须 `http://`/`https://`/`unix:` 前缀 + 合法主机） |
| alias | string | 显示别名（可空，用于记住 IP/域名含义，如「生产集群」「测试环境」） |

**URL 校验规则**（`proxymgr._normalize_target`）：
- 必须 `http://` 或 `https://` 或 `unix:` 开头；`unix:` 后须为 `/` 开头的 socket 路径。
- host 部分：合法主机名（字母/数字/`.`/`-`/`_`）或 IPv4；可带 `:端口`（端口为 1-5 位数字）。
- 可带路径（`/` 开头，任意非空白字符）。
- 拒绝：纯裸 IP/域名（无协议）、含空白、含 `{}`、任意乱输的字符串。

**与代理的关系**：
- 前端渲染代理下拉框时，选项 = 目标池全部地址 ∪ 该代理已有备选地址（去重）；池地址带别名时显示 `别名 (地址)`。
- 通过 `PUT /api/proxies/switch` 切换池中地址时，后端自动将该地址追加为该代理的备选并激活。
- 删除池中地址**不影响**已写入各代理配置的备选（代理的备选独立存在于 nginx.conf）。

### GET /api/proxy-pool

返回目标地址池。

**成功响应 200**

```json
{ "targets": [ { "target": "http://docker_balance", "alias": "生产Docker集群" }, { "target": "http://10.170.103.65:10040/", "alias": "" } ] }
```

### POST /api/proxy-pool

添加目标地址（可带别名）。

**请求体**

```json
{ "target": "http://zhang_balance", "alias": "张哥环境" }
```

**成功响应 200**

```json
{ "ok": true, "targets": [ { "target": "http://zhang_balance", "alias": "张哥环境" } ] }
```

**错误**
- `400`：target 缺失或不符合 URL 校验规则（不能随便输入字符串）。
- `409`：target 已存在于池中（去重，按 target 去重）。

### PUT /api/proxy-pool

更新池条目的别名（或仅新增别名）。

**请求体**

```json
{ "target": "http://zhang_balance", "alias": "张哥测试环境" }
```

**成功响应 200**

```json
{ "ok": true, "targets": [ { "target": "http://zhang_balance", "alias": "张哥测试环境" } ] }
```

**错误**
- `400`：target 缺失。
- `404`：target 不在池中。

### DELETE /api/proxy-pool

删除目标地址。

**请求体**

```json
{ "target": "http://zhang_balance" }
```

**成功响应 200**

```json
{ "ok": true, "targets": [] }
```

**错误**
- `400`：target 缺失。
- `404`：target 不在池中。

## 前端行为约定

- 所有写操作（保存/启停/回滚）在弹确认框后进行；**保存提供「保存并备份 / 仅保存」选择**，备份仅在用户显式确认时执行（不再每次保存自动备份）。
- `409` 响应中若含 `saved: true`，前端必须展示「已保存未应用」警告条，并给出「回滚」入口。
- 状态面板每 10 秒轮询 `GET /api/status` 刷新运行状态与进程信息。
- 保存按钮旁显示最近一次校验结果（成功/失败 + 输出摘要）。
- 目标池地址带别名时，下拉与池列表均显示 `别名 (地址)`；无别名仅显示地址。
- **代理切换重载询问**：`PUT /api/proxies/switch` 成功后（`nginx -t` 校验通过），前端弹确认框
  「是否立即重载 nginx 配置？」（是/否）——点是调 `POST /api/nginx/reload`，点否仅保存配置不重载。
- **代理搜索过滤**：代理管理页顶部提供搜索框，按代理路径（`path`）、目标地址（`targets`）实时过滤列表；
  输入即过滤（不触发后端请求），空关键词恢复全量列表；提供「清除搜索」按钮一键清空关键词并恢复全量。
