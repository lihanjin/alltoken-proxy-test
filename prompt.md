在 HubTo 项目的 test.js 文件夹写一简单的递归

new Api ：sk-KsyBzneT6LWLBn8P7UOREFmcpLS9pfRwdmYqeQeZNa1sAdhx




cd "$(git rev-parse --show-toplevel)"
NEW_API_TOKEN='sk-KsyBzneT6LWLBn8P7UOREFmcpLS9pfRwdmYqeQeZNa1sAdhx' ./scripts/test-claude-clean.sh --dangerously-skip-permissions

cd "$(git rev-parse --show-toplevel)"
.venv/bin/python ./scripts/build-grouped-exports.py


export ANTHROPIC_BASE_URL="https://code.newcli.com/claude/ultra"
export ANTHROPIC_AUTH_TOKEN="sk-ant-oat01-swNctZ8L6GlBEfkDjcsOzFtI-PKEPbhFpF8OXzB8duItbuwLm27FfXreq4DziPEceod8GHbRN_5nSaejrL1fxOuX7RSXXAA" 
claude
