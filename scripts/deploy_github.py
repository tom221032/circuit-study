# -*- coding: utf-8 -*-
"""一键部署到 GitHub Pages（本机网络环境专用：github.com 的默认解析被墙，自动换可用 IP）。

用法：
    python scripts/deploy_github.py            # 设备码登录 → 建仓库 → 推送 → 开 Pages
    python scripts/deploy_github.py --status   # 只看 Pages 构建状态

鉴权用 GitHub 的 device flow（gh CLI 同一套公开流程，申请范围只有 public_repo）：
脚本打印 8 位验证码，打开 https://github.com/login/device 输入即可。
token 只在内存里，不落盘、不打印。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
CLIENT_ID = "178c6fc778ccc68e1d6a"          # GitHub CLI 的公开 OAuth client id
SCOPES = "public_repo"                      # 只申请公开仓库读写：够建仓库、推送、开 Pages
REPO_NAME = "circuit-study"
DESCRIPTION = "电路学习站：邱关源《电路》第六版 · 18 章笔记/课后习题/考研真题 + 80 集视频 AI 字幕 + 354 条视频知识点"

# github.com 在国内部分 IP 被封（本机 20.205.243.166 就連不上），这些是可用候选
GH_IPS = ["140.82.112.3", "140.82.113.3", "140.82.114.3", "140.82.116.3", "20.27.177.113"]
_gh_ip = None


def direct_ok():
    """github.com 直连能不能通（开着代理/VPN 时通常通）。"""
    r = subprocess.run(["curl", "-sS", "-o", os.devnull, "-w", "%{http_code}",
                        "--max-time", "12", "https://github.com/login/device"],
                       capture_output=True, text=True)
    return r.stdout.strip() not in ("", "000")


def gh_ip():
    """直连不通时，退回到「换一个没被封的 GitHub 前端 IP」。返回 None 表示不用 --resolve。"""
    global _gh_ip
    if _gh_ip is not None:
        return _gh_ip or None
    if direct_ok():
        _gh_ip = ""
        print("github.com 直连可用 ✓", flush=True)
        return None
    for ip in GH_IPS:
        r = subprocess.run(["bash", "-c", f"timeout 5 bash -c 'echo > /dev/tcp/{ip}/443'"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            _gh_ip = ip
            print(f"github.com 直连不通，改用 IP {ip}", flush=True)
            return ip
    raise SystemExit("找不到可用的 github.com IP，请检查网络/代理。")


def curl_json(url, payload=None):
    """用 curl 访问 github.com（带 --resolve 指到可用 IP），返回解析后的 JSON。"""
    cmd = ["curl", "-sS", "--max-time", "40"]
    ip = gh_ip()
    if ip:
        cmd += ["--resolve", f"github.com:443:{ip}"]
    if payload is not None:
        # 这两个端点只认 x-www-form-urlencoded（JSON body 会返回 {"error":"Not Found"}）
        cmd += ["-H", "Accept: application/json",
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "-X", "POST", "-d", urlencode(payload)]
    else:
        cmd += ["-H", "Accept: application/json"]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise SystemExit("接口返回异常： " + out[:300])


def api(path, token=None, method="GET", payload=None):
    """api.github.com 直连可用，用 urllib。"""
    url = path if path.startswith("http") else "https://api.github.com" + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "circuit-study-deploy")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", "replace")
        return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)


def device_login():
    code = curl_json("https://github.com/login/device/code",
                     {"client_id": CLIENT_ID, "scope": SCOPES})
    print("\n" + "=" * 46)
    print("  请在浏览器打开： https://github.com/login/device")
    print("  验证码：          " + code["user_code"])
    print("=" * 46 + "\n", flush=True)
    interval, deadline = code.get("interval", 5), time.time() + code.get("expires_in", 900)
    while time.time() < deadline:
        time.sleep(interval)
        tok = curl_json("https://github.com/login/oauth/access_token",
                        {"client_id": CLIENT_ID, "device_code": code["device_code"],
                         "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if tok.get("access_token"):
            print("已授权 ✓", flush=True)
            return tok["access_token"]
        err = tok.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        raise SystemExit("授权失败： %s %s" % (err, tok.get("error_description", "")))
    raise SystemExit("验证码超时，请重新运行脚本。")


def ensure_repo(token, login):
    try:
        api(f"/repos/{login}/{REPO_NAME}", token)
        print(f"仓库已存在：{login}/{REPO_NAME}", flush=True)
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    print(f"创建仓库 {login}/{REPO_NAME} …", flush=True)
    api("/user/repos", token, method="POST", payload={
        "name": REPO_NAME, "description": DESCRIPTION,
        "private": False, "has_issues": True, "has_wiki": False, "auto_init": False,
    })
    time.sleep(2)


def push(token, login, attempts=4):
    url = f"https://github.com/{login}/{REPO_NAME}.git"
    subprocess.run(["git", "remote", "remove", "origin"], cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=ROOT, check=True)
    print("推送约 77MB（首次会久一点，几分钟）…", flush=True)
    auth = f"https://x-access-token:{token}@github.com/{login}/{REPO_NAME}.git"
    ip = gh_ip()
    # 77MB 一次性推送容易被中途重置：关掉 HTTP/2、加大 postBuffer、失败重试
    extra = ["-c", "credential.helper=",
             "-c", "http.version=HTTP/1.1",
             "-c", "http.postBuffer=524288000",
             "-c", "http.lowSpeedLimit=0",
             "-c", "http.lowSpeedTime=999999"]
    if ip:
        extra += ["-c", f"http.curloptResolve=github.com:443:{ip}"]
    for i in range(1, attempts + 1):
        r = subprocess.run(["git"] + extra + ["push", "-u", auth, "HEAD:main"],
                           cwd=ROOT, capture_output=True, text=True, errors="replace")
        if r.returncode == 0:
            print("推送完成 ✓", flush=True)
            return
        tail = (r.stderr or "").strip().splitlines()[-3:]
        print(f"第 {i} 次推送失败： " + " | ".join(tail), flush=True)
        time.sleep(5 * i)
    raise SystemExit("推送连续失败，网络对 github.com 的大包传输不稳定。")


def enable_pages(token, login):
    payload = {"source": {"branch": "main", "path": "/"}}
    try:
        api(f"/repos/{login}/{REPO_NAME}/pages", token, method="POST", payload=payload)
        print("已开启 GitHub Pages ✓", flush=True)
    except urllib.error.HTTPError as e:
        if e.code == 409:
            api(f"/repos/{login}/{REPO_NAME}/pages", token, method="PUT", payload=payload)
            print("Pages 已存在，来源已确认为 main / 根目录", flush=True)
        else:
            raise
    _, info = api(f"/repos/{login}/{REPO_NAME}/pages", token)
    return info.get("html_url", f"https://{login}.github.io/{REPO_NAME}/")


def wait_pages(token, login, url):
    print("等待首次构建 …", flush=True)
    for _ in range(60):
        time.sleep(10)
        try:
            _, b = api(f"/repos/{login}/{REPO_NAME}/pages/builds/latest", token)
        except urllib.error.HTTPError:
            continue
        print("  构建状态： %s" % b.get("status"), flush=True)
        if b.get("status") == "built":
            break
        if b.get("status") == "errored":
            print("构建报错，请看仓库 Actions 日志。", flush=True)
            break
    return url


def main():
    if "--status" in sys.argv and len(sys.argv) > 2:
        _, info = api(f"/repos/{sys.argv[2]}/{REPO_NAME}/pages")
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    # 已授权过的话，可以直接复用内存里的令牌：set GITHUB_TOKEN=... 再跑，省一次设备码
    token = os.environ.get("GITHUB_TOKEN") or device_login()
    _, me = api("/user", token)
    login = me["login"]
    print(f"登录账号：{login}", flush=True)
    ensure_repo(token, login)
    push(token, login)
    url = wait_pages(token, login, enable_pages(token, login))
    print("\n部署完成： " + url, flush=True)


if __name__ == "__main__":
    main()
