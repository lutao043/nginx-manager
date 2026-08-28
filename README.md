<div align="center">

# nginx-manager

轻量级 nginx 网页管理端 · 后端零第三方依赖 · Windows 单文件 exe 开箱即用

简体中文 ｜ [English](README.en.md)

</div>

---

## 功能特性

- **配置文件管理**：树形浏览 + 在线编辑；保存前自动备份原文件并执行 `nginx -t` 校验，校验失败一键回滚
- **反向代理管理**：可视化切换 `proxy_pass` 目标（多备选、别名、按路径/地址搜索），改动直接写入 nginx.conf 并自动校验生效
- **目标地址池**：全部 proxy_pass 目标统一管理；等价写法（大小写/默认端口/末尾斜杠）自动去重
- **系统选择框**：首次启动弹系统对话框选择 nginx；界面所有路径输入均有「浏览…」按钮，免手输免出错
- **manager 数据目录可迁移**：配置文件 settings.json 与备份的存放位置可在界面修改，自动迁移并重启
- **预览模式**：未安装 nginx 也可先浏览界面、调试接口；配置好后自动进入正常模式
- **8 套主题**：4 配色 × 日夜双模式；前端零外部资源，内网/离线环境完全可用
- **安全保障**：仅监听 `127.0.0.1`、写操作 CSRF 防护、路径穿越校验、命令 `shell=False` 防注入

## 快速开始

### Windows（推荐）

1. 从 [Releases](../../releases) 下载 `nginx-manager-vX.X.X.exe`
2. 双击运行，浏览器自动打开 `http://127.0.0.1:8310`
3. 首次启动弹系统选择框，依次选择 nginx 可执行文件（如 `C:\nginx\nginx.exe`）与配置目录（含 `nginx.conf`）即可开始管理

### 源码运行

要求：Python 3.10+（首次选择框与「浏览…」按钮依赖标准库 tkinter）

```bash
git clone <本仓库>
cd nginx-manager
python backend/server.py             # 默认端口 8310，被占用时自动换随机端口
```

### 启动参数

| 参数 | 说明 |
|---|---|
| `--port` | 监听端口，缺省 `8310` |
| `--nginx-path` | nginx 可执行文件路径（跳过首次选择对话框） |
| `--conf-dir` | nginx 配置目录（含 nginx.conf，跳过首次选择对话框） |
| `--preview` | 预览模式：不要求 nginx 已安装/配置 |
| `--data-dir` | 指定 manager 数据目录（优先级高于界面修改，界面将锁定不可改） |

## 数据目录

运行时数据（settings.json、backups/、单实例锁）默认存放于：

| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\nginx-manager` |
| macOS | `~/Library/Application Support/nginx-manager` |
| Linux | `~/.config/nginx-manager` |

可通过 `--data-dir` 参数或环境变量 `NGINX_MANAGER_DATA_DIR` 指定；也可在「设置」界面修改（自动迁移设置与备份到新目录后重启）。

## 经 nginx 反向代理访问

服务仅监听 `127.0.0.1:8310`，可经 nginx 反代以 `/nginx-manager/` 前缀对外访问（前端自动适配路径前缀）：

```nginx
location /nginx-manager/ {
    proxy_pass http://127.0.0.1:8310/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## 从源码打包（Windows exe）

```bash
python build.py        # 产物 dist/nginx-manager-v{版本}.exe（单文件）
```

要求：打包用 Python 必须自带 tcl/tk（缺失时 build.py 直接报错退出），否则 exe 无法弹系统选择框。

## 安全说明

服务无登录鉴权（单机本机使用场景），**请勿将端口暴露到公网**。安全设计详见 [SECURITY_AUDIT.md](SECURITY_AUDIT.md)。

## 文档

- [API.md](API.md) — 前后端契约（接口唯一权威源）
- [VIBE_CODING_GUIDE.md](VIBE_CODING_GUIDE.md) — 项目规范与开发约定
- [SECURITY_AUDIT.md](SECURITY_AUDIT.md) — 安全审计
