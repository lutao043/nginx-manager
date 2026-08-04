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

保存配置文件。保存前自动备份原文件到 `backups/<时间戳>/`，然后写入新内容。

**请求体**

```json
{ "path": "nginx.conf", "content": "worker_processes  1;\n...", "runTest": true }
```

- `runTest`（可选，默认 true）：保存后是否执行 `nginx -t` 校验。

**成功响应 200**（校验通过或未请求校验）

```json
{
  "ok": true,
  "backupId": "20260804_193000",
  "test": { "ok": true, "output": "nginx: configuration file ... test is successful" }
}
```

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

列出所有备份。

**成功响应 200**

```json
{ "backups": [ { "id": "20260804_193000", "createdAt": "2026-08-04 19:30:00", "files": ["nginx.conf"] } ] }
```

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

返回当前设置（nginxPath、confDir）。

**成功响应 200**

```json
{ "nginxPath": "C:/nginx/nginx.exe", "confDir": "C:/nginx/conf", "configured": true }
```

- `configured`：nginxPath 与 confDir 是否均已配置。

### PUT /api/settings

更新设置并持久化。

**请求体**

```json
{ "nginxPath": "C:/nginx/nginx.exe", "confDir": "C:/nginx/conf" }
```

**成功响应 200**

```json
{ "ok": true, "nginxPath": "...", "confDir": "..." }
```

**错误**
- `400`：字段缺失。
- `409`：`nginxPath` 不存在或 `nginx -v` 无法执行。

## 前端行为约定

- 所有写操作（保存/启停/回滚）在弹确认框后进行；保存、回滚前前端提示「将自动备份原文件」。
- `409` 响应中若含 `saved: true`，前端必须展示「已保存未应用」警告条，并给出「回滚」入口。
- 状态面板每 10 秒轮询 `GET /api/status` 刷新运行状态与进程信息。
- 保存按钮旁显示最近一次校验结果（成功/失败 + 输出摘要）。
