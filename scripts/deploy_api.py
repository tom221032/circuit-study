#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把本地站点同步到 GitHub 仓库 —— 只走 api.github.com（HTTPS API），
不需要 git，也不需要能连 github.com（被墙时依然可用）。

用法：
    set GITHUB_TOKEN=你的令牌          # classic: public_repo / fine-grained: Contents: Read and write
    python scripts\\deploy_api.py            # 真正提交
    python scripts\\deploy_api.py --dry-run  # 只列出会新增/修改/删除哪些文件
    python scripts\\deploy_api.py -m "提交说明"

原理：读远端 tree → 用 git 的 blob 规则（sha1("blob <len>\\0" + 内容)）逐个比对本地文件 →
只为有差异的文件上传 blob → 建 tree / commit → 移动 main 分支指针 → Pages 自动重建。
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
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
def _ignored_dirs() -> set[str]:
    """把 .gitignore 里以 / 结尾的目录也排除，避免把中间产物（_frames/、_shots/）传上去。"""
    out: set[str] = set()
    gi = ROOT / ".gitignore"
    if gi.exists():
        for line in gi.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line.endswith("/"):
                out.add(line.rstrip("/").lstrip("/"))
    return out


SKIP_DIRS = {".git", "__pycache__", "_probe", "node_modules"} | _ignored_dirs()
SKIP_FILES = {"push.cmd", ".gitignore"}
API = "https://api.github.com"


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    t = getpass.getpass("GitHub token (input hidden): ").strip()
    if not t:
        sys.exit("没有令牌，退出。")
    return t


RETRY_STATUS = {500, 502, 503, 504, 522, 524}
RETRY_MAX = 4          # 首次 + 3 次重试
RETRY_BASE = 1.5       # 秒，指数退避

WAIT_LIMIT = 70 * 60   # 等配额重置最多等 70 分钟
WAIT_ENABLED = True    # 配额用尽时是否自动等重置（--no-wait 关掉）


def _limits(tok: str):
    """读 /rate_limit（不消耗核心配额）→ (remaining, reset_epoch)。失败返回 (None, 0)。"""
    req = urllib.request.Request(API + "/rate_limit")
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("User-Agent", "circuit-study-deploy")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            core = json.loads(r.read()).get("resources", {}).get("core", {})
        return int(core.get("remaining", 0)), int(core.get("reset", 0))
    except Exception:
        return None, 0


def _fmt_reset(reset: int) -> str:
    if not reset:
        return "未知"
    return time.strftime("%H:%M:%S", time.localtime(reset))


def quota_report(tok: str, need: int) -> bool:
    """开工前看一眼配额。返回 True 表示够用。"""
    remaining, reset = _limits(tok)
    if remaining is None:
        print("配额：读不到 /rate_limit，继续（失败会自己等重置）。")
        return True
    print(f"配额：剩余 {remaining} 次 / 每小时 5000；本次约需 {need} 次；重置时间 {_fmt_reset(reset)}")
    return remaining >= need


def wait_for_quota(reset: int, need: int, allow_wait: bool) -> bool:
    """配额不够时等到重置。返回 False 表示不该等（没开 --wait 或等太久）。"""
    now = time.time()
    secs = max(0, int(reset - now)) + 20
    if not allow_wait:
        print(f"\n[X] 配额不够（还需要约 {need} 次）。两种办法：")
        print("    1) 等重置后再跑：上面的重置时间到了直接双击 push.cmd；")
        print("    2) 这次就等：python scripts\\deploy_api.py --wait")
        return False
    if secs > WAIT_LIMIT:
        print(f"[X] 要等 {secs/60:.0f} 分钟才能重置，超过 {WAIT_LIMIT/60:.0f} 分钟上限。")
        return False
    print(f"\n配额用完，等 {secs/60:.1f} 分钟后重置（Ctrl+C 可随时中止）…")
    left = secs
    while left > 0:
        step = min(30, left)
        time.sleep(step)
        left -= step
        print(f"    还剩 {left/60:.1f} 分钟…")
    print("    继续。")
    return True


def _once(path: str, tok: str, method: str, payload, raw: bool):
    url = path if path.startswith("http") else API + path
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
            return r.status, (body if raw else json.loads(body or b"{}"))
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            info = json.loads(body)
        except Exception:
            info = {"raw": body[:400].decode("utf-8", "replace")}
        return e.code, info


def _rate_limited(st: int, info) -> bool:
    return st == 403 and isinstance(info, dict) and "rate limit" in str(info.get("message", "")).lower()


def api(path: str, tok: str, method: str = "GET", payload=None, raw=False, allow_wait=None):
    """带重试的 GitHub API 调用：5xx / 网络抖动退避重试；403 配额用尽则等重置再试。"""
    if allow_wait is None:
        allow_wait = WAIT_ENABLED
    last = (0, {"message": "not executed"})
    waited = False
    for attempt in range(1, RETRY_MAX + 1):
        try:
            st, info = _once(path, tok, method, payload, raw)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            st, info = 0, {"message": f"{type(e).__name__}: {e}"}
        last = (st, info)

        if _rate_limited(st, info):
            _, reset = _limits(tok)
            if not waited and wait_for_quota(reset, 1, allow_wait):
                waited = True
                attempt -= 1
                continue
            return st, info

        if st not in RETRY_STATUS and st != 0:
            return st, info
        if attempt == RETRY_MAX:
            break
        wait = RETRY_BASE ** attempt
        print(f"    ! {method} {path.split('?')[0]} → {st or '网络异常'}，{wait:.1f}s 后重试（{attempt}/{RETRY_MAX - 1}）")
        time.sleep(wait)
    return last


def blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def local_files() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
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


def batch_size(argv: list[str]) -> int:
    if "--batch" in argv:
        try:
            n = int(argv[argv.index("--batch") + 1])
            return max(1, min(n, 500))
        except (IndexError, ValueError):
            pass
    return 120


def main() -> int:
    global WAIT_ENABLED
    WAIT_ENABLED = "--no-wait" not in sys.argv
    dry = "--dry-run" in sys.argv
    message = "更新站点"
    if "-m" in sys.argv:
        message = sys.argv[sys.argv.index("-m") + 1]

    print(f"本地目录：{ROOT}")
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

    needed = len(add) + len(mod) + 10
    print()
    if not quota_report(tok, needed):
        _, reset = _limits(tok)
        if not wait_for_quota(reset, needed, WAIT_ENABLED):
            return 1
        if not quota_report(tok, needed):
            print("[X] 配额仍未恢复，稍后再试。")
            return 1

    todo = add + mod
    print(f"\n上传 {len(todo)} 个文件…")

    def make_blob(rel):
        st, info = api(f"/repos/{OWNER}/{REPO}/git/blobs", tok, "POST", {
            "content": base64.b64encode(local[rel]).decode("ascii"),
            "encoding": "base64",
        })
        if st not in (200, 201):
            raise RuntimeError(f"blob {rel} 失败 {st}: {info}")
        return rel, info["sha"]

    shas = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (rel, sha) in enumerate(ex.map(make_blob, todo), 1):
            shas[rel] = sha
            if i % 10 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")

    entries = []
    for rel in add + mod:
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": shas[rel]})
    for rel in delete:
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": None})

    # 一次提交里文件太多时，GitHub 的 /git/trees 偶发 502；分小批串成一串树更稳
    batch = batch_size(sys.argv)
    chunks = [entries[i:i + batch] for i in range(0, len(entries), batch)] or [[]]
    tree = tree_sha
    if len(chunks) > 1:
        print(f"\n建 tree：{len(entries)} 项，分 {len(chunks)} 批（每批 ≤{batch} 项）…")
    for bi, chunk in enumerate(chunks, 1):
        st, new_tree = api(f"/repos/{OWNER}/{REPO}/git/trees", tok, "POST",
                           {"base_tree": tree, "tree": chunk})
        if st not in (200, 201):
            sys.exit(f"建 tree 失败（第 {bi}/{len(chunks)} 批，{len(chunk)} 项）{st}: {new_tree}")
        tree = new_tree["sha"]
        if len(chunks) > 1:
            print(f"  {bi}/{len(chunks)} ok")

    st, new_commit = api(f"/repos/{OWNER}/{REPO}/git/commits", tok, "POST",
                         {"message": message, "tree": tree, "parents": [head]})
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
