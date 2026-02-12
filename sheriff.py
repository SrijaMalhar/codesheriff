import os
from review import process_pr

owner = os.environ["REPO_OWNER"]
repo = os.environ["REPO_NAME"]
pr_num = int(os.environ["PR_NUMBER"])
sha = os.environ["HEAD_SHA"]
shadow = os.environ.get("SHADOW_MODE", "false").lower() == "true"

result = process_pr(owner=owner, repo=repo, pr_num=pr_num, sha=sha, shadow=shadow)
print(f"Review posted: {result}")
