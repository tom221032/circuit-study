#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把本地站点同步到 GitHub 仓库 —— 只走 api.github.com（HTTPS API），
不需要 git，也不需要能连 github.com（被墙时依然可用）。

用法：
    set GITHUB_TOKEN=你的令牌          # classic: public_repo / fine-grained: Contents: Read and write
    python scripts\\deploy_api.py             # 真正提交
    python scripts\\deploy_api.py --dry-run   # 只列出会新增/修改/删除哪些文件
    python scripts\\deploy_api.py --no-wait   # 配额用完时不要等，直接退出
    python scripts\\deploy_api.py --batch 80  # 建 tree 时每批的条目数（默认 100）
    python scripts\\deploy_api.py -m "提交说明"

原理：读远端 tree → 用 git 的 blob 规则（sha1("blob <len>\\0" + 内容)）逐个比对本地文件 →
只为有差异的文件上传 blob → 分批建 tree（带 base_tree 串起来）→ 建 commit → 移动 main 分支指针
→ Pages 自动重建。

稳健性：
  * 自动跳过 .gitignore 里以 / 结尾的目录（_frames/、_shots/ 等中间产物不会上传）；
  * 5xx / 连接错误自动重试（指数退避），建 tree 时若仍失败则把这一批对半拆开再试；
  * 403 rate limit 会读 /rate_limit 的剩余配额与重置时间，默认等到重置后继续（--no-wait 关闭）。
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OWNER = "tom221032"
REPO = "circuit-study"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", "_probe", "node_modules"}
SKIP_FILES = {"push.cmd", ".gitignore"}
API = "https://api.github.com"

RETRY_STATUS = {500, 502, 503, 504, 522, 524}
RETRY_MAX = 5
RETRY_BASE = 1.5
WAIT_LIMIT = 70 * 60          # 等配额重置最多等这么久
DEFAULT_BATCH = 100

WAIT_ENABLED = True


# ---------------------------------------------------------------- 基础工具

def read_gitignore_dirs() -> set[str]:
    """.gitignore 里以 / 结尾（或本地确实是目录）的条目 —— 这些目录不上传。"""
    extra: set[str] = set()
    gi = ROOT / ".gitignore"
    if not gi.exists():
        return extra
    try:
        text = gi.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return extra
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        s = s.lstrip("/").rstrip()
        if s.endswith("/"):
            extra.add(s.rstrip("/"))
        elif "*" not in s and (ROOT / s).is_dir():
            extra.add(s)
    return extra


def skip_dirs() -> set[str]:
    return SKIP_DIRS | read_gitignore_dirs()


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    t = getpass.getpass("GitHub token (input hidden): ").strip()
    if not t:
        sys.exit("没有令牌，退出。")
    return t


def fmt_reset(epoch: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(epoch or time.time()))


def _once(url: str, tok: str, method: str, payload, raw: bool):
    data = None
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "circuit-study-deploy")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=90) as r:
            body = r.read()
            return r.status, (body if raw else json.loads(body or b"{}")), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            info = json.loads(body)
        except Exception:
            info = {"raw": body[:400].decode("utf-8", "replace")}
        return e.code, info, dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return 0, {"error": str(e)}, {}


def limits(tok: str):
    """(remaining, reset_epoch)；读 /rate_limit 不消耗核心配额。"""
    st, info, _ = _once(f"{API}/rate_limit", tok, "GET", None, False)
    if st != 200 or not isinstance(info, dict):
        return None, None
    core = (info.get("resources") or {}).get("core") or {}
    return core.get("remaining"), core.get("reset")


def wait_for_quota(reset: float, need: int, allow_wait: bool) -> bool:
    if not allow_wait:
        print(f"! 配额不足（本次约需 {need}），重置时间 {fmt_reset(reset)}；"
              f"按 --no-wait 要求退出。稍后重跑，或去掉 --no-wait 让它等。")
        return False
    wait = max(5.0, (reset or time.time()) - time.time() + 5)
    if wait > WAIT_LIMIT:
        print(f"! 配额要等到 {fmt_reset(reset)} 才重置，超过 {WAIT_LIMIT // 60} 分钟，先退出。")
        return False
    print(f"  配额已用完，等 {int(wait)}s 到 {fmt_reset(reset)} 自动继续…")
    while wait > 0:
        time.sleep(min(30, wait))
        wait -= 30
        print(f"    还差 {max(0, int(wait))}s")
    return True


def api(path: str, tok: str, method: str = "GET", payload=None, raw=False,
        allow_wait=None):
    """带重试的 API 调用。5xx 退避重试；403 配额用完时按需等待。"""
    url = path if path.startswith("http") else API + path
    allowed = WAIT_ENABLED if allow_wait is None else allow_wait
    delay = RETRY_BASE
    for attempt in range(1, RETRY_MAX + 1):
        st, info, headers = _once(url, tok, method, payload, raw)
        if st in RETRY_STATUS or st == 0:
            if attempt == RETRY_MAX:
                return st, info
            sleep = delay + random.uniform(0, 0.6)
            print(f"    {st or '连接失败'}，{sleep:.1f}s 后重试（{attempt}/{RETRY_MAX - 1}）")
            time.sleep(sleep)
            delay *= 2
            continue
        if st == 403 and isinstance(info, dict) and "rate limit" in str(info.get("message", "")).lower():
            reset = float(headers.get("X-RateLimit-Reset") or 0) or (time.time() + 60)
            rem = headers.get("X-RateLimit-Remaining")
            print(f"  403 配额用完（剩余 {rem}），重置 {fmt_reset(reset)}")
            if not wait_for_quota(reset, 1, allowed):
                return st, info
            continue
        return st, info
    return st, info


# ---------------------------------------------------------------- 本地/远端比对

def blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def local_files() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    dirs = skip_dirs()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in dirs]
        for name in filenames:
            if name in SKIP_FILES:
                continue
            p = Path(dirpath) / name
            rel = p.relative_to(ROOT).as_posix()
            try:
                out[rel] = p.read_bytes()
            except OSError as e:
                print(f"  跳过 {rel}: {e}")
    return out


def remote_tree(tok: str):
    st, info = api(f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", tok)
    if st == 404:
        sys.exit(f"仓库或分支不存在：{OWNER}/{REPO}:{BRANCH}（先在网页上建好仓库并初始化 main）")
    if st != 200:
        sys.exit(f"读分支失败 {st}: {info}")
    commit_sha = info["object"]["sha"]
    st, commit = api(f"/repos/{OWNER}/{REPO}/git/commits/{commit_sha}", tok)
    if st != 200:
        sys.exit(f"读提交失败 {st}: {commit}")
    tree_sha = commit["tree"]["sha"]
    st, tree = api(f"/repos/{OWNER}/{REPO}/git/trees/{tree_sha}?recursive=1", tok)
    if st != 200:
        sys.exit(f"读 tree 失败 {st}: {tree}")
    remote = {}
    for e in tree.get("tree", []):
        if e.get("type") == "blob":
            remote[e["path"]] = e["sha"]
    return commit_sha, tree_sha, remote, tree.get("truncated", False)


def build_tree(tok: str, base: str, entries: list, batch: int, depth: int = 0) -> str:
    """分批建 tree：每批以 base_tree 指向上一批的 sha。单批 5xx 时对半拆开重试。"""
    if not entries:
        return base
    sha = base
    for i in range(0, len(entries), batch):
        chunk = entries[i:i + batch]
        sha = _post_tree(tok, sha, chunk, depth)
        print(f"    tree 批次 {i // batch + 1}/{(len(entries) + batch - 1) // batch}（{len(chunk)} 条）")
    return sha


def _post_tree(tok: str, base: str, entries: list, depth: int) -> str:
    st, info = api(f"/repos/{OWNER}/{REPO}/git/trees", tok, "POST",
                   {"base_tree": base, "tree": entries})
    if st in (200, 201):
        return info["sha"]
    if st in RETRY_STATUS and len(entries) > 1 and depth < 6:
        mid = len(entries) // 2
        print(f"    这一批 {len(entries)} 条仍失败（{st}），对半拆开重试")
        first = _post_tree(tok, base, entries[:mid], depth + 1)
        return _post_tree(tok, first, entries[mid:], depth + 1)
    sys.exit(f"建 tree 失败 {st}: {info}")


def batch_size(argv: list[str]) -> int:
    if "--batch" in argv:
        try:
            n = int(argv[argv.index("--batch") + 1])
            return max(1, min(500, n))
        except Exception:
            pass
    return DEFAULT_BATCH


# ---------------------------------------------------------------- 主流程

def main() -> int:
    global WAIT_ENABLED
    WAIT_ENABLED = "--no-wait" not in sys.argv
    dry = "--dry-run" in sys.argv
    batch = batch_size(sys.argv)
    message = "更新站点"
    if "-m" in sys.argv:
        message = sys.argv[sys.argv.index("-m") + 1]

    print(f"本地目录：{ROOT}")
    skipped = sorted(skip_dirs() - {"__pycache__"})
    if skipped:
        print(f"跳过目录：{', '.join(skipped)}")
    tok = "" if dry and not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")) else token()

    local = local_files()
    print(f"本地文件：{len(local)} 个，{sum(len(v) for v in local.values()) / 1048576:.1f} MB")

    if dry and not tok:
        print("\n[dry-run] 没有令牌，只统计本地文件。")
        for rel in sorted(local)[:15]:
            print(f"   {rel}")
        print(f"   … 共 {len(local)} 个")
        return 0

    head, tree_sha, remote, truncated = remote_tree(tok)
    if truncated:
        print("! 远端 tree 列表被截断（仓库文件过多），比对可能不完整。")

    add, mod, same = [], [], 0
    for rel, data in local.items():
        sha = blob_sha(data)
        if rel not in remote:
            add.append(rel)
        elif remote[rel] != sha:
            mod.append(rel)
        else:
            same += 1
    delete = [p for p in remote if p not in local and p not in SKIP_FILES]

    print(f"内容未变 {same} 个；新增 {len(add)}；修改 {len(mod)}；删除 {len(delete)}")
    for label, items in (("新增", add), ("修改", mod), ("删除", delete)):
        if items:
            print(f"  {label}:")
            for p in items[:40]:
                print(f"    {p}")
            if len(items) > 40:
                print(f"    … 共 {len(items)} 个")

    if not (add or mod or delete):
        print("\n远端已经是最新的，不需要提交。")
        return 0

    if dry:
        print("\n[dry-run] 未做任何写入。")
        return 0

    need = len(add) + len(mod) + 6
    rem, reset = limits(tok)
    if rem is not None:
        print(f"\n配额：剩余 {rem}/5000；本次约需 {need}；重置时间 {fmt_reset(reset)}")
        if rem < need and not wait_for_quota(reset or time.time() + 60, need, WAIT_ENABLED):
            return 1

    todo = add + mod
    print(f"\n上传 {len(todo)} 个文件…")

    def make_blob(rel):
        st, info = api(f"/repos/{OWNER}/{REPO}/git/blobs", tok, "POST", {
            "content": base64.b64encode(local[rel]).decode("ascii"),
            "encoding": "base64",
        }, allow_wait=WAIT_ENABLED)
        if st not in (200, 201):
            raise RuntimeError(f"blob {rel} 失败 {st}: {info}")
        return rel, info["sha"]

    shas = {}
    if todo:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, (rel, sha) in enumerate(ex.map(make_blob, todo), 1):
                shas[rel] = sha
                if i % 20 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}")

    entries = []
    for rel in add + mod:
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": shas[rel]})
    for rel in delete:
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": None})

    print(f"\n建 tree（{len(entries)} 条，每批 {batch}）…")
    new_tree_sha = build_tree(tok, tree_sha, entries, batch)

    st, new_commit = api(f"/repos/{OWNER}/{REPO}/git/commits", tok, "POST",
                         {"message": message, "tree": new_tree_sha, "parents": [head]})
    if st not in (200, 201):
        sys.exit(f"建 commit 失败 {st}: {new_commit}")

    st, ref = api(f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", tok, "PATCH",
                  {"sha": new_commit["sha"]})
    if st not in (200, 201):
        sys.exit(f"更新分支失败 {st}: {ref}")

    print(f"\n[√] 已提交 {new_commit['sha'][:7]} 到 {OWNER}/{REPO}:{BRANCH}")
    print("    等 Pages 重建（通常 1~2 分钟）…")

    for i in range(40):
        time.sleep(8)
        st, b = api(f"/repos/{OWNER}/{REPO}/pages/builds/latest", tok)
        if st == 200 and isinstance(b, dict):
            status = b.get("status")
            if i == 0 or status in ("built", "errored"):
                print(f"    构建状态：{status}")
            if status == "built":
                print(f"\n站点已更新：https://{OWNER}.github.io/{REPO}/  （浏览器 Ctrl+F5 强刷）")
                return 0
            if status == "errored":
                print("    构建失败，去 Actions 看日志：https://github.com/{}/{}/actions".format(OWNER, REPO))
                return 1
        elif st == 404:
            print("    这个仓库还没启用 Pages：Settings → Pages → Source 选 main / (root) 后重跑即可。")
            return 1
        elif st in (401, 403):
            print(f"    查询构建状态被拒（{st}）：令牌可能没有 Pages 读取权限。")
            print("    提交已经成功，Pages 由 GitHub 自己重建，去 Actions 页面看结果即可：")
            print(f"    https://github.com/{OWNER}/{REPO}/actions")
            return 0
    print("    构建还在进行，稍后自己看一眼 Actions 页面即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
