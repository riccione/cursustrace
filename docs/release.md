# How to release

Needs a clean tree, `uv` (for `uvx bump-my-version`) and `git-cliff`.

1. Cut the release branch from an up-to-date `main`.

   ```sh
   git checkout main
   git pull --ff-only
   git fetch --tags
   git checkout -b release/v0.2.1
   ```

2. Bump the version: `patch` for fixes, `minor` for features, `major` for
   breaking changes. This updates `pyproject.toml` and commits; it does not tag.

   ```sh
   uvx bump-my-version bump patch
   ```

3. Generate and commit the changelog.

   ```sh
   git cliff --tag v0.2.1 -o CHANGELOG.md
   git add CHANGELOG.md
   git commit -m "chore(release): prepare for v0.2.1"
   ```

   `cliff.toml` skips `chore(release): prepare for`, so this commit does not
   appear in the next release's changelog.

4. Push and merge the PR.

   ```sh
   git push -u origin release/v0.2.1
   ```

5. Back on `main`, regenerate the changelog so commits merged after the branch
   was cut are included.

   ```sh
   git checkout main
   git pull --ff-only
   git cliff --tag v0.2.1 -o CHANGELOG.md
   git diff --stat CHANGELOG.md
   ```

   If the diff is not empty, commit the update on a small branch and merge it
   before tagging.

6. Tag the merge commit and push.

   ```sh
   git tag v0.2.1
   git push origin v0.2.1
   ```

## What the tag triggers

- **GitHub Release** (`release.yml`): release with generated notes and a
  `source-code.tar.gz` asset.
- **Docker** (`docker.yml`): `linux/amd64` + `linux/arm64` image pushed to
  `ptolemy77/cursustrace` as `0.2.1`, `0.2` and `latest` (uses the
  `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` secrets).

## Verify

```sh
gh release view v0.2.1
docker pull ptolemy77/cursustrace:0.2.1
```

Or check <https://hub.docker.com/r/ptolemy77/cursustrace/tags>.

## Notes

- CI has no push/PR triggers right now; run it manually before merging:
  `gh workflow run CI --ref release/v0.2.1`.
- A manual `Docker` workflow dispatch refreshes only `latest`; version tags
  come from a release tag.
