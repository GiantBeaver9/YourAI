#!/usr/bin/env bash
# ============================================================================
#  Secure Context Pipeline - API smoke / mass test  (macOS / Linux / Git-Bash)
#
#  Usage:  scripts/test_api.sh [BASE_URL] [API_KEY]
#  Requires: curl (jq optional, for pretty output).
# ============================================================================
set -u

BASE="${1:-https://merry-playfulness-production-d238.up.railway.app}"
KEY="${2:-scp_Gf6tTE7qRnegYSEK3g1F4e7G5JLVsGD1}"
PASS=0; FAIL=0

ok(){ echo "[PASS] $1"; PASS=$((PASS+1)); }
no(){ echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

echo "============================================================"
echo "  Secure Context Pipeline - API tests"
echo "  Target : $BASE"
echo "============================================================"

# 1. health
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/health")
[ "$code" = "200" ] && ok "health returns 200" || no "health returned $code"

# 2. auth gate
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/process" \
  -H "Content-Type: application/json" -d '{"text":"x","task":"y"}')
[ "$code" = "401" ] && ok "auth gate rejects missing key (401)" || no "auth gate returned $code (expected 401)"

# leakcheck <label> <text> <must-be-absent> [<must-be-present>]
leakcheck(){
  local label="$1" text="$2" absent="$3" present="${4:-}"
  local resp; resp=$(curl -s -X POST "$BASE/obfuscate" \
    -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
    -d "{\"text\":\"$text\"}")
  case "$resp" in
    *"$absent"*) no "$label : LEAK - \"$absent\" still present" ;;
    *)           ok "$label : \"$absent\" removed from payload" ;;
  esac
  if [ -n "$present" ]; then
    case "$resp" in
      *"$present"*) ok "$label : clinical value \"$present\" preserved" ;;
      *)            no "$label : clinical value \"$present\" was dropped" ;;
    esac
  fi
}

echo; echo "--- leakage checks on /obfuscate ---"
leakcheck "SSN+name+MRN" 'Patient: Jonathan Reyes\nSSN: 482-19-7734\nMRN: 4457812\n62-year-old Han Chinese male, dose 500 mg.' "482-19-7734" "Han Chinese"
leakcheck "email+phone"  'Contact: Maria Reyes\nEmail: mreyes@example.com\nPhone: (415) 555-0177' "mreyes@example.com"
leakcheck "another SSN"  'Client: Dana Whitfield\nSSN: 019-28-4412\nViral load 250000 copies/mL' "019-28-4412" "250000 copies/mL"
leakcheck "surname-prose" 'Patient: Aaron Kessler\nKessler tolerated the regimen well.' "Kessler tolerated"

echo; echo "--- full round-trip on /process ---"
resp=$(curl -s -X POST "$BASE/process" -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"text":"Patient: Jonathan Reyes\nSSN: 482-19-7734\nMRN: 4457812","task":"Summarize."}')
case "$resp" in
  *'"deobfuscation_clean":true'*) ok "round-trip completed, de-obfuscation clean" ;;
  *) no "round-trip de-obfuscation not clean: $resp" ;;
esac

echo "============================================================"
echo "  RESULT: $PASS passed, $FAIL failed"
echo "============================================================"
[ "$FAIL" -eq 0 ]
