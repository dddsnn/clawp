import pathlib

import github
import whenever

with pathlib.Path(
    "./.secrets/clawp-agent-development-helper.2026-07-22.private-key.pem"
).open() as f:
    private_key = f.read()
app_auth = github.Auth.AppAuth(app_id=4367662, private_key=private_key)
integration = github.GithubIntegration(auth=app_auth, lazy=False)
installation = integration.get_app_installation(150245518)
gh = integration.get_github_for_installation(150245518)
token = integration.get_access_token(150245518)
print(token.token)
# repo = gh.get_repo("clawp-agents/test-repo")
# issues = {i.number: i for i in repo.get_issues()}
# for n, i in issues.items():
#     print(f"issue {n}: {i.pull_request}")
# since = whenever.Instant("2026-08-17T10:50:13.001Z")
# print(f"since: {since}")
# for comment in issue.get_comments(since=since.to_stdlib()):
#     print(comment.created_at < since.to_stdlib())
#     print(f"[{comment.created_at}] {comment.user.login}: {comment.body[:50]}")
