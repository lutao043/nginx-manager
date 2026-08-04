# VIBE_CODING_GUIDE.md — nginx 管理端项目规范

本项目遵循 vibe-coding-conventions 方法论。本文档登记项目专属约定，与 `API.md`（契约唯一权威源）配套使用。

## 技术选型（已敲定，勿随意变更）

| 项 | 决策 | 原因 |
|---|---|---|
| 后端 | Python 标准库（http.server + subprocess） | 零第三方依赖，易打包，用户偏好 Python |
| 前端 | 原生 HTML/CSS/JS，零构建零框架 | 可移植、无供应链风险 |
| 主题 | 暗色护眼绿（CSS 变量） | 用户偏好 |
| 鉴权 | 无，仅绑定 127.0.0.1 | 用户决策：单机本机使用 |
| 端口 | `--port` 指定，缺省随机空闲端口 | 用户决策 |
| 打包 | PyInstaller 单文件 exe | 用户决策 |
| 首次配置 | 启动时系统对话框选 nginx 路径 + 配置目录 | 用户决策（不做自动探测） |

## 目录结构

```
├── frontend/            # 原生前端（index.html + css/ + js/）
├── backend/
│   ├── server.py        # HTTP 服务、路由、settings、备份
│   └── nginxctl.py      # nginx 控制（三端适配层）
├── tests/               # 测试源码（如后续补充）
├── data/                # 运行时数据（gitignore；实际在用户数据目录）
├── API.md               # 契约唯一权威源 ★
├── SECURITY_AUDIT.md
├── VIBE_CODING_GUIDE.md # 本文件
├── build.py             # 一键打包
└── nginx-manager.spec   # PyInstaller 配置
```

## 关键约定

### 契约纪律
- **任何接口/字段变更：先改 API.md → 再改 backend → 再同步 frontend**。
- 错误响应统一 `{error, detail?}` + 非 2xx 状态码；前端 `api.js` 统一抛错处理。
- `409 + saved:true` 是「已保存未应用」特殊语义，前端必须展示警告条 + 回滚入口（勿当普通错误）。

### 数据与安全
- 运行时数据（settings.json、backups/）**写入用户数据目录**（`server.py::user_data_dir`），禁止写程序目录（onefile 场景临时目录不可写）。
- 所有路径参数必须过 `_safe_rel()` + `_in_conf_dir()` 双校验（防路径穿越）。
- 命令执行一律列表参数 + `shell=False`（防注入）。
- 前端用户数据渲染必须 `textContent` 或 `escapeHtml()`（防 XSS）。
- 前端资源**禁止引入 CDN/远程库**，一律相对路径。

### 编码风格
- 变量/函数小驼峰，常量全大写下划线；注释用中文解释"为什么"。
- 日期展示用本地时区 `yyyy-MM-dd HH:mm:ss`；备份 id 用 `yyyyMMdd_HHMMSS`。
- UI 文案与注释术语统一（"校验配置 / nginx -t"）。

### 测试与验证
- 测试源码放 `tests/`，产物 gitignore。
- 本机验证以手动启动 `python backend/server.py` 为主；改动后跑 `py_compile` / `node --check` 语法校验。
- 大版本发布前询问用户是否需要跑测试，未获同意不跑。

### Git 提交纪律
- 改前基线提交 → 改后功能提交；commit message 用 `type(scope): 简述`。

## 启动 / 构建命令

```bash
# 开发启动（首次会弹选择窗口）
python backend/server.py --port 8080

# 跳过首次对话框（已配置或用 CLI 指定）
python backend/server.py --nginx-path C:/nginx/nginx.exe --conf-dir C:/nginx/conf

# 打包单文件 exe
python build.py          # 产物 dist/nginx-manager.exe
```
