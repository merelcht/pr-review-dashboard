import json
import os
import re
import sys
import urllib.request
import urllib.error

TOKEN = os.environ.get("GH_TOKEN", "")
if not TOKEN:
    print("Error: GH_TOKEN is not set. Add PR_DASHBOARD_TOKEN to your repo secrets.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "pr-review-dashboard",
}

REPO_PATTERN = re.compile(r"^[a-zA-Z0-9\-_.]+/[a-zA-Z0-9\-_.]+$")


def gh_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def fetch_repo(repo):
    print(f"Fetching {repo}...")
    open_prs = gh_get(
        f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=100&sort=updated&direction=desc"
    )
    closed_prs = gh_get(
        f"https://api.github.com/repos/{repo}/pulls?state=closed&per_page=30&sort=updated&direction=desc"
    )
    merged_prs = [pr for pr in closed_prs if pr.get("merged_at")]

    results = []
    for pr in open_prs + merged_prs:
        try:
            reviews = gh_get(
                f"https://api.github.com/repos/{repo}/pulls/{pr['number']}/reviews"
            )
        except urllib.error.HTTPError:
            reviews = []

        reviewer_map = {}
        for r in pr.get("requested_reviewers", []):
            reviewer_map[r["login"]] = {
                "login": r["login"],
                "avatar_url": r["avatar_url"],
                "state": "PENDING",
            }
        for r in reviews:
            login = r["user"]["login"]
            if login == pr["user"]["login"]:
                continue
            existing = reviewer_map.get(login)
            if not existing or r.get("submitted_at", "") > existing.get(
                "submitted_at", ""
            ):
                reviewer_map[login] = {
                    "login": r["user"]["login"],
                    "avatar_url": r["user"]["avatar_url"],
                    "state": r["state"],
                    "submitted_at": r.get("submitted_at", ""),
                }

        reviewers = list(reviewer_map.values())
        review_states = [r["state"] for r in reviewers]

        if pr.get("draft"):
            status = "draft"
        elif pr.get("merged_at"):
            status = "merged"
        elif "CHANGES_REQUESTED" in review_states:
            status = "changes_requested"
        elif review_states.count("APPROVED") >= 2:
            status = "approved"
        elif "APPROVED" in review_states or any(
            s in ("COMMENTED", "DISMISSED") for s in review_states
        ):
            status = "in_review"
        else:
            status = "needs_review"

        results.append(
            {
                "id": pr["id"],
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["html_url"],
                "repo": repo,
                "repoShort": repo.split("/")[-1],
                "author": {
                    "login": pr["user"]["login"],
                    "avatar_url": pr["user"]["avatar_url"],
                },
                "draft": pr.get("draft", False),
                "merged": bool(pr.get("merged_at")),
                "status": status,
                "reviewers": reviewers,
                "created_at": pr["created_at"],
                "updated_at": pr["updated_at"],
                "labels": [
                    {"name": l["name"], "color": l["color"]}
                    for l in pr.get("labels", [])
                ],
            }
        )

    print(f"  Found {len(results)} PRs in {repo}")
    return results


def fetch_project_issues(org, project_number):
    print(f"Fetching project board {org}/{project_number}...")
    query = """
    query($org: String!, $number: Int!) {
      organization(login: $org) {
        projectV2(number: $number) {
          items(first: 200, orderBy: {field: POSITION, direction: ASC}) {
            nodes {
              content {
                ... on Issue {
                  title
                  number
                  url
                  state
                  repository { nameWithOwner }
                  labels(first: 10) { nodes { name color } }
                  author { login avatarUrl }
                  createdAt
                  updatedAt
                }
              }
              fieldValues(first: 10) {
                nodes {
                  ... on ProjectV2ItemFieldSingleSelectValue {
                    name
                    field { ... on ProjectV2SingleSelectField { name } }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    variables = {"org": org, "number": project_number}
    data = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=data,
        headers={**HEADERS, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    issues = []
    items = (
        result.get("data", {})
        .get("organization", {})
        .get("projectV2", {})
        .get("items", {})
        .get("nodes", [])
    )
    for item in items:
        if not item:
            continue
        content = item.get("content")
        if not content or not content.get("title"):
            continue
        if content.get("state") != "OPEN":
            continue

        status = ""
        for fv in item.get("fieldValues", {}).get("nodes", []):
            field = (fv or {}).get("field")
            if field and field.get("name", "").lower() == "status":
                status = fv.get("name", "")

        issues.append(
            {
                "title": content["title"],
                "number": content["number"],
                "url": content["url"],
                "repo": content.get("repository", {}).get("nameWithOwner", ""),
                "author": {
                    "login": content.get("author", {}).get("login", ""),
                    "avatar_url": content.get("author", {}).get("avatarUrl", ""),
                },
                "labels": [
                    {"name": l["name"], "color": l["color"]}
                    for l in content.get("labels", {}).get("nodes", [])
                ],
                "status": status,
                "created_at": content.get("createdAt", ""),
                "updated_at": content.get("updatedAt", ""),
            }
        )

    print(f"  Found {len(issues)} open issues on project board")
    return issues


def main():
    with open("repos.json") as f:
        repos = json.load(f)

    for repo in repos:
        if not REPO_PATTERN.match(repo):
            print(f"Error: invalid repo format '{repo}'. Expected 'owner/repo'.")
            sys.exit(1)

    all_prs = []
    for repo in repos:
        all_prs.extend(fetch_repo(repo))

    wizard_issues = []
    try:
        wizard_issues = fetch_project_issues("kedro-org", 10)
    except Exception as e:
        print(f"Warning: could not fetch project board: {e}")

    with open("data.json", "w") as f:
        json.dump(
            {"updated_at": "", "prs": all_prs, "wizard_issues": wizard_issues}, f
        )

    print(f"Wrote {len(all_prs)} PRs and {len(wizard_issues)} issues to data.json")


if __name__ == "__main__":
    main()
