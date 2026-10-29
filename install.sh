#!/usr/bin/env bash
# industrial-agent-skill installer.
#
# One command to pull and install into any agent CLI that supports the
# skills/ convention (Claude Code, Codex, CodeBuddy, ...):
#
#   curl -fsSL https://raw.githubusercontent.com/yanlixia/industrial-agent-skill/main/install.sh | bash
#
# or after cloning:
#   bash install.sh
#
# What it does:
#   0. when piped (no local repo), clones the repo to a temp cache first
#   1. installs industrial-agent-builder and session-storyboard into
#      ~/.agents/skills/  (the cross-CLI convention dir)
#   2. links them for Claude Code at ~/.claude/skills/
#      (skip with SKIP_CLAUDE_LINK=1)
#   3. optionally adds tools/ to PATH via ~/.agents/bin (skip with SKIP_TOOLS_PATH=1)
#   4. records the installed git commit in installed.json
#   5. verifies: scaffold tooling, update metadata, and session-storyboard self-tests
#
# Idempotent: re-running updates in place. Never deletes other skills.
set -euo pipefail

REPO_URL="https://github.com/yanlixia/industrial-agent-skill.git"
SKILL_NAME="industrial-agent-builder"
STORYBOARD_NAME="session-storyboard"
TARGET_BASE="${HOME}/.agents/skills"
TARGET="${TARGET_BASE}/${SKILL_NAME}"
STORYBOARD_TARGET="${TARGET_BASE}/${STORYBOARD_NAME}"
LINK="${HOME}/.claude/skills/${SKILL_NAME}"
STORYBOARD_LINK="${HOME}/.claude/skills/${STORYBOARD_NAME}"
BIN_DIR="${HOME}/.agents/bin"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 is required (any 3.8+ works; tools are stdlib-only)"

# Piped execution (curl | bash) has no local repo: clone to a cache dir.
if [ -f "$(dirname "${BASH_SOURCE[0]:-$0}")/skill/SKILL.md" ]; then
  REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
else
  CACHE="${HOME}/.cache/industrial-agent-skill"
  say "piped install: cloning ${REPO_URL} to ${CACHE}"
  command -v git >/dev/null 2>&1 || die "git is required for piped install (or clone the repo and run install.sh inside)"
  rm -rf "${CACHE}"
  git clone --depth 1 "${REPO_URL}" "${CACHE}" >/dev/null 2>&1 || die "git clone failed"
  REPO_DIR="${CACHE}"
fi
[ -f "${REPO_DIR}/skill/SKILL.md" ] || die "repo layout unexpected: skill/SKILL.md missing in ${REPO_DIR}"
[ -f "${REPO_DIR}/session-storyboard/SKILL.md" ] || die "repo layout unexpected: session-storyboard/SKILL.md missing in ${REPO_DIR}"

COMMIT="${INDUSTRIAL_AGENT_SKILL_COMMIT:-}"
if [[ ! "${COMMIT}" =~ ^[0-9a-f]{40}$ ]]; then
  COMMIT="$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || true)"
fi
[[ "${COMMIT}" =~ ^[0-9a-f]{40}$ ]] || die "cannot determine install commit; set INDUSTRIAL_AGENT_SKILL_COMMIT"
INSTALLED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

say "installing skill to ${TARGET}"
mkdir -p "${TARGET_BASE}"
rm -rf "${TARGET}"
cp -R "${REPO_DIR}/skill/." "${TARGET}/"
mkdir -p "${TARGET}/tools"
cp -R "${REPO_DIR}/tools/." "${TARGET}/tools/"
mkdir -p "${TARGET}/scripts"
install -m 0755 "${REPO_DIR}/tools/check_update.py" "${TARGET}/scripts/check_update.py"

say "installing companion skill to ${STORYBOARD_TARGET}"
rm -rf "${STORYBOARD_TARGET}"
cp -R "${REPO_DIR}/session-storyboard" "${STORYBOARD_TARGET}"
install -m 0755 "${REPO_DIR}/tools/check_update.py" "${STORYBOARD_TARGET}/scripts/check_update.py"

write_metadata() {
  local target="$1"
  local temporary="${target}.tmp"
  cat >"${temporary}" <<JSON
{
  "schema": "industrial-agent-skill/install/1",
  "repository": "${REPO_URL%.git}",
  "branch": "main",
  "commit": "${COMMIT}",
  "installed_at": "${INSTALLED_AT}",
  "skills": ["industrial-agent-builder", "session-storyboard"]
}
JSON
  mv "${temporary}" "${target}"
}

say "recording installed version ${COMMIT:0:12}"
write_metadata "${TARGET}/installed.json"
write_metadata "${STORYBOARD_TARGET}/installed.json"

if [ "${SKIP_CLAUDE_LINK:-0}" != "1" ]; then
  if [ -d "${HOME}/.claude/skills" ]; then
    say "linking skills for Claude Code at ${LINK} and ${STORYBOARD_LINK}"
    rm -rf "${LINK}"
    ln -s "../../.agents/skills/${SKILL_NAME}" "${LINK}"
    rm -rf "${STORYBOARD_LINK}"
    ln -s "../../.agents/skills/${STORYBOARD_NAME}" "${STORYBOARD_LINK}"
  else
    say "~/.claude/skills not found; skipping Claude Code link (other CLIs read ~/.agents/skills)"
  fi
fi

if [ "${SKIP_TOOLS_PATH:-0}" != "1" ]; then
  say "installing CLI tools to ${BIN_DIR}"
  mkdir -p "${BIN_DIR}"
  for tool in check_marker_contract.py validate_reply.py state_roundtrip.py remote_probe.py check_update.py; do
    install -m 0755 "${REPO_DIR}/tools/${tool}" "${BIN_DIR}/${tool%.py}" 2>/dev/null || \
      cp "${REPO_DIR}/tools/${tool}" "${BIN_DIR}/${tool}"
  done
  case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) say "NOTE: add ${BIN_DIR} to PATH to use the tools directly" ;;
  esac
fi

say "verifying toolchain (self-tests)"
python3 "${REPO_DIR}/tools/state_roundtrip.py" >/dev/null || die "state_roundtrip self-test failed"
python3 "${TARGET}/tools/scaffold.py" --help >/dev/null || die "scaffold generator self-test failed"
python3 "${TARGET}/scripts/check_update.py" --self-check >/dev/null || die "update metadata self-test failed"
python3 -m unittest discover -s "${STORYBOARD_TARGET}/tests" >/dev/null || die "session-storyboard self-test failed"
say "self-tests passed"

say "done. usage:"
echo "  - in your agent CLI: mention '工业智能体' / 'industrial agent' — the skill triggers"
echo "  - session replay: mention '会话回放' / '分析为什么这轮慢' — session-storyboard triggers"
echo "  - check updates: python3 ${TARGET}/scripts/check_update.py"
echo "  - tools now on PATH (after reload): check_marker_contract  validate_reply  state_roundtrip  remote_probe  check_update"
echo "  - scaffold a single expert: python3 ${TARGET}/tools/scaffold.py --mode single --domain <domain> --title '<title>' --target ./my-expert"
echo "  - scaffold an expert team:  python3 ${TARGET}/tools/scaffold.py --mode team --domain <domain> --title '<title>' --roles role1,role2 --target ./my-team"
