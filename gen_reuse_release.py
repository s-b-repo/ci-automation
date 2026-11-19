#!/usr/bin/env python3
"""Regenerate .github/workflows/_reuse-release.yml with the release/ scripts
embedded inline (base64), so the reusable workflow is self-contained and works
even while this repo is private. Run after editing anything in release/."""
import base64, os
HERE = os.path.dirname(os.path.abspath(__file__))

def b64(name):
    with open(os.path.join(HERE, "release", name), "rb") as f:
        return base64.b64encode(f.read()).decode()

tpl = f"""name: _reuse-release

# Reusable release workflow. Called by a repo's release.yml; runs in THAT repo's
# context. Release logic lives here (scripts embedded below from release/*), so
# editing this file improves releases for every caller at once.
# Regenerate with gen_reuse_release.py after editing release/.

on:
  workflow_call:
    inputs:
      release_as:
        type: string
        required: false
        default: "auto"
      explicit_version:
        type: string
        required: false
        default: ""

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    env:
      SYNC_MANIFEST: auto
    steps:
      - name: Checkout caller repo
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: true

      - name: Materialize release scripts
        run: |
          mkdir -p "$RUNNER_TEMP/rel"
          printf %s '{b64("compute-release.sh")}' | base64 -d > "$RUNNER_TEMP/rel/compute-release.sh"
          printf %s '{b64("bump-manifest.py")}' | base64 -d > "$RUNNER_TEMP/rel/bump-manifest.py"
          printf %s '{b64("release-notes.py")}' | base64 -d > "$RUNNER_TEMP/rel/release-notes.py"

      - name: Compute next version
        id: ver
        run: |
          force=""
          ev='${{{{ inputs.explicit_version }}}}'
          ra='${{{{ inputs.release_as }}}}'
          if [ -n "$ev" ]; then force="$ev";
          else case "$ra" in patch|minor|major|none) force="$ra" ;; esac; fi
          out="$(bash "$RUNNER_TEMP/rel/compute-release.sh" "$force")"
          echo "$out"
          echo "$out" >> "$GITHUB_OUTPUT"

      - name: No release needed
        if: steps.ver.outputs.next_version == ''
        run: echo "Nothing to release (bump=${{{{ steps.ver.outputs.bump }}}})."

      - name: Configure git identity
        if: steps.ver.outputs.next_version != ''
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

      - name: Sync manifest version (best-effort)
        if: steps.ver.outputs.next_version != '' && env.SYNC_MANIFEST != 'off'
        run: |
          v="${{{{ steps.ver.outputs.next_version }}}}"
          changed="$(python3 "$RUNNER_TEMP/rel/bump-manifest.py" "${{v#v}}" 2>/dev/null || true)"
          if [ -n "$changed" ]; then
            echo "Bumped: $changed"
            printf '%s\\n' "$changed" | xargs -r git add --
            git commit -m "chore(release): ${{v}} [skip ci]"
            if ! git push origin "HEAD:${{{{ github.ref_name }}}}"; then
              echo "::warning::manifest push rejected — releasing without the bump"
              git reset --hard "origin/${{{{ github.ref_name }}}}"
            fi
          fi

      - name: Tag and publish release
        if: steps.ver.outputs.next_version != ''
        env:
          GH_TOKEN: ${{{{ github.token }}}}
        run: |
          v="${{{{ steps.ver.outputs.next_version }}}}"
          prev="${{{{ steps.ver.outputs.prev }}}}"
          if git rev-parse "$v" >/dev/null 2>&1; then
            echo "::error::tag $v already exists"; exit 1
          fi
          python3 "$RUNNER_TEMP/rel/release-notes.py" "$prev" > "$RUNNER_TEMP/rel/notes.md"
          if [ "$prev" != "none" ] && [ -n "$prev" ]; then
            {{ echo; echo "**Full Changelog**: ${{{{ github.server_url }}}}/${{{{ github.repository }}}}/compare/${{prev}}...${{v}}"; }} >> "$RUNNER_TEMP/rel/notes.md"
          fi
          git tag -a "$v" -m "$v"
          git push origin "refs/tags/$v"
          gh release create "$v" --title "$v" --notes-file "$RUNNER_TEMP/rel/notes.md" --latest --verify-tag
          echo "Released $v"
"""

with open(os.path.join(HERE, ".github/workflows/_reuse-release.yml"), "w") as f:
    f.write(tpl)
print("wrote .github/workflows/_reuse-release.yml")
