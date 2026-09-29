# Agent Knowledge Template

Read the topic files for orientation, then check each important claim against its source chat and evidence card. A chat message is evidence of what someone said at a particular time; it is not automatically a current fact.

Keep personal data private. Do not publish topic files, evidence cards, or source conversations.

Suggested structure:

- `00_start_here/agent_brief.md`: concise, reviewed orientation.
- `topics/`: focused Markdown summaries.
- `evidence/evidence_cards.jsonl`: dated atomic observations with source chat IDs.
- `evidence/claim_register.jsonl`: consolidated claims with status and evidence IDs.
- `inbox/`: unreviewed observations; never treat these as confirmed facts.

This template does not perform automatic fact extraction. Review the full context before promoting a claim.
