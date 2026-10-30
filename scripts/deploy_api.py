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
SKIP_DIRS = {".git", "__pycache__", "_probe", "node_modules"}
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


def api(path: str, tok: str, method: str = "GET", payload=None, raw=False):
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
        with urllib.request.urlopen(req, data, timeout=60) as r:
            body = r.read()
            return r.status, (body if raw else json.loads(body or b"{}"))
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            info = json.loads(body)
        except Exception:
            info = {"raw": body[:400].decode("utf-8", "replace")}
        return e.code, info


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


def main() -> int:
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

    st, new_tree = api(f"/repos/{OWNER}/{REPO}/git/trees", tok, "POST",
                       {"base_tree": tree_sha, "tree": entries})
    if st not in (200, 201):
        sys.exit(f"建 tree 失败 {st}: {new_tree}")

    st, new_commit = api(f"/repos/{OWNER}/{REPO}/git/commits", tok, "POST",
                         {"message": message, "tree": new_tree["sha"], "parents": [head]})
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
