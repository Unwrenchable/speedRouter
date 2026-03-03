from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .importer import import_agency_agents, merge_into_registry, write_json
from .registry import (
    assess_agent_access,
    find_agents,
    load_agents,
    load_profiles,
    recommend_profile,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentx",
        description="Quick-access agent toolkit with capability/access checks",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all registered agents")

    find_parser = sub.add_parser("find", help="Search agents by keyword")
    find_parser.add_argument("query", help="search term")

    check_parser = sub.add_parser("check", help="Check if profile grants required access")
    check_parser.add_argument("agent_id", help="agent identifier")
    check_parser.add_argument("--profile", default=None, help="profile name (safe|balanced|power)")

    rec_parser = sub.add_parser("recommend", help="Recommend best profile for an agent")
    rec_parser.add_argument("agent_id", help="agent identifier")

    export_parser = sub.add_parser("export", help="Export merged toolkit data")
    export_parser.add_argument("--json", action="store_true", help="output JSON")

    import_parser = sub.add_parser(
        "import-agency",
        help="Import markdown agents from agency-style repos into JSON registry",
    )
    import_parser.add_argument("source", help="path to agency repo or folder with markdown agents")
    import_parser.add_argument(
        "--output",
        default="agent_tools/data/agency_import.json",
        help="output JSON path for imported agents",
    )
    import_parser.add_argument(
        "--merge",
        action="store_true",
        help="merge imported agents into registry target",
    )
    import_parser.add_argument(
        "--merge-target",
        default="agent_tools/data/agents.json",
        help="registry JSON path used when --merge is set",
    )

    return parser


def cmd_list() -> int:
    agents = load_agents()
    for agent in agents.values():
        print(f"{agent.id:24} {agent.role}")
    return 0


def cmd_find(query: str) -> int:
    agents = load_agents()
    matches = list(find_agents(agents, query))
    if not matches:
        print("No agents found")
        return 1

    for agent in matches:
        print(f"{agent.id}: {agent.role}")
        print(f"  tags: {', '.join(agent.tags)}")
        print(f"  capabilities: {', '.join(agent.capabilities)}")
    return 0


def cmd_check(agent_id: str, profile_name: str | None) -> int:
    agents = load_agents()
    profiles = load_profiles()

    agent = agents.get(agent_id)
    if agent is None:
        print(f"Unknown agent: {agent_id}")
        return 2

    if profile_name is None:
        profile = recommend_profile(agent, profiles)
    else:
        profile = profiles.get(profile_name)
        if profile is None:
            print(f"Unknown profile: {profile_name}")
            return 2

    report = assess_agent_access(agent, profile)

    status = "PASS" if report["pass"] else "FAIL"
    print(f"[{status}] agent={report['agent']} profile={report['profile']}")
    if report["missing_tools"]:
        print(f"missing_tools: {', '.join(report['missing_tools'])}")
    else:
        print("missing_tools: none")

    print(f"extra_tools: {', '.join(report['extra_tools']) if report['extra_tools'] else 'none'}")
    print(f"risk_level: {report['risk_level']}")
    print(f"recommended_profile: {report['recommended_profile']}")
    return 0 if report["pass"] else 3


def cmd_recommend(agent_id: str) -> int:
    agents = load_agents()
    profiles = load_profiles()

    agent = agents.get(agent_id)
    if agent is None:
        print(f"Unknown agent: {agent_id}")
        return 2

    profile = recommend_profile(agent, profiles)
    print(profile.name)
    print(f"tools: {', '.join(profile.tools)}")
    print(f"write={profile.write} network={profile.network} secrets={profile.secrets}")
    return 0


def cmd_export(as_json: bool) -> int:
    agents = load_agents()
    profiles = load_profiles()

    payload = {
        "agents": [asdict(a) for a in agents.values()],
        "profiles": [asdict(p) for p in profiles.values()],
    }

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"agents={len(payload['agents'])} profiles={len(payload['profiles'])}")

    return 0


def cmd_import_agency(source: str, output: str, merge: bool, merge_target: str) -> int:
    imported = import_agency_agents(source)
    if not imported:
        print("No agent markdown files were detected")
        return 1

    if merge:
        target, imported_count, added, updated = merge_into_registry(imported, merge_target)
        print(f"Imported: {imported_count}")
        print(f"Merged into: {target}")
        print(f"Added: {added}")
        print(f"Updated: {updated}")
        return 0

    target = write_json(output, imported)
    print(f"Imported: {len(imported)}")
    print(f"Output: {target}")
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "list":
        return cmd_list()
    if args.command == "find":
        return cmd_find(args.query)
    if args.command == "check":
        return cmd_check(args.agent_id, args.profile)
    if args.command == "recommend":
        return cmd_recommend(args.agent_id)
    if args.command == "export":
        return cmd_export(args.json)
    if args.command == "import-agency":
        return cmd_import_agency(args.source, args.output, args.merge, args.merge_target)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
