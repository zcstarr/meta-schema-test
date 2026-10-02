# LLMLOG

## 2026-10-02 - bind-all, CORS, root, versioned harness (v1.3/v1.4)
- `serve.py` now defaults `--host 0.0.0.0`, CORS `*` on all responses + `OPTIONS` 204 preflight.
- Sim endpoints served from `/` root, no prefix: `/status/`, `/redirect`, `/delay/`, `/echo`, `/routes` (`/__` aliases kept).
- Versioned harness: `--versions v1.3,v1.4`, layout `./v1.3/... -> /v1.3/...`; `/` renders harness index when no `index.html`.
- Clean URLs: extensionless resolve (`/v1.3/schema` -> `schema.html/.md/.json`), md served as `text/markdown`.
- Config `version` shorthand: `{"version": ["v1.3","v1.4"], "path": "/old", ...}` expands to per-version routes.
- `routes.example.json` updated with versioned examples.

## 2026-10-02 - version dirs seeded
- Created `v1.3/` + `v1.4/`, copied root `schema.md` into each as `schema.md` (currently empty placeholders).
- Serve: `/v1.3/schema` -> `./v1.3/schema.md`, `/v1.4/schema` -> `./v1.4/schema.md`. Edit each copy independently to diverge versions.

## 2026-10-02 - first commit, push pending gh auth
- Committed `846ba4f` (7 files: serve.py, routes.example.json, schema.md, v1.3/+v1.4/schema.md, LLMLOG.MD, .gitignore). GPG sign disabled for sandbox commit.
- `origin` set to `git@github.com:zcstarr/meta-schema-test.git`.
- BLOCKED: `gh` keyring token invalid (`gh auth login` needed) + remote repo not yet created, so no push yet.

## 2026-10-02 - pushed to zcstarr/meta-schema-test
- User re-authed `gh`; repo already existed (created it themselves).
- Fixed stale `github.com` RSA host key in `~/.ssh/known_hosts` (server presented `SHA256:uNiVztksCs...`, matches GitHub's published fingerprint).
- Pushed `846ba4f` + `4c3554f` to `origin/main`. Remote: `git@github.com:zcstarr/meta-schema-test.git`. Tree clean.
