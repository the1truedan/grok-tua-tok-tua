# Public security + smoke (maintainer notes)

## Branch protection

`main` should block:

- force-pushes
- branch deletion

Solo maintainers may still push directly when pull-request reviews are **not** required.
That is intentional velocity for a small public slice — protection’s job is **no history rewrite**, not bureaucracy.

If GitHub alerts “branch is not protected,” re-apply rules (see monorepo ops or):

```bash
# Classic protection: no force-push / no delete (no PR required)
gh api -X PUT "repos/OWNER/REPO/branches/main/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["smoke"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

After the first green `smoke` workflow, required status checks make “production-adjacent” claims auditable on PRs.

## What CI smoke means (honest claims)

| Claim | CI |
|-------|-----|
| Python packages import | Yes |
| Config JSON valid | Yes |
| Resolve / loop dry-run paths | Yes |
| No obvious PHI/staging paths in docs | Yes (deny-list) |
| Full Headroom → LiteLLM → GPU home lab | **No** — needs local stack |
| Herdr multipane live | **No** |

Say: **“CI smoke green”** not **“identical to production lab.”**

## Cloud Docker / coding runtimes

GitHub Actions `ubuntu-latest` is the default free cloud runner. Optional later:

- `container:` jobs for compose-config validation
- Codespaces / devcontainers for interactive smoke

No secrets are required for this repo’s smoke job.
