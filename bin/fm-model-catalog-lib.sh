#!/usr/bin/env bash
# fm-model-catalog-lib.sh - shared validation for the local dispatch model catalog.
# Usage: . bin/fm-model-catalog-lib.sh

fm_model_catalog_path() {  # [<config-dir>]
  local config_dir=${1:-${FM_CONFIG_OVERRIDE:-}}
  [ -n "$config_dir" ] || config_dir="${FM_HOME:-.}/config"
  printf '%s/model-catalog.json\n' "$config_dir"
}

# fm_model_catalog_check <model> [<config-dir>]
# Returns 0 for active or uncatalogued models, 1 with a replacement diagnostic
# for retired models, and 2 for malformed catalogs.
fm_model_catalog_check() {
  local model=$1 path status replacement
  path=$(fm_model_catalog_path "${2:-}")
  [ -e "$path" ] || return 0
  command -v jq >/dev/null 2>&1 || { printf 'error: jq required to read model catalog %s\n' "$path" >&2; return 2; }
  jq -e '.models | type == "array" and all(.[]; (.id | type) == "string" and (.status == "active" or .status == "retired") and ((has("replaced_by") | not) or (.replaced_by | type) == "string"))' "$path" >/dev/null 2>&1 || {
    printf 'error: malformed model catalog %s\n' "$path" >&2
    return 2
  }
  status=$(jq -r --arg model "$model" '[.models[] | select(.id == $model) | .status] | first // "uncatalogued"' "$path") || return 2
  [ "$status" = retired ] || return 0
  replacement=$(jq -r --arg model "$model" '[.models[] | select(.id == $model) | .replaced_by // empty] | first // "no replacement listed"' "$path") || replacement='no replacement listed'
  printf 'error: dispatch model %s is retired; replacement: %s\n' "$model" "$replacement" >&2
  return 1
}
