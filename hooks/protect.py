"""A conservative agent guardrail, not a sandbox or general shell interpreter."""
import json
import re
import sys

def reason(event):
    raw=json.dumps(event.get('tool_input',{}),ensure_ascii=False)
    if re.search(r'(?<![\w])\.env(?![\w.]|\.example)',raw,re.I):
        return 'Agent access to .env is blocked. Use runtime configuration scripts; never print secrets.'
    if re.search(r'\b\d{7,12}:[A-Za-z0-9_-]{25,}\b|sk-or-v1-[a-zA-Z0-9]{20,}|sk-proj-[a-zA-Z0-9_-]{20,}',raw):
        return 'Credential-like value in tool arguments is blocked.'
    if re.search(r'(?:PRODUCTION_ENABLED\s*[=:]\s*(?:true|1)|TELEGRAM_MODE\s*[=:]\s*production)',raw,re.I):
        return 'Only the human operator may enable production.'
    return None

def main():
    try: event=json.load(sys.stdin)
    except ValueError:
        print('Malformed hook input',file=sys.stderr)
        return 2
    blocked=reason(event)
    if blocked:
        print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':blocked}}))
    return 0

if __name__=='__main__': sys.exit(main())
