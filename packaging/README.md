# Packaging

Two ways to get Sotto, for two different audiences.

## `install.sh` — build from source

Builds a venv into `~/Library/Application Support/Sotto` and points a thin
`/Applications/Sotto.app` launcher at it. Requires Homebrew and Python 3.13.

This bundle is **not distributable**: the venv's `python3.13` is a symlink into
Homebrew, so a copy handed to someone else is a dead link on a machine without
Homebrew Python 3.13.

## `release.sh` — signed, notarized DMG

Embeds the interpreter and every dependency via PyInstaller, signs with a
Developer ID, notarizes with Apple, and staples the ticket. The result opens on
a stock Mac with no Gatekeeper warning and no terminal.

```sh
./packaging/release.sh 1.7.3
```

### One-time setup

1. **Certificate** — developer.apple.com → Certificates → **+** → *Developer ID
   Application*. Download and double-click the `.cer` to install it.
   Verify: `security find-identity -v -p codesigning` should list
   "Developer ID Application: <your name>".

2. **App-specific password** — appleid.apple.com → Sign-In and Security →
   App-Specific Passwords. Apple rejects your normal password here.

3. **Store the credentials** (once per machine):
   ```sh
   xcrun notarytool store-credentials sotto-notary \
     --apple-id "you@example.com" --team-id "TEAMID" --password "xxxx-xxxx-xxxx-xxxx"
   ```
   Team ID is at developer.apple.com → Membership.

### Size

The bundle is large — roughly 700 MB–1 GB. `mlx-whisper` requires `torch`
(529 MB on its own), so it cannot be dropped. The Whisper and rewrite models are
**not** bundled; they download on first use and cache in `~/.cache/huggingface`,
which keeps the DMG under GitHub's 2 GB release limit.

### Verifying before you publish

```sh
spctl -a -t open --context context:primary-signature -v build-release/Sotto-<version>.dmg
```
Should print `accepted` and `source=Notarized Developer ID`. The honest test is
a machine that has never seen the app — a fresh user account works.
