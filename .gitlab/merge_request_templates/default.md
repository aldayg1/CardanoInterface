---
name: Merge request
about: Submit a change to CardanoInterface
title: ""
labels: ""
---

## Summary
What does this MR change and why?

## Type of change
- [ ] Bug fix
- [ ] Feature
- [ ] Documentation
- [ ] Governance / repo hygiene
- [ ] Dependency or security update

## Checks
- [ ] `uv run mypy CardanoInterface.py` passes with no new errors
- [ ] `uv run ruff check CardanoInterface.py` passes
- [ ] No mocks, stubs, or placeholder implementations added
- [ ] No credentials, mnemonic, key, or API key data introduced
- [ ] Money values use integer lovelace (no floats)
- [ ] Network is never hardcoded
- [ ] CHANGELOG.md updated (if user-visible change)

## Testing performed
Describe what you ran (unit/static, manual on Preprod, etc.) and the result.

## Related issues
Fixes #X