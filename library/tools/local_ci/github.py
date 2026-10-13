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

import base64
import binascii
import json
import subprocess
from urllib.parse import quote, urlparse

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

    Older gh-axi printed a one-line count tag (`[N]:`) plus the YAML
    payload, and its `--jq` responses used an `api_response:
    {body, truncated}` envelope. gh-axi 0.1.37 prints the payload raw
    with no envelope: plain calls return YAML, `--jq ... | @base64`
    returns newline-delimited base64. Plain JSON passes through
    untouched.
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
    jq_envelope = bool(lines and lines[0].strip() == "api_response:")
    payload = "\n".join(lines[1:]) if lines and lines[0].rstrip().endswith(":") else text
    try:
        data = yaml.safe_load(payload)
    except Exception as exc:
        raise GitHubError(f"gh-axi api {path} returned non-JSON") from exc
    body_envelope = jq_envelope or (
        isinstance(data, dict) and set(data) == {"body"})
    if body_envelope and isinstance(data, dict) and "body" in data:
        if data.get("truncated") is True:
            raise GitHubError(f"gh-axi api {path} jq response was truncated")
        body = data["body"]
        try:
            return json.loads(body) if isinstance(body, str) else body
        except ValueError:
            return body
    if isinstance(data, str):
        # Raw multi-line text (gh-axi 0.1.37 `--jq` output): YAML folds
        # plain-scalar lines into one space-joined string, which would
        # corrupt newline-delimited records, so hand back the lines.
        return payload
    return data if data is not None else {}


def _api(path: str, method: str = "GET", fields: dict | None = None,
         jq: str = "") -> object:
    # gh-axi api [METHOD] <path> --field k=v --full. --full matters:
    # without it long bodies truncate, and a truncated SHA is a
    # freshness check against a lie.
    args = ["api"]
    if method != "GET":
        args.append(method)
    args.append(path)
    for key, value in (fields or {}).items():
        args.extend(["--field", f"{key}={value}"])
    if jq:
        args.extend(["--jq", jq])
    args.append("--full")
    return _parse_response(_run(*args), path)


def repo_path() -> str:
    """owner/name for every REST call."""
    return f"{config.OWNER}/{config.REPO}"


def pull_url(number: int) -> str:
    """A browser link for a PR's status, never a machine-local log path."""
    return f"https://github.com/{repo_path()}/pull/{number}"


def _shape_pull(data: dict) -> dict:
    head = data.get("head", {}) or {}
    base = data.get("base", {}) or {}
    labels = data.get("labels", []) or []
    return {"number": int(data.get("number", 0)),
            "head": str(head.get("sha", "")),
            "base": str(base.get("ref", "")),
            "base_sha": str(base.get("sha", "")),
            "labels": [str(label.get("name", ""))
                       if isinstance(label, dict) else str(label)
                       for label in labels],
            "draft": bool(data.get("draft", False)),
            "url": str(data.get("html_url", "")),
            "title": str(data.get("title", "")),
            "body": str(data.get("body", "") or "")}


_PULL_FIELDS_JQ = (
    "{number, head: {sha: .head.sha}, base: {ref: .base.ref, sha: .base.sha}, "
    "labels: [.labels[].name], draft, html_url, title, body}"
)


def _decode_jq_objects(payload: object, path: str) -> list[dict]:
    """Decode gh-axi's newline-delimited jq base64 projection.

    gh-axi formats ordinary API arrays as a human table. Nested arrays
    such as `labels` become pseudo-keys (`labels[1]{...}`), which are
    not the JSON shape this integration needs. `--jq ... | @base64`
    keeps each projected record lossless while still using gh-axi.
    """
    if not isinstance(payload, str):
        raise GitHubError(f"gh-axi api {path} returned non-text jq output")
    records = []
    for line in payload.splitlines():
        if not line:
            continue
        try:
            decoded = base64.b64decode(line, validate=True)
            record = json.loads(decoded.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise GitHubError(
                f"gh-axi api {path} returned invalid base64 JSON") from exc
        if not isinstance(record, dict):
            raise GitHubError(f"gh-axi api {path} returned a non-object row")
        records.append(record)
    return records


def list_open_prs() -> list:
    """Every open PR targeting main: number, head SHA, labels, draft, url.

    Drafts are listed and never tested: an unfinished tree has no
    verdict worth posting. Reads the REST pulls endpoint (stable JSON),
    never the human CLI table.
    """
    path = f"repos/{repo_path()}/pulls?state=open&base=main&per_page=100"
    payload = _api(path, jq=f".[] | {_PULL_FIELDS_JQ} | @base64")
    data = _decode_jq_objects(payload, path)
    return [_shape_pull(item) for item in data
            if isinstance(item, dict)]


def get_pr(number: int) -> dict:
    """One PR's integration fields: number, head, base, labels, draft, url."""
    path = f"repos/{repo_path()}/pulls/{number}"
    payload = _api(path, jq=f"{_PULL_FIELDS_JQ} | @base64")
    records = _decode_jq_objects(payload, path)
    if len(records) != 1:
        raise GitHubError(f"pulls/{number} returned a non-object")
    return _shape_pull(records[0])


def pr_files(number: int) -> list:
    """Repo-relative paths the PR touches (the grouping advice input).

    Reads the REST pull-files endpoint (stable JSON filenames),
    following `list_open_prs`' base64 projection. Raises GitHubError
    when unreadable - the caller, not this module, decides that
    unknown files mean eyes-open overlap rather than a refusal.
    """
    path = f"repos/{repo_path()}/pulls/{number}/files?per_page=100"
    payload = _api(path, jq=".[] | {filename} | @base64")
    records = _decode_jq_objects(payload, path)
    return sorted(str(record.get("filename", "")) for record in records
                  if isinstance(record, dict)
                  and str(record.get("filename", "")))


def post_status(head: str, state: str, description: str,
                target_url: str = "") -> None:
    """Post the check verdict for a head SHA.

    state is pending | success | failure | error. `target_url` must be
    a browser-accessible HTTP(S) page. Run logs stay local and are
    printed by `ren ci status`; a filesystem path is not a GitHub link.
    """
    if state not in ("pending", "success", "failure", "error"):
        raise ValueError(f"commit status state {state!r} is not a status")
    if target_url:
        parsed = urlparse(target_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("commit status target_url must be HTTP(S)")
    fields = {"state": state, "context": config.STATUS_CONTEXT,
              "description": description[:140]}
    if target_url:
        fields["target_url"] = target_url
    _api(f"repos/{repo_path()}/statuses/{head}", method="POST", fields=fields)


def set_label(number: int, label: str) -> None:
    """Mark a PR (ready for the queue)."""
    _api(f"repos/{repo_path()}/issues/{number}/labels", method="POST",
         fields={"labels[]": label})


def remove_label(number: int, label: str) -> None:
    """Unmark a PR (kicked, merged, or superseded)."""
    path = (f"repos/{repo_path()}/issues/{number}/labels/"
            f"{quote(label, safe='')}")
    _api(path, method="DELETE")


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
