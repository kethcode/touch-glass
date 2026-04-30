#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

env_override_names=(
  BRAIN_DB
  BRAIN_PASSWORD
  TOUCH_GLASS_PYTHON
  TOUCH_GLASS_PYTHON_CMD
  TOUCH_GLASS_TOPICS_CONFIG
  TOUCH_GLASS_HERMES_CONSOLIDATION_PROMPT
  TOUCH_GLASS_HERMES_COMMENTARY_PROMPT
  TOUCH_GLASS_HERMES_PROMPT
  TOUCH_GLASS_HERMES_MARK_DELIVERED
  TOUCH_GLASS_HERMES_LIMIT
  TOUCH_GLASS_HERMES_WINDOW_MINUTES
  TOUCH_GLASS_HERMES_MIN_SCORE
  TOUCH_GLASS_HERMES_MIN_PRIORITY
)
env_override_set=()
env_override_values=()

for name in "${env_override_names[@]}"; do
  if value="$(printenv "${name}")"; then
    env_override_set+=("1")
    env_override_values+=("${value}")
  else
    env_override_set+=("0")
    env_override_values+=("")
  fi
done

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

for i in "${!env_override_names[@]}"; do
  if [[ "${env_override_set[$i]}" == "1" ]]; then
    export "${env_override_names[$i]}=${env_override_values[$i]}"
  fi
done

PYTHON_CMD_RAW="${TOUCH_GLASS_PYTHON_CMD:-}"
PYTHON_BIN="${TOUCH_GLASS_PYTHON:-${ROOT_DIR}/.venv/bin/python}"
TOPICS_CONFIG="${TOUCH_GLASS_TOPICS_CONFIG:-topics/index.json}"
CONSOLIDATION_PROMPT_FILE="${TOUCH_GLASS_HERMES_CONSOLIDATION_PROMPT:-prompts/hermes_consolidation.md}"
COMMENTARY_PROMPT_FILE="${TOUCH_GLASS_HERMES_COMMENTARY_PROMPT:-${TOUCH_GLASS_HERMES_PROMPT:-prompts/hermes_commentary.md}}"
MARK_DELIVERED="${TOUCH_GLASS_HERMES_MARK_DELIVERED:-true}"
EVENT_LIMIT="${TOUCH_GLASS_HERMES_LIMIT:-25}"
WINDOW_MINUTES="${TOUCH_GLASS_HERMES_WINDOW_MINUTES:-120}"
MIN_SCORE="${TOUCH_GLASS_HERMES_MIN_SCORE:-}"
MIN_PRIORITY="${TOUCH_GLASS_HERMES_MIN_PRIORITY:-}"

if [[ -n "${PYTHON_CMD_RAW}" ]]; then
  read -r -a python_cmd <<< "${PYTHON_CMD_RAW}"
elif [[ -x "${PYTHON_BIN}" ]]; then
  python_cmd=("${PYTHON_BIN}")
elif command -v python3 >/dev/null 2>&1; then
  python_cmd=("python3")
else
  printf 'No Python command available. Set TOUCH_GLASS_PYTHON_CMD or TOUCH_GLASS_PYTHON.\n' >&2
  exit 1
fi

detect_args=(
  -m enrichment.events
  detect
  --config "${TOPICS_CONFIG}"
  --format markdown
  --limit "${EVENT_LIMIT}"
)

if [[ -n "${WINDOW_MINUTES}" ]]; then
  detect_args+=(--window-minutes "${WINDOW_MINUTES}")
fi
if [[ -n "${MIN_SCORE}" ]]; then
  detect_args+=(--min-score "${MIN_SCORE}")
fi
if [[ -n "${MIN_PRIORITY}" ]]; then
  detect_args+=(--min-priority "${MIN_PRIORITY}")
fi

case "${MARK_DELIVERED}" in
  1|true|TRUE|yes|YES|on|ON)
    detect_args+=(--mark-delivered)
    ;;
esac

detector_output="$("${python_cmd[@]}" "${detect_args[@]}")"

if [[ "${detector_output}" == "[SILENT]" ]]; then
  printf '[SILENT]\n'
  exit 0
fi

if [[ ! -f "${CONSOLIDATION_PROMPT_FILE}" ]]; then
  printf 'Touch Glass Hermes consolidation prompt file not found: %s\n' "${CONSOLIDATION_PROMPT_FILE}" >&2
  exit 1
fi

if [[ ! -f "${COMMENTARY_PROMPT_FILE}" ]]; then
  printf 'Touch Glass Hermes commentary prompt file not found: %s\n' "${COMMENTARY_PROMPT_FILE}" >&2
  exit 1
fi

cat "${CONSOLIDATION_PROMPT_FILE}"
printf '\n\n---\n\n'
cat "${COMMENTARY_PROMPT_FILE}"
printf '\n\n---\n\n# Candidate Events\n\n'
printf '%s\n' "${detector_output}"
