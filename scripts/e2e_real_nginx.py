# -*- coding: utf-8 -*-
"""发布前真机走查：用工作区那份真实 nginx 二进制跑一遍管理端全流程。

覆盖 ROADMAP 第二节「未覆盖的剩余风险 ①」：端到端只跑过替身脚本的情况。
要真实 nginx：需要工作区存在 `nginx-1.30.4/`（本地测试树，gitignored），故**不进 CI**，
属发布前手动执行的验证脚本。

用法：
    python3 scripts/e2e_real_nginx.py        # 成功退出码 0，任一项失败退出码 1
    NM_E2E_DIR=/tmp/nm-e2e python3 scripts/e2e_real_nginx.py   # 指定中间目录

隔离方式：把 nginx 树复制到中间目录，并把副本配置里的 error_log/access_log/pid 改写成
中间目录的绝对路径、监听端口换成空闲端口 —— 编译期 --prefix 仍指向工作区（`nginx -V`
决定，改不了），不改写就会写进工作区那份开发实例的日志与 pid。
中间目录跑完保留（便于回看日志与配置），失败项会逐条列出。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NGINX_TREE = os.path.join(REPO, "nginx-1.30.4")
E2E = os.environ.get("NM_E2E_DIR") or tempfile.mkdtemp(prefix="nm-e2e-")
PREFIX = os.path.join(E2E, "nginx")
CONF = os.path.join(PREFIX, "conf")
DATA = os.path.join(E2E, "data")
LOGS = os.path.join(PREFIX, "logs")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s | %s | %s" % ("PASS" if ok else "FAIL", name, detail))
    return bool(ok)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method, url, payload=None, csrf=True, timeout=30):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if csrf:
        headers["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def raw_get(url, timeout=5):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def setup():
    """复制一份 nginx 树到中间目录，并把日志/pid 指向中间目录的绝对路径、监听端口换空闲端口。"""
    shutil.rmtree(E2E, ignore_errors=True)
    os.makedirs(DATA)
    for sub in ("conf", "logs", "html", "sbin"):
        shutil.copytree(os.path.join(NGINX_TREE, sub), os.path.join(PREFIX, sub))
    nginx_bin = os.path.join(PREFIX, "sbin", "nginx")
    os.chmod(nginx_bin, 0o755)
    for d in ("client_body_temp", "proxy_temp", "fastcgi_temp", "uwsgi_temp", "scgi_temp"):
        os.makedirs(os.path.join(PREFIX, d), exist_ok=True)
    site_port = free_port()
    conf_path = os.path.join(CONF, "nginx.conf")
    text = open(conf_path, encoding="utf-8").read()
    text = re.sub(r"listen\s+18080", "listen       %d" % site_port, text)
    text = text.replace("logs/error.log", os.path.join(LOGS, "error.log"))
    text = text.replace("logs/access.log", os.path.join(LOGS, "access.log"))
    text = re.sub(r"pid\s+logs/nginx\.pid", "pid        %s" % os.path.join(LOGS, "nginx.pid"), text)
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(DATA, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"nginxPath": nginx_bin, "confDir": CONF, "backupRetention": 5}, f)
    return nginx_bin, site_port


def start_server(nginx_bin, port):
    env = dict(os.environ)
    env["BROWSER"] = "/usr/bin/true"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-u", os.path.join(REPO, "backend", "server.py"),
         "--data-dir", DATA, "--nginx-path", nginx_bin, "--conf-dir", CONF, "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=REPO,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError("服务提前退出")
            continue
        m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
        if m:
            return proc, "http://127.0.0.1:%s" % m.group(1), int(m.group(1))
    raise RuntimeError("服务未就绪")


def nginx_procs(prefix):
    """本实例的 nginx 进程（master + 它的 worker）。

    不能按「命令行含 sbin/nginx」匹配：manager 自己的命令行里也有 --nginx-path .../sbin/nginx。
    也不给 worker 加路径条件：worker 的进程标题就是 "nginx: worker process"，没有路径。
    故先按 master 命令行里的 prefix 认出本实例的 master，再按父 pid 收它的 worker。
    """
    out = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    rows = []
    master_pid = None
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, cmd = parts[0], parts[1], parts[2]
        if "nginx: master process" in cmd and prefix in cmd:
            master_pid = pid
        rows.append((pid, ppid, cmd))
    hits = []
    for pid, ppid, cmd in rows:
        if "nginx:" not in cmd:
            continue
        if pid == master_pid or (master_pid and ppid == master_pid):
            hits.append("%s %s" % (pid, cmd))
    return hits


def reload_and_settle(base, site_url):
    """重载是异步的：`nginx -s reload` 只是发信号就返回，新配置由 master 重新 fork worker 后生效，
    旧 worker 仍会接一小段连接。故重载后停一拍再验证，避免把竞态当成「没生效」。"""
    st, body = http("POST", base + "/api/nginx/reload")
    time.sleep(0.8)
    return st, body


def main():
    if not os.path.isfile(os.path.join(NGINX_TREE, "sbin", "nginx")) \
            and not os.path.isfile(os.path.join(NGINX_TREE, "nginx.exe")):
        print("跳过：工作区没有 nginx-1.30.4/ 真实 nginx（本脚本需要真实二进制，不进 CI）")
        return 0
    nginx_bin, site = setup()
    site_url = "http://127.0.0.1:%d" % site
    port = free_port()
    proc, base, sport = start_server(nginx_bin, port)
    print("服务地址 %s（nginx prefix=%s，站点端口=%d）\n" % (base, PREFIX, site))
    try:
        # ---- 1. 状态：真实 nginx -v 版本 ----
        st, body = http("GET", base + "/api/status")
        check("状态/版本来自真实 nginx -v", st == 200 and body.get("version") == "1.30.4",
              "version=%s pid=%s" % (body.get("version"), body.get("pid")))
        check("状态/主配文件存在", body.get("confFileExists") is True, "confPath=%s" % body.get("confPath"))

        # ---- 2. nginx -t 真实校验 ----
        st, body = http("POST", base + "/api/config/test")
        check("nginx -t 真实校验通过", st == 200 and body.get("ok") is True,
              (body.get("output") or "").replace("\n", " ")[:120])

        # ---- 3. 真实启动 ----
        st, body = http("POST", base + "/api/nginx/start")
        check("启动真实 nginx", st == 200 and body.get("ok") is True, body.get("message", ""))
        procs = nginx_procs(PREFIX)
        check("进程确实存在（master+worker）", len(procs) >= 2, "%d 个进程" % len(procs))
        code, text = raw_get(site_url + "/")
        check("真实 HTTP 端口可访问", code == 200, "GET / -> %s" % code)

        # ---- 4. stub_status 真实指标 ----
        st, body = http("POST", base + "/api/metrics/enable", {"path": "/nginx_status"})
        check("stub_status 已存在时幂等返回", st == 200 and body.get("already") is True, body.get("stubPath", ""))
        st, body = http("GET", base + "/api/metrics")
        m = body.get("metrics") or {}
        check("抓取真实 stub_status 指标", st == 200 and body.get("available") is True and "requests" in m,
              "port=%s stubPath=%s metrics=%s" % (body.get("port"), body.get("stubPath"), m))

        # ---- 5. 写配置（带备份）→ 重载 → 真实验证生效 ----
        st, body = http("GET", base + "/api/config/file?path=nginx.conf")
        original = body["content"]
        check("读取主配置文件", st == 200 and ("listen       %d" % site) in original, "%d 字节" % len(original))

        st, body = http("PUT", base + "/api/config/file",
                        {"path": "nginx.conf", "content": original, "runTest": True, "doBackup": True})
        check("保存未变更内容（含备份）成功", st == 200 and body.get("ok") is True,
              "backupId=%s backedUp=%s" % (body.get("backupId"), body.get("backedUp")))
        backup_id = body.get("backupId")

        st, body = http("POST", base + "/api/proxies",
                        {"path": "/e2e-stub", "target": site_url + "/nginx_status",
                         "template": "standard"})
        check("新增代理触发真实 nginx -t 并落盘", st == 200 and body.get("ok") is True,
              "backupId=%s test=%s" % (body.get("backupId"), (body.get("test") or {}).get("output", "")[:60]))
        on_disk = open(os.path.join(CONF, "nginx.conf"), encoding="utf-8").read()
        check("代理块已写入 nginx.conf", "location /e2e-stub" in on_disk,
              "含 proxy_pass=%s" % (("proxy_pass %s/nginx_status" % site_url) in on_disk))

        st, body = reload_and_settle(base, site_url)
        check("重载成功", st == 200 and body.get("ok") is True, body.get("message", ""))
        code, text = raw_get(site_url + "/e2e-stub")
        check("新增代理真实生效（透传 stub_status）", code == 200 and "Active connections" in text,
              "GET /e2e-stub -> %s, %d 字节" % (code, len(text)))

        # ---- 6. 坏配置：409 + saved:true，并验证真实行号 ----
        broken = original + "\nINVALID_DIRECTIVE on;\n"
        st, body = http("PUT", base + "/api/config/file",
                        {"path": "nginx.conf", "content": broken, "runTest": True, "doBackup": True})
        detail = body.get("detail", "")
        errline = (body.get("test") or {}).get("errLine")
        check("坏配置返回 409 + saved:true（已保存未应用）",
              st == 409 and body.get("saved") is True and "INVALID_DIRECTIVE" in detail,
              "status=%s saved=%s errLine=%s" % (st, body.get("saved"), errline))
        line_no = broken.split("\n").index("INVALID_DIRECTIVE on;") + 1
        check("报错行号解析正确（真实 nginx 输出）", errline == line_no,
              "解析=%s 期望=%s" % (errline, line_no))
        disk_now = open(os.path.join(CONF, "nginx.conf"), encoding="utf-8").read()
        check("坏配置确实写在磁盘上（语义=已保存未应用）", "INVALID_DIRECTIVE" in disk_now, "")

        # 复原
        st, body = http("PUT", base + "/api/config/file",
                        {"path": "nginx.conf", "content": original, "runTest": True, "doBackup": False})
        check("恢复合法配置", st == 200 and body.get("ok") is True, "")
        st, body = reload_and_settle(base, site_url)
        # 恢复的是「新增代理之前」的原文，故 /e2e-stub 应当消失、站点本身照常
        code_stub, _ = raw_get(site_url + "/e2e-stub")
        code_root, _ = raw_get(site_url + "/")
        check("重载后恢复的配置真实生效（/ 200 且 /e2e-stub 404）",
              code_root == 200 and code_stub == 404,
              "GET / -> %s, GET /e2e-stub -> %s" % (code_root, code_stub))

        # ---- 7. 备份列表 / diff / 回滚 ----
        st, body = http("GET", base + "/api/backups")
        ids = [b["id"] for b in body.get("backups", [])]
        check("备份列表非空", st == 200 and len(ids) >= 2, "共 %d 份：%s" % (len(ids), ids[:5]))

        target_bid = backup_id or (ids[0] if ids else None)
        # 最新一份备份（含 /e2e-stub）与当前文件的 diff 必须非空；用 target_bid 比是比不出来的
        # （它正是当前内容的来源，diff 天然为空）
        st, body = http("GET", base + "/api/backups/diff?a=%s&b=current&path=nginx.conf" % ids[0])
        check("备份与当前 diff 可读（含新增代理的差异）",
              st == 200 and "location /e2e-stub" in body.get("diff", ""),
              "diff 行数=%d" % len(body.get("diff", "").splitlines()))

        st, body = http("POST", base + "/api/backups/restore", {"id": target_bid})
        check("回滚备份成功且 nginx -t 通过", st == 200 and body.get("ok") is True,
              "restored=%s" % body.get("restored"))
        after = open(os.path.join(CONF, "nginx.conf"), encoding="utf-8").read()
        check("回滚后内容与备份一致（/e2e-stub 已消失）", "location /e2e-stub" not in after,
              "%d 字节" % len(after))
        reload_and_settle(base, site_url)
        code, text = raw_get(site_url + "/e2e-stub")
        check("回滚真实生效（/e2e-stub 返回 404）", code == 404, "GET /e2e-stub -> %s" % code)

        # ---- 8. 日志读取（真实日志文件） ----
        for _ in range(3):
            raw_get(site_url + "/")
            raw_get(site_url + "/nginx_status")
        st, body = http("GET", base + "/api/logs/access?lines=20")
        check("访问日志读取（真实 access.log）", st == 200 and "GET /" in body.get("content", ""),
              "logPath=%s paths=%s" % (body.get("logPath"), (body.get("paths") or [])[:3]))
        st, body = http("GET", base + "/api/logs/error?lines=20")
        check("错误日志读取（真实 error.log）", st == 200 and bool(body.get("content")),
              "logPath=%s %d 字节" % (body.get("logPath"), len(body.get("content", ""))))

        # ---- 8b. 实时跟随的增量读取（真实日志文件） ----
        marker = "/e2e-follow-%d" % os.getpid()
        raw_get(site_url + marker)                       # 真实请求 → 真实 access.log 新增一行
        time.sleep(0.4)                                  # 等 nginx flush（access_log 有缓冲）
        st, first = http("GET", base + "/api/logs/access?lines=20")
        check("增量跟随：首帧为尾部快照（reset + 偏移对齐文件末尾）",
              st == 200 and first.get("reset") is True and first.get("offset") == first.get("size"),
              "offset=%s size=%s" % (first.get("offset"), first.get("size")))
        raw_get(site_url + marker)                       # 再产生一行新日志
        time.sleep(0.4)
        st, inc = http("GET", base + "/api/logs/access?lines=20&since=%s" % first.get("offset"))
        check("增量跟随：只取新增行（reset=False 且含本次请求）",
              st == 200 and inc.get("reset") is False and marker in inc.get("content", ""),
              "新增 %d 字节，含标记=%s" % (len(inc.get("content", "")), marker in inc.get("content", "")))
        st, again = http("GET", base + "/api/logs/access?lines=20&since=%s" % inc.get("offset"))
        check("增量跟随：偏移用尽后不再重复下发", st == 200 and again.get("content") == "",
              "content=%r" % again.get("content"))

        error_log = os.path.join(LOGS, "error.log")
        before = os.path.getsize(error_log)
        st, first_err = http("GET", base + "/api/logs/error?lines=20")
        with open(error_log, "a", encoding="utf-8") as f:
            f.write("2026/09/22 10:00:00 [error] 半行写入中")   # 模拟写入中：没有换行符
        st, mid = http("GET", base + "/api/logs/error?since=%s" % first_err.get("offset"))
        check("增量跟随：未写完的半行不下发（偏移不前进）",
              st == 200 and mid.get("content") == "" and mid.get("offset") == first_err.get("offset"),
              "content=%r offset=%s" % (mid.get("content"), mid.get("offset")))
        with open(error_log, "a", encoding="utf-8") as f:
            f.write("（补完）\n")
        st, done = http("GET", base + "/api/logs/error?since=%s" % first_err.get("offset"))
        check("增量跟随：补齐换行后整行取到",
              st == 200 and "半行写入中（补完）" in done.get("content", ""),
              "取到 %d 字节（文件 %d → %d）" % (len(done.get("content", "").encode("utf-8")),
                                                before, os.path.getsize(error_log)))

        # ---- 9. 安全边界抽查 ----
        st, body = http("GET", base + "/api/config/file?path=../../etc/passwd")
        check("路径穿越被拒", st == 400, "status=%s body=%s" % (st, body.get("error")))
        st, body = http("GET", base + "/api/logs/access?path=/etc/passwd")
        check("日志路径越界被拒", st == 403, "status=%s body=%s" % (st, body.get("error")))
        st, body = http("POST", base + "/api/nginx/stop", csrf=False)
        check("缺 X-Requested-With 的写操作被拒", st == 403, "status=%s" % st)

        # ---- 10. 停止 / 重启（真实进程数） ----
        st, body = http("POST", base + "/api/nginx/stop")
        time.sleep(1.5)
        check("优雅停止返回成功", st == 200 and body.get("ok") is True, body.get("message", ""))
        left = nginx_procs(PREFIX)
        check("停止后无残留进程", len(left) == 0, "残留：%s" % left)

        http("POST", base + "/api/nginx/start")
        st2, body2 = http("POST", base + "/api/nginx/restart")
        time.sleep(1.5)
        procs = nginx_procs(PREFIX)
        masters = [p for p in procs if "master" in p]
        check("重启后仅一个 master（不叠加实例）", st2 == 200 and len(masters) == 1,
              "master 数=%d" % len(masters))
        st, body = http("POST", base + "/api/nginx/stop")
        time.sleep(1.0)
        check("最终停止干净", len(nginx_procs(PREFIX)) == 0, "")
    finally:
        try:
            http("POST", base + "/api/nginx/stop", timeout=10)
        except Exception:
            pass
        time.sleep(0.5)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        for line in nginx_procs(PREFIX):
            pid = int(line.split()[0])
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        print("\n=== 汇总 ===")
        failed = [r for r in RESULTS if not r[1]]
        print("共 %d 项，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL %s :: %s" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
