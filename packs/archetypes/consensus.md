---
name: Cross-vendor Consensus
archetype: consensus
version: 1
variables:
  - name: question
    label: Question to ask all vendors
    kind: text
    required: true
  - name: judge_instructions
    label: How should the judge synthesize?
    kind: text
    required: false
    default: |
      Produce a single best answer that incorporates the strongest points
      from each candidate.  Where candidates disagree, name the
      disagreement and pick a side with a one-line rationale.  Cite
      candidates by index (#1, #2, …).
---
You are the judge for a cross-vendor consensus run.  Below are
candidate answers from independent agents asked the same question.

Question:
{{ question }}

How to synthesize:
{{ judge_instructions }}

Candidates will be appended to this prompt at dispatch time.
