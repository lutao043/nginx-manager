#!/usr/bin/env python3
"""热点路径基准：量化「空闲轮询」与「代理操作」的真实成本（纯标准库，无第三方依赖）。

用法（在仓库根执行）::

    python3 scripts/bench_hotpath.py            # 人类可读表
    python3 scripts/bench_hotpath.py --json     # 机器可读（回写 ROADMAP 用）
    python3 scripts/bench_hotpath.py --repeat 400

指标口径
--------
``cold``     全新状态下的首次调用（缓存未建立）的行数与打开次数。
``steady``   同一状态重复调用：耗时取中位数，打开次数取「每次调用平均」。
``opens``    一次调用实际触发的**配置文件/日志文件打开次数**（分读/写）。只统计
             nginx prefix 之下的路径，故 ``os.stat`` / ``os.scandir`` 不计——这正是
             「每次轮询要不要把所有配置文件重读一遍」的直接度量。
``parses``   一次代理切换调用序列里 ``parse_proxies`` 的执行次数。
``µs/req``   单请求进程 CPU 时间（``time.process_time``：不含子进程与客户端等待）。
``conns``    服务端为 N 次请求接受的 TCP 连接数——keep-alive 是否生效的直接证据。

HTTP 段以进程内真实 ``Server``/``Handler`` 跑真实 socket 请求（真实路由 + 真实控制器），
只跳过 ``main()`` 的启动装配（前端持久化、单实例锁、打开浏览器），因此 CPU 归因比
子进程 ``getrusage`` 干净。数据目录经 ``NGINX_MANAGER_DATA_DIR`` 指向临时目录，
不触碰用户真实数据目录（也不创建仓库内文件）。nginx 用 POSIX 替身脚本：与 ``tests/``
同思路，按**精确 token** 判定参数，不用子串匹配——子串匹配会被含 ``-v`` 的临时目录名
误命中（该坑已记录在 ROADMAP）。
"""
import argparse
import builtins
import contextlib
import http.client
import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
POSIX = os.name != "nt"

# 计数口径：只统计该根之下的打开（由 build_fixture 填充）
_NGINX_ROOT = [""]

# 替身脚本：只认精确 token（`-v` 与 `-V` 严格区分）
NGINX_STUB = """#!/bin/sh
for a in "$@"; do
  case "$a" in
    -V)
      echo "nginx version: nginx/1.30.4-bench" >&2
      echo "configure arguments: --prefix={prefix} " >&2
      exit 0 ;;
    -v)
      echo "nginx version: nginx/1.30.4-bench" >&2
      exit 0 ;;
    -t)
      echo "nginx: the configuration file {conf} syntax is ok" >&2
      echo "nginx: configuration file {conf} test is successful" >&2
      exit 0 ;;
  esac
done
exit 0
"""


# ---------- 计数与计时 ----------

@contextlib.contextmanager
def count_opens():
    """统计 nginx 根目录之下的文件打开次数（分读/写）。

    客户端线程在请求期间只等待、服务端请求串行发出，故计数无需加锁。
    """
    counts = {"read": 0, "write": 0}
    real_open = builtins.open
    root = _NGINX_ROOT[0]

    def tracking_open(file, *args, **kwargs):
        try:
            name = os.fspath(file)
        except TypeError:
            name = None
        if isinstance(name, str) and root and os.path.abspath(name).startswith(root):
            mode = kwargs.get("mode") or (args[0] if args else "r")
            if any(ch in str(mode) for ch in "wxa"):
                counts["write"] += 1
            else:
                counts["read"] += 1
        return real_open(file, *args, **kwargs)

    builtins.open = tracking_open
    try:
        yield counts
    finally:
        builtins.open = real_open


def _per_call(opens, n):
    return {"read": opens["read"] / n, "write": opens["write"] / n}


def cold_call(fn):
    """冷调用：全新状态下的首次调用（缓存未建立），返回 {ms, opens}。"""
    with count_opens() as counts:
        t0 = time.perf_counter()
        fn()
        ms = (time.perf_counter() - t0) * 1000.0
    return {"ms": ms, "opens": dict(counts)}


def steady_call(fn, repeat):
    """稳态重复调用：耗时取中位数，打开次数取每次调用平均值。"""
    samples = []
    with count_opens() as counts:
        for _ in range(repeat):
            t0 = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - t0) * 1000.0)
    return {"ms": statistics.median(samples), "opens": _per_call(counts, repeat)}


# ---------- 配置 fixture ----------

def build_fixture(root, stub_port, conf_files=6, sites=4, locations=24, pool=12):
    """按真实布局造一份 nginx 配置。

    代理 location 落在**主配置**（``ProxyManager`` 只管理 ``ctl.main_conf_path()``，
    server.py::_proxy_manager 锁定 nginx.conf），include 文件承载 upstream 与静态站点
    —— 前者喂代理/池基准，后者喂「扫描全部 include」的轮询基准。
    """
    prefix = os.path.join(root, "ngx")
    conf_dir = os.path.join(prefix, "conf")
    conf_d = os.path.join(conf_dir, "conf.d")
    sites_d = os.path.join(conf_d, "sites")
    logs = os.path.join(prefix, "logs")
    bindir = os.path.join(prefix, "bin")
    for d in (conf_d, sites_d, logs, bindir):
        os.makedirs(d, exist_ok=True)

    stub = os.path.join(bindir, "nginx")
    with open(stub, "w", encoding="utf-8") as f:
        f.write(NGINX_STUB.format(prefix=prefix, conf=os.path.join(conf_dir, "nginx.conf")))
    os.chmod(stub, 0o755)

    def location_lines(i):
        """每个 location：1 条激活 proxy_pass + 2 条注释备选（真实使用方式）。"""
        return ["        location /svc%02d/ {" % i,
                "            proxy_pass http://127.0.0.1:%d/;" % (9000 + (i % pool)),
                "            #proxy_pass http://127.0.0.1:%d/;  # 备选一" % (9100 + (i % pool)),
                "            #proxy_pass http://backend_pool/;  # 备选二",
                "        }"]

    # 主配置：代理 location（工具管理的目标）+ 一个 upstream + stub_status
    main = [
        "worker_processes  1;",
        "error_log  logs/error.log;",
        "pid        logs/nginx.pid;",
        "events { worker_connections 1024; }",
        "http {",
        "    access_log logs/access.log;",
        "    include conf.d/*.conf;",
        "",
        "    upstream backend_pool {",
        "        server 127.0.0.1:9500 weight=1;",
        "        server 127.0.0.1:9501 weight=1 backup;",
        "        keepalive 32;",
        "    }",
        "",
        "    server {",
        "        listen %d;" % stub_port,
        "        server_name metrics.local;",
        "        location /nginx_status { stub_status; }",
    ]
    per_server = max(1, locations // 2)
    idx = 0
    for _ in range(per_server):
        main += location_lines(idx)
        idx += 1
    main += ["    }", "", "    server {", "        listen 8080;", "        server_name app.local;"]
    while idx < locations:
        main += location_lines(idx)
        idx += 1
    main += ["    }", "}", ""]
    main_conf = "\n".join(main)
    with open(os.path.join(conf_dir, "nginx.conf"), "w", encoding="utf-8") as f:
        f.write(main_conf)

    # include 文件：upstream + 静态站点（轮询路径要 BFS 扫过它们）
    for n in range(conf_files):
        body = ["# conf.d/app-%02d.conf（由 nginx.conf 的 include conf.d/*.conf 引入）" % n]
        if n == 0:
            # 嵌套 include：conf.d 再引 sites/，覆盖多层 BFS 与二次 glob
            body.append("include sites/*.conf;")
            body.append("")
        body += ["upstream backend_%02d {" % n,
                 "    server 127.0.0.1:%d weight=1;" % (9200 + n),
                 "    server 127.0.0.1:%d weight=1 backup;" % (9300 + n),
                 "    keepalive 32;",
                 "}", "",
                 "server {",
                 "    listen %d;" % (8080 + n),
                 "    server_name app%02d.local;" % n,
                 "    location /static/ { root /var/www/app%02d; }" % n,
                 "}", ""]
        with open(os.path.join(conf_d, "app-%02d.conf" % n), "w", encoding="utf-8") as f:
            f.write("\n".join(body))

    for s in range(sites):
        with open(os.path.join(sites_d, "site-%02d.conf" % s), "w", encoding="utf-8") as f:
            f.write("server {\n    listen %d;\n    server_name site%d.local;\n"
                    "    location /static/ { root /var/www/site%d; }\n}\n" % (8090 + s, s, s))

    with open(os.path.join(logs, "error.log"), "w", encoding="utf-8") as f:
        for i in range(400):
            f.write('[error] %d#0: *%d open() "/srv/a%d.txt" failed (2: No such file)\n' % (i, i, i))
    with open(os.path.join(logs, "access.log"), "w", encoding="utf-8") as f:
        for i in range(2000):
            f.write('127.0.0.1 - - [23/Sep/2026:10:00:%02d +0800] "GET /svc%02d/ HTTP/1.1" 200 512 "-" "curl/8"\n'
                    % (i % 60, i % locations))

    _NGINX_ROOT[0] = os.path.abspath(prefix)
    info = {"prefix": prefix, "conf_dir": conf_dir, "nginx": stub,
            "main_conf": os.path.join(conf_dir, "nginx.conf")}
    info["scale"] = {"include_files": conf_files + sites + 1, "locations": idx, "pool": pool}
    return info


# ---------- stub_status 辅助服务 ----------

def start_stub_status_server():
    """真实回一份 stub_status 文本，让 /api/metrics 走完整「扫描 + HTTP 抓取」链路。"""
    body = (b"Active connections: 3 \n"
            b"server accepts handled requests\n 128 128 512 \n"
            b"Reading: 0 Writing: 1 Waiting: 2 \n")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# ---------- 各段基准 ----------

def bench_functions(fx, repeat):
    """单个扫描/读取函数的冷/稳态成本。"""
    sys.path.insert(0, BACKEND_DIR)
    from nginxctl import NginxController

    fresh = lambda: NginxController(fx["nginx"], fx["conf_dir"])  # noqa: E731
    ctl = fresh()
    tail = os.path.getsize(os.path.join(fx["prefix"], "logs", "error.log"))

    cases = [
        ("collect_included_files", lambda c: c.collect_included_files()),
        ("find_error_log_paths", lambda c: c.find_error_log_paths()),
        ("find_access_log_paths", lambda c: c.find_access_log_paths()),
        ("find_stub_status", lambda c: c.find_stub_status()),
        ("detect_listen_port", lambda c: c.detect_listen_port()),
        ("read_error_log_since(已到尾部)", lambda c: c.read_error_log_since(tail, 200)),
    ]
    out = {}
    for name, fn in cases:
        out[name] = {
            "cold": cold_call(lambda fn=fn: fn(fresh())),
            "steady": steady_call(lambda fn=fn: fn(ctl), repeat),
        }
    return out


def bench_proxy_switch(fx, repeat):
    """模型化 PUT /api/proxies/switch 的真实调用序列（不含备份与 nginx -t）：
    新建实例 → list_proxies → switch → commit → list_proxies。"""
    sys.path.insert(0, BACKEND_DIR)
    import proxymgr

    pristine = os.path.join(fx["prefix"], "switch-pristine.conf")
    scratch = os.path.join(fx["prefix"], "switch-scratch.conf")
    shutil.copy2(fx["main_conf"], pristine)

    counts = {"parse_proxies": 0, "parse_upstreams": 0}
    real_pp, real_pu = proxymgr.parse_proxies, proxymgr.parse_upstreams

    def wrap_pp(*a, **kw):
        counts["parse_proxies"] += 1
        return real_pp(*a, **kw)

    def wrap_pu(*a, **kw):
        counts["parse_upstreams"] += 1
        return real_pu(*a, **kw)

    proxymgr.parse_proxies, proxymgr.parse_upstreams = wrap_pp, wrap_pu
    try:
        shutil.copy2(pristine, scratch)
        first_blocks = proxymgr.ProxyManager(scratch).list_proxies()
        target_path = None
        for p in first_blocks:
            if len(p["targets"]) >= 2:
                target_path = p["path"]
                break
        if target_path is None:
            raise RuntimeError("fixture 里找不到可切换的代理（需要至少 2 个目标）")

        samples = []
        for _ in range(repeat):
            shutil.copy2(pristine, scratch)
            counts["parse_proxies"] = counts["parse_upstreams"] = 0
            with count_opens() as opens:
                t0 = time.perf_counter()
                pm = proxymgr.ProxyManager(scratch)
                cur = pm.list_proxies()
                targets = next((p["targets"] for p in cur if p["path"] == target_path), [])
                pm.switch(target_path, targets[1])
                pm.commit()
                pm.list_proxies()
                ms = (time.perf_counter() - t0) * 1000.0
            samples.append((ms, dict(opens), counts["parse_proxies"]))

        return {"ms": statistics.median(s[0] for s in samples),
                "opens": dict(samples[0][1]),
                "parses": {"parse_proxies": samples[0][2]}}
    finally:
        proxymgr.parse_proxies, proxymgr.parse_upstreams = real_pp, real_pu
        for p in (pristine, scratch):
            if os.path.isfile(p):
                os.remove(p)


def bench_http(fx, data_root, repeat):
    """真实 Server/Handler + 真实 socket：测量各轮询端点的稳态成本。"""
    os.environ["NGINX_MANAGER_DATA_DIR"] = data_root
    sys.path.insert(0, BACKEND_DIR)
    import server

    dd = server.ensure_data_dirs()
    server.Handler.data_dirs = dd
    server.Handler.settings = server.SettingsStore(dd["root"])
    server.Handler.controller = server.create_controller(fx["nginx"], fx["conf_dir"])

    conns = {"n": 0}
    real_process_request = server.Server.process_request

    def counting_process_request(self, request, client_address):
        conns["n"] += 1
        return real_process_request(self, request, client_address)

    server.Server.process_request = counting_process_request
    srv = server.Server(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    endpoints = ["/api/status", "/api/metrics", "/api/logs/error?lines=200",
                 "/api/logs/access?lines=500", "/api/proxies"]
    out = {}
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        for ep in endpoints:  # 预热：建立缓存与首个连接
            conn.request("GET", ep)
            conn.getresponse().read()
        for ep in endpoints:
            conns["n"] = 0
            with count_opens() as opens:
                t0 = time.process_time()
                for _ in range(repeat):
                    conn.request("GET", ep)
                    resp = conn.getresponse()
                    resp.read()
                    assert resp.status == 200, (ep, resp.status)
                cpu_ms = (time.process_time() - t0) * 1000.0
            out[ep] = {"us_cpu_per_req": cpu_ms * 1000.0 / repeat,
                       "conns": conns["n"], "reqs": repeat,
                       "opens_per_req": _per_call(opens, repeat)}
        conn.close()
    finally:
        server.Server.process_request = real_process_request
        srv.shutdown()
        srv.server_close()
    return out


# ---------- 报告 ----------

def fmt_opens(o):
    """打开次数：整数则按整数显示（冷调用/单次），否则保留一位小数（每次平均）。"""
    def one(v):
        return str(int(v)) if float(v).is_integer() else "%.1f" % v

    return "r%s/w%s" % (one(o.get("read", 0)), one(o.get("write", 0)))


def report_human(functions, switch, http, scale, repeat):
    print("=" * 84)
    print("热点路径基准  cold=冷调用  steady=稳态（中位数耗时 / 每次调用平均打开数）")
    print("=" * 84)
    print("%-32s %9s %10s %10s %10s" % ("函数", "cold ms", "cold opens", "steady ms", "steady opens"))
    for name, r in functions.items():
        print("%-32s %9.3f %10s %10.3f %10s"
              % (name, r["cold"]["ms"], fmt_opens(r["cold"]["opens"]),
                 r["steady"]["ms"], fmt_opens(r["steady"]["opens"])))
    print()
    print("代理切换序列（新建 → list_proxies → switch → commit → list_proxies）")
    print("  耗时 %.3f ms   文件打开 %s   parse_proxies %d 次"
          % (switch["ms"], fmt_opens(switch["opens"]), switch["parses"]["parse_proxies"]))
    print()
    if http:
        print("真实 HTTP 轮询端点（%d 次请求，单条 keep-alive 连接）" % repeat)
        print("%-32s %14s %8s %14s" % ("端点", "µs CPU/请求", "连接数", "打开数/请求"))
        for ep, r in http.items():
            print("%-32s %14.1f %8d %14s"
                  % (ep, r["us_cpu_per_req"], r["conns"], fmt_opens(r["opens_per_req"])))
    print("-" * 84)
    print("配置规模：%d 个 include 文件 / %d 个 location / 池 %d 条；repeat=%d；nginx 替身（POSIX）"
          % (scale["include_files"], scale["locations"], scale["pool"], repeat))
    print("=" * 84)


def main():
    ap = argparse.ArgumentParser(description="nginx-manager 热点路径基准")
    ap.add_argument("--repeat", type=int, default=200, help="每个测量点的重复次数（默认 200）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="nm-bench-")
    data_root = os.path.join(tmp, "data")
    stub_srv = None
    try:
        stub_srv, stub_port = start_stub_status_server()
        fx = build_fixture(tmp, stub_port)
        functions = bench_functions(fx, args.repeat)
        switch = bench_proxy_switch(fx, args.repeat)
        http_res = bench_http(fx, data_root, args.repeat) if POSIX else {}

        if args.json:
            print(json.dumps({"repeat": args.repeat, "scale": fx["scale"],
                              "functions": functions, "proxy_switch": switch, "http": http_res},
                             ensure_ascii=False, indent=2, sort_keys=True))
        else:
            report_human(functions, switch, http_res, fx["scale"], args.repeat)
        if not POSIX:
            print("[跳过] HTTP 段：替身脚本依赖 POSIX shell（Windows 上不做该段）", file=sys.stderr)
    finally:
        if stub_srv is not None:
            stub_srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
