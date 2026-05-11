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
        elif "APPROVED" in review_states:
            status = "approved"
        elif any(s in ("COMMENTED", "DISMISSED") for s in review_states):
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

    with open("data.json", "w") as f:
        json.dump({"updated_at": "", "prs": all_prs}, f)

    print(f"Wrote {len(all_prs)} PRs to data.json")


if __name__ == "__main__":
    main()
