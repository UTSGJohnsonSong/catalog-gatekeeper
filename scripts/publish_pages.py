"""Builds the landing page and the privacy page as a static site, for GitHub Pages, on the gh-pages branch.

They are the pages the API serves at / and /privacy/, with two differences: asset paths start with the Pages base
path, and "Open the demo" points at the hosted demo, because a static host has no API behind it. The review app is
left out for the same reason.

    python scripts/publish_pages.py --demo-url https://<demo host>/app/            build into build/gh-pages only
    python scripts/publish_pages.py --demo-url https://<demo host>/app/ --push     then commit and push gh-pages

Pages serves a public site only from a public repository on a free plan, and the owner turns it on in the
repository's settings.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = ROOT / "build" / "pages-dist"
WORKTREE = ROOT / "build" / "gh-pages"
BRANCH = "gh-pages"
# What a static host can serve. The review app (app/) needs the API.
PUBLISHED = ("index.html", "privacy", "assets")


def run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> str:
    return subprocess.run(  # noqa: S603 - fixed commands, no shell
        list(command), cwd=cwd, env=env, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout


def build(base: str, demo_url: str) -> None:
    npx = shutil.which("npx")
    if npx is None:
        raise SystemExit("npx was not found; install Node and run npm ci in frontend/ first")
    env = {**os.environ, "VITE_DEMO_URL": demo_url}
    run(npx, "vite", "build", "--base", base, "--outDir", str(DIST), "--emptyOutDir", cwd=FRONTEND, env=env)


def checkout_pages_branch() -> None:
    if (WORKTREE / ".git").exists():
        return
    known = run("git", "branch", "--list", BRANCH).strip()
    remote = run("git", "ls-remote", "--heads", "origin", BRANCH).strip()
    if known:
        run("git", "worktree", "add", str(WORKTREE), BRANCH)
    elif remote:
        run("git", "worktree", "add", "-b", BRANCH, str(WORKTREE), f"origin/{BRANCH}")
    else:
        # The first publish starts a branch with no history of its own, so the site never carries the source.
        run("git", "worktree", "add", "--orphan", "-b", BRANCH, str(WORKTREE))


def assemble() -> None:
    # Files the previous publish left are taken out of the branch's next commit. History keeps them.
    run("git", "rm", "-r", "-q", "--ignore-unmatch", ".", cwd=WORKTREE)
    for name in PUBLISHED:
        source = DIST / name
        if source.is_dir():
            shutil.copytree(source, WORKTREE / name, dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, WORKTREE / name)
        else:
            raise SystemExit(f"the build has no {name}; did the frontend build change?")
    (WORKTREE / ".nojekyll").write_text("", encoding="utf-8")  # Serve assets/ as they are.


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo-url", required=True, help="the hosted demo's review screen")
    parser.add_argument("--base", default="/catalog-gatekeeper/", help="the path Pages serves the site under")
    parser.add_argument("--push", action="store_true", help="commit to gh-pages and push it")
    args = parser.parse_args(argv)
    if not args.demo_url.startswith("https://"):
        parser.error("--demo-url must be an https address")
    if not (args.base.startswith("/") and args.base.endswith("/")):
        parser.error("--base must start and end with /")

    build(args.base, args.demo_url)
    checkout_pages_branch()
    assemble()
    run("git", "add", "-A", cwd=WORKTREE)
    if not run("git", "status", "--porcelain", cwd=WORKTREE).strip():
        print("the site did not change; nothing to publish")
        return
    if not args.push:
        print(f"built into {WORKTREE}; run again with --push to publish it")
        return
    run("git", "commit", "-q", "-m", f"Publish the site for {args.demo_url}", cwd=WORKTREE)
    run("git", "push", "origin", BRANCH, cwd=WORKTREE)
    print(f"pushed {BRANCH}")


if __name__ == "__main__":
    main()
