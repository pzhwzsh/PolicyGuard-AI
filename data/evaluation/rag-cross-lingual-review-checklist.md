# Cross-language dataset review checklist

Dataset: `rag-cross-lingual-zh-en-v1.json`

The dataset remains `ai_assisted_pending_human_review` until a reviewer completes every item.

For each positive sample:

- Read the official English section identified by `expected_section_id`.
- Confirm the Chinese question preserves the section's actors, conduct, and conditions.
- Confirm no penalty, date, approval, product category, or legal conclusion was introduced.
- Mark `review_status` as `human_verified` or correct/remove the sample.

For each no-answer sample:

- Confirm the answer is absent from the active four-section US/EU subset.
- Do not interpret the label as absence from all US or EU law.
- Re-review whenever active source coverage changes.

Only change the top-level `label_status` to `human_verified` after all samples are verified. Record
the reviewer and date in a pull request; do not place a real name in the public dataset if the
reviewer prefers a pseudonym.
