"""GitHub through gh-axi: PR reads, commit statuses, labels, merges.

Commit statuses and the merge API are free REST calls, not Actions
minutes: posting a status costs nothing against the 2,000-minute
allowance. Every mutating call names its reason, because a status the
service posts is the captain's signal now that no workflow posts one.

`gh` (which gh-axi wraps) carries the auth; this module carries the
shape. All subprocess calls capture text with encoding="utf-8"
(AGENTS.md 9: text=True decodes with the locale codec).
"""

from __future__ import annotations

import json
import subprocess

from library.tools.local_ci import config

GH = "gh-axi"


class GitHubError(RuntimeError):
    """A gh-axi call that failed: stdout/stderr travel with it."""


def _run(*argv: str, input_text: str = "") -> str:
    try:
        result = subprocess.run(
            [GH, *argv], input=input_text or None, capture_output=True,
            encoding="utf-8", check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubError(f"gh-axi {argv[0]} could not run: {exc}") from exc
    if result.returncode != 0:
        raise GitHubError(
            f"gh-axi {' '.join(argv)} failed: "
            f"{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _parse_response(out: str, path: str) -> object:
    """gh-axi's envelope off, payload out.

    gh-axi prints a one-line count tag (`[N]:`) plus the YAML payload
    (or `api_response:` plus a `body:` scalar for --jq calls, which
    this module never makes). Plain JSON passes through untouched.
    """
    text = out.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        pass
    import yaml
    lines = text.splitlines()
    payload = "\n".join(lines[1:]) if lines and lines[0].rstrip().endswith(":") else text
    try:
        data = yaml.safe_load(payload)
    except Exception as exc:
        raise GitHubError(f"gh-axi api {path} returned non-JSON") from exc
    if isinstance(data, dict) and set(data) == {"body"}:
        body = data["body"]
        try:
            return json.loads(body) if isinstance(body, str) else body
        except ValueError:
            return body
    return data if data is not None else {}


def _api(path: str, method: str = "GET", fields: dict | None = None) -> object:
    # gh-axi api [METHOD] <path> --field k=v --full. --full matters:
    # without it long bodies truncate, and a truncated SHA is a
    # freshness check against a lie.
    args = ["api"]
    if method != "GET":
        args.append(method)
    args.append(path)
    for key, value in (fields or {}).items():
        args.extend(["--field", f"{key}={value}"])
    args.append("--full")
    return _parse_response(_run(*args), path)


def repo_path() -> str:
    """owner/name for every REST call."""
    return f"{config.OWNER}/{config.REPO}"


def _shape_pull(data: dict) -> dict:
    head = data.get("head", {}) or {}
    base = data.get("base", {}) or {}
    labels = data.get("labels", []) or []
    return {"number": int(data.get("number", 0)),
            "head": str(head.get("sha", "")),
            "base": str(base.get("ref", "")),
            "labels": [str(label.get("name", ""))
                       for label in labels if isinstance(label, dict)],
            "draft": bool(data.get("draft", False)),
            "url": str(data.get("html_url", "")),
            "title": str(data.get("title", ""))}


def list_open_prs() -> list:
    """Every open PR targeting main: number, head SHA, labels, draft, url.

    Drafts are listed and never tested: an unfinished tree has no
    verdict worth posting. Reads the REST pulls endpoint (stable JSON),
    never the human CLI table.
    """
    data = _api(f"repos/{repo_path()}/pulls?state=open&base=main&per_page=100")
    if not isinstance(data, list):
        raise GitHubError("pulls endpoint returned a non-list")
    return [_shape_pull(item) for item in data
            if isinstance(item, dict)]


def get_pr(number: int) -> dict:
    """One PR's integration fields: number, head, base, labels, draft, url."""
    data = _api(f"repos/{repo_path()}/pulls/{number}")
    if not isinstance(data, dict):
        raise GitHubError(f"pulls/{number} returned a non-object")
    return _shape_pull(data)


def post_status(head: str, state: str, description: str,
                target_url: str = "") -> None:
    """Post the check verdict for a head SHA.

    state is pending | success | failure | error. The description is
    the one-line verdict; the log travels via target_url (a file:// or
    gist path the status command prints - statuses carry no log body).
    """
    if state not in ("pending", "success", "failure", "error"):
        raise ValueError(f"commit status state {state!r} is not a status")
    fields = {"state": state, "context": config.STATUS_CONTEXT,
              "description": description[:140]}
    if target_url:
        fields["target_url"] = target_url
    _api(f"repos/{repo_path()}/statuses/{head}", method="POST", fields=fields)


def set_label(number: int, label: str) -> None:
    """Mark a PR (ready for the queue)."""
    _run("pr", "edit", str(number), "--add-label", label)


def remove_label(number: int, label: str) -> None:
    """Unmark a PR (kicked, merged, or superseded)."""
    _run("pr", "edit", str(number), "--remove-label", label)


def merge(number: int, head: str, message: str = "") -> str:
    """Merge a PR with the head-SHA pin: refuse unless the head the
    queue TESTED is still the head that would land.

    The REST merge endpoint's `sha` parameter is this pin: GitHub
    refuses with 409 when the head moved under us, which is the
    freshness re-check at the exact moment of landing (the local
    re-check before it only decides whether to bother asking).
    Returns the landed merge commit SHA, the next link in the
    freshness chain the queue verifies before the following merge.
    """
    fields = {"merge_method": "merge", "sha": head}
    if message:
        fields["commit_title"] = message[:255]
    try:
        result = _api(f"repos/{repo_path()}/pulls/{number}/merge",
                      method="PUT", fields=fields)
    except GitHubError as exc:
        if "409" in str(exc) or "sha" in str(exc).lower():
            raise GitHubError(
                f"PR #{number} moved under the queue (head {head[:12]} "
                f"is stale): re-test on the new head, never merge blind"
            ) from exc
        raise
    if isinstance(result, dict):
        return str(result.get("sha", ""))
    return ""


def main_sha() -> str:
    """Current origin main's head SHA, the base every batch stacks on."""
    data = _api(f"repos/{repo_path()}/branches/main")
    commit = data.get("commit", {}) if isinstance(data, dict) else {}
    sha = commit.get("sha", "")
    if not sha:
        raise GitHubError("could not read main's head SHA")
    return str(sha)
