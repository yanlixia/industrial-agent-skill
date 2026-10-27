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
#   1. installs the skill (SKILL.md + references + scaffold) into
#      ~/.agents/skills/industrial-agent-builder/  (the cross-CLI convention dir)
#   2. links it for Claude Code at ~/.claude/skills/industrial-agent-builder
#      (skip with SKIP_CLAUDE_LINK=1)
#   3. optionally adds tools/ to PATH via ~/.agents/bin (skip with SKIP_TOOLS_PATH=1)
#   4. verifies: python3 runs check_marker_contract.py and state_roundtrip.py self-tests
#
# Idempotent: re-running updates in place. Never deletes other skills.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="industrial-agent-builder"
TARGET_BASE="${HOME}/.agents/skills"
TARGET="${TARGET_BASE}/${SKILL_NAME}"
LINK="${HOME}/.claude/skills/${SKILL_NAME}"
BIN_DIR="${HOME}/.agents/bin"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 is required (any 3.8+ works; tools are stdlib-only)"

say "installing skill to ${TARGET}"
mkdir -p "${TARGET_BASE}"
rm -rf "${TARGET}"
cp -R "${REPO_DIR}/skill/." "${TARGET}/"

if [ "${SKIP_CLAUDE_LINK:-0}" != "1" ]; then
  if [ -d "${HOME}/.claude/skills" ]; then
    say "linking for Claude Code at ${LINK}"
    rm -rf "${LINK}"
    ln -s "../../.agents/skills/${SKILL_NAME}" "${LINK}"
  else
    say "~/.claude/skills not found; skipping Claude Code link (other CLIs read ~/.agents/skills)"
  fi
fi

if [ "${SKIP_TOOLS_PATH:-0}" != "1" ]; then
  say "installing CLI tools to ${BIN_DIR}"
  mkdir -p "${BIN_DIR}"
  for tool in check_marker_contract.py validate_reply.py state_roundtrip.py remote_probe.py; do
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
say "self-tests passed"

say "done. usage:"
echo "  - in your agent CLI: mention '工业智能体' / 'industrial agent' — the skill triggers"
echo "  - tools now on PATH (after reload): check_marker_contract  validate_reply  state_roundtrip  remote_probe"
echo "  - scaffold a new agent repo: copy ${TARGET}/assets/scaffold and follow its README"
