# Rule: Component props and client boundaries

Scope: Applies to all React and Next.js components.

Requirements:
- Every component must define a typed props interface.
- Do not use inline styles.
- Add use client only when the component actually needs state, effects tied to browser APIs, or event handlers.
- Keep one component per file.

Use this rule during implementation and code review.
