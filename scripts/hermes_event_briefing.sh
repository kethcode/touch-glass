#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

PYTHON_BIN="${TOUCH_GLASS_PYTHON:-${ROOT_DIR}/.venv/bin/python}"
TOPICS_CONFIG="${TOUCH_GLASS_TOPICS_CONFIG:-topics/index.json}"
PROMPT_FILE="${TOUCH_GLASS_HERMES_PROMPT:-prompts/hermes_commentary.md}"
MARK_DELIVERED="${TOUCH_GLASS_HERMES_MARK_DELIVERED:-true}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

detect_args=(
  -m enrichment.events
  detect
  --config "${TOPICS_CONFIG}"
  --format markdown
)

case "${MARK_DELIVERED}" in
  1|true|TRUE|yes|YES|on|ON)
    detect_args+=(--mark-delivered)
    ;;
esac

detector_output="$("${PYTHON_BIN}" "${detect_args[@]}")"

if [[ "${detector_output}" == "[SILENT]" ]]; then
  printf '[SILENT]\n'
  exit 0
fi

if [[ ! -f "${PROMPT_FILE}" ]]; then
  printf 'Touch Glass Hermes prompt file not found: %s\n' "${PROMPT_FILE}" >&2
  exit 1
fi

cat "${PROMPT_FILE}"
printf '\n\n---\n\n'
printf '%s\n' "${detector_output}"
