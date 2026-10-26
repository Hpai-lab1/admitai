"""guide-gate: flags high-level violations in a pull request and alerts the guide.

Runs from the base branch (pull_request_target) and never executes code from the
pull request; it only reads the PR's file list and diff through the GitHub API.

On a violation it fails the check, labels the PR `needs-guide` and @mentions the
guide. The guide can clear a false positive by adding the `guide-cleared` label.
It also requests peer reviewers when none have been requested yet.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

GUIDE = os.environ.get("GUIDE", "narenccode")
MODULE_OWNERS = {
    "knowledge/": "adityadk03",
    "assistant/": "koushikkumar2004",
    "experience/": "ramitharavichandra",
}
STUDENTS = sorted(set(MODULE_OWNERS.values()))
MARKER = "<!-- guide-gate -->"
NEEDS_GUIDE = "needs-guide"
CLEARED = "guide-cleared"

BLOCKED_EXTENSIONS = (
    ".docx",
    ".doc",
    ".pdf",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".sqlite",
    ".db",
    ".dump",
    ".bak",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".zip",
)
PHONE = re.compile(r"(?<![\d.])(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d.])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EMAIL_OK = re.compile(
    r"@(example\.(com|org|net)|[\w-]+\.(test|example|invalid|local)|users\.noreply\.github\.com)$",
    re.I,
)
PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
)
SECRETS = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bxox[abpr]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{35}\b"
)


def mask(value: str) -> str:
    """Show only the edges so the comment does not re-publish personal data."""
    if len(value) <= 4:
        return "****"
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def scan_file(name: str, patch: str | None) -> list[str]:
    """Return human-readable violations for one changed file."""
    found = []
    base = name.rsplit("/", 1)[-1]
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        found.append(f"`{name}`: environment file (may hold secrets)")
    if name.lower().endswith(BLOCKED_EXTENSIONS):
        found.append(
            f"`{name}`: document, data or key file type is not allowed in this public repo"
        )
    if patch is None:
        return found
    line_no = 0
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line_no = int(m.group(1)) - 1 if m else 0
            continue
        if raw.startswith("-"):
            continue
        line_no += 1
        if not raw.startswith("+"):
            continue
        text = raw[1:]
        where = f"`{name}` line {line_no}"
        if SECRETS.search(text):
            found.append(f"{where}: looks like a key or token")
        for m in PHONE.finditer(text):
            found.append(f"{where}: looks like a phone number ({mask(m.group())})")
        for m in EMAIL.finditer(text):
            if not EMAIL_OK.search(m.group()):
                found.append(
                    f"{where}: looks like a real e-mail address ({mask(m.group())})"
                )
        for m in PRIVATE_IP.finditer(text):
            found.append(f"{where}: internal IP address ({mask(m.group())})")
    return found


def reviewers_for(files: list[str], author: str) -> list[str]:
    """Owners of other modules the PR touches; otherwise every other student."""
    touched = {
        owner
        for prefix, owner in MODULE_OWNERS.items()
        if any(f.startswith(prefix) for f in files)
    }
    others = sorted(touched - {author})
    if others:
        return others
    return [s for s in STUDENTS if s != author]


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token, self.repo = token, repo

    def call(self, method: str, path: str, body: dict | None = None):
        url = (
            path
            if path.startswith("https://")
            else f"https://api.github.com/repos/{self.repo}/{path}"
        )
        req = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
        )
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            return json.loads(data) if data else None

    def paged(self, path: str) -> list:
        items, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self.call("GET", f"{path}{sep}per_page=100&page={page}")
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1


def cleared_by_guide(gh: GitHub, number: int) -> bool:
    """True if the most recent `guide-cleared` label was added by the guide."""
    events = gh.paged(f"issues/{number}/events")
    labeled = [
        e
        for e in events
        if e.get("event") == "labeled" and e.get("label", {}).get("name") == CLEARED
    ]
    return bool(labeled) and labeled[-1].get("actor", {}).get("login") == GUIDE


def main() -> int:
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    number = int(os.environ["PR_NUMBER"])
    pr = gh.call("GET", f"pulls/{number}")
    author = pr["user"]["login"]
    labels = {label["name"] for label in pr["labels"]}

    files = gh.paged(f"pulls/{number}/files")
    names = [f["filename"] for f in files]
    violations = [v for f in files for v in scan_file(f["filename"], f.get("patch"))]

    # Ask peers to review when nobody has been asked yet.
    if os.environ.get("EVENT_ACTION") in (
        "opened",
        "ready_for_review",
        "reopened",
    ) and not pr.get("draft"):
        if not pr.get("requested_reviewers") and not gh.call(
            "GET", f"pulls/{number}/reviews"
        ):
            wanted = reviewers_for(names, author)
            if wanted:
                try:
                    gh.call(
                        "POST",
                        f"pulls/{number}/requested_reviewers",
                        {"reviewers": wanted},
                    )
                    print("requested reviewers:", ", ".join(wanted))
                except urllib.error.HTTPError as err:
                    print("could not request reviewers:", err)

    comments = gh.paged(f"issues/{number}/comments")
    existing = next((c for c in comments if MARKER in c.get("body", "")), None)

    if not violations:
        if NEEDS_GUIDE in labels:
            gh.call("DELETE", f"issues/{number}/labels/{NEEDS_GUIDE}")
        if existing:
            gh.call(
                "PATCH",
                f"issues/comments/{existing['id']}",
                {"body": f"{MARKER}\n✅ guide-gate: no high-level violations now."},
            )
        print("guide-gate: no violations")
        return 0

    cleared = CLEARED in labels and cleared_by_guide(gh, number)
    shown = violations[:30] + (
        [f"…and {len(violations) - 30} more"] if len(violations) > 30 else []
    )
    status = (
        "The guide has cleared these findings with the `guide-cleared` label."
        if cleared
        else "This PR is blocked until the items are removed, or the guide reviews them and adds the `guide-cleared` label."
    )
    body = (
        f"{MARKER}\n⚠️ **guide-gate found possible high-level violations** — @{GUIDE}, please take a look.\n\n"
        + "\n".join(f"- {v}" for v in shown)
        + f"\n\n{status}\n\nThis repository is public: secrets, real personal data, server details and document files must never be committed. "
        "If a real secret was pushed, revoke it first — deleting the commit is not enough."
    )
    if existing:
        gh.call("PATCH", f"issues/comments/{existing['id']}", {"body": body})
    else:
        gh.call("POST", f"issues/{number}/comments", {"body": body})
    if NEEDS_GUIDE not in labels:
        gh.call("POST", f"issues/{number}/labels", {"labels": [NEEDS_GUIDE]})

    print("guide-gate violations:\n" + "\n".join(violations))
    if cleared or author == GUIDE:
        print(
            "passing: cleared by the guide"
            if cleared
            else "passing: PR authored by the guide"
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
