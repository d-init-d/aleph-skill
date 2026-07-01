from __future__ import annotations

import argparse
import os
from pathlib import Path

from _lib import SKILL_NAME, copy_skill_tree, skill_root


def destination(target: str, scope: str, project_dir: Path) -> Path:
    home = Path.home()
    if target == "codex":
        if scope == "project":
            return project_dir / ".codex" / "skills" / SKILL_NAME
        return home / ".codex" / "skills" / SKILL_NAME
    if target == "claude-code":
        if scope == "project":
            return project_dir / ".claude" / "skills" / SKILL_NAME
        return home / ".claude" / "skills" / SKILL_NAME
    if target == "opencode":
        if scope == "project":
            return project_dir / ".opencode" / "skills" / SKILL_NAME
        return home / ".config" / "opencode" / "skills" / SKILL_NAME
    if target == "agents":
        if scope == "project":
            return project_dir / ".agents" / "skills" / SKILL_NAME
        return home / ".agents" / "skills" / SKILL_NAME
    raise ValueError(f"Unsupported target: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Aleph Timeline Simulator into agent skill paths.")
    parser.add_argument("--target", required=True, choices=["codex", "claude-code", "opencode", "agents"])
    parser.add_argument("--scope", default="user", choices=["user", "project"])
    parser.add_argument("--project-dir", default=".", help="Project directory for project-scope installs.")
    parser.add_argument("--dry-run", action="store_true", help="Show destination without copying.")
    parser.add_argument("--copy", action="store_true", help="Copy the skill tree to the destination.")
    parser.add_argument("--symlink", action="store_true", help="Create a directory symlink to the skill tree.")
    parser.add_argument("--force", action="store_true", help="Replace an existing destination.")
    args = parser.parse_args()

    src = skill_root()
    dest = destination(args.target, args.scope, Path(args.project_dir).resolve())

    if args.dry_run or not (args.copy or args.symlink):
        print(f"source={src}")
        print(f"destination={dest}")
        print("mode=dry-run")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    if args.copy:
        copy_skill_tree(src, dest, force=args.force)
        print(f"copied {src} -> {dest}")
        return

    if args.symlink:
        if dest.exists():
            if not args.force:
                raise FileExistsError(f"Destination already exists: {dest}")
            if dest.is_dir() and not dest.is_symlink():
                raise FileExistsError("Refusing to remove a real directory for symlink install")
            dest.unlink()
        os.symlink(src, dest, target_is_directory=True)
        print(f"symlinked {dest} -> {src}")


if __name__ == "__main__":
    main()
