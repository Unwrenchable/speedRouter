from __future__ import annotations

import json
import re
from pathlib import Path


KNOWN_TOOLS = {
    "read_file",
    "list_dir",
    "grep_search",
    "semantic_search",
    "apply_patch",
    "create_file",
    "run_in_terminal",
    "runSubagent",
    "github_repo",
}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "agent"


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---\n"):
        return {}, content

    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content

    raw = content[4:end]
    body = content[end + 5 :]
    frontmatter: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"')
    return frontmatter, body


def _extract_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            title = re.sub(r"\s+Agent Personality$", "", title, flags=re.IGNORECASE)
            title = re.sub(r"\s+Agent$", "", title, flags=re.IGNORECASE)
            return title.strip()
    return "Imported Agent"


def _extract_bullets_after_heading(body: str, heading_regex: str, limit: int = 6) -> list[str]:
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(heading_regex, line.strip(), flags=re.IGNORECASE):
            start = i + 1
            break

    if start is None:
        return []

    items: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## ") and items:
            break
        if stripped.startswith("- ") or stripped.startswith("* "):
            value = re.sub(r"^[-*]\s+", "", stripped)
            value = re.sub(r"\*\*", "", value)
            value = value.split(":", 1)[0].strip()
            if value:
                items.append(value)
        if len(items) >= limit:
            break
    return items


def _infer_required_tools(body: str, role: str) -> list[str]:
    lowered = body.lower()
    required = {"read_file", "list_dir"}

    if "code" in lowered or "implement" in lowered or "fix" in lowered:
        required.update({"apply_patch", "create_file"})
    if "bash" in lowered or "command" in lowered or "terminal" in lowered:
        required.add("run_in_terminal")
    if "orchestrator" in role.lower() or "multi-agent" in lowered:
        required.add("runSubagent")
    if "github" in lowered or "repository" in lowered:
        required.add("github_repo")
    if "analysis" in lowered or "research" in lowered:
        required.update({"grep_search", "semantic_search"})

    return sorted(name for name in KNOWN_TOOLS if name in required)


def _profile_for_tools(tools: list[str]) -> tuple[str, str]:
    tool_set = set(tools)
    if "runSubagent" in tool_set or "github_repo" in tool_set:
        return "power", "medium"
    if "apply_patch" in tool_set or "create_file" in tool_set:
        return "balanced", "medium"
    return "safe", "low"


def parse_markdown_agent(path: Path) -> dict[str, object] | None:
    content = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _parse_frontmatter(content)

    if "agent" not in body.lower() and "agent" not in path.name.lower():
        return None

    role = frontmatter.get("name") or _extract_heading(body)
    description = frontmatter.get("description", "").replace("\\n", " ").strip()

    capabilities = _extract_bullets_after_heading(body, r"^##\s+(core\s+capabilities|what\s+you\s+can\s+do)")
    if not capabilities:
        capabilities = _extract_bullets_after_heading(body, r"^##\s+your\s+core\s+mission")

    category = path.parent.name
    tags = [category]
    if capabilities:
        tags.extend(_slugify(c).replace("-", " ") for c in capabilities[:3])
    tags = sorted(set(t for t in tags if t))

    required_tools = _infer_required_tools(body, role)
    preferred_profile, risk_level = _profile_for_tools(required_tools)

    agent_id = _slugify(frontmatter.get("name", "") or path.stem)
    return {
        "id": agent_id,
        "role": role,
        "description": description,
        "tags": tags,
        "capabilities": capabilities[:6],
        "required_tools": required_tools,
        "preferred_profile": preferred_profile,
        "risk_level": risk_level,
    }


def import_agency_agents(source: str) -> list[dict[str, object]]:
    root = Path(source).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Source path does not exist: {root}")

    paths = [p for p in root.rglob("*.md") if p.is_file()]
    imported: dict[str, dict[str, object]] = {}
    for path in paths:
        parsed = parse_markdown_agent(path)
        if not parsed:
            continue
        imported[str(parsed["id"])] = parsed

    return sorted(imported.values(), key=lambda item: str(item["id"]))


def write_json(path: str, payload: list[dict[str, object]]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def merge_into_registry(
    imported: list[dict[str, object]], target_path: str
) -> tuple[Path, int, int, int]:
    target = Path(target_path).expanduser().resolve()
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            raise ValueError(f"Target registry must be a JSON array: {target}")
    else:
        existing = []

    by_id = {
        str(item.get("id")): item
        for item in existing
        if isinstance(item, dict) and item.get("id")
    }
    added = 0
    updated = 0

    for item in imported:
        item_id = str(item["id"])
        previous = by_id.get(item_id)
        by_id[item_id] = item
        if previous is None:
            added += 1
        elif previous != item:
            updated += 1

    merged = sorted(by_id.values(), key=lambda item: str(item["id"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

    return target, len(imported), added, updated
