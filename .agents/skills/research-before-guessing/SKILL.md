---
name: research-before-guessing
description: "Mandatory research discipline when the agent is uncertain, making assumptions, or entering an error loop. Load this skill BEFORE guessing how PyCardano, CIP-1854, CIP-8, CIP-30, CIP-5, Ogmios, Kupo, or any external library/API works — especially when previous attempts failed or produced unexpected results. Triggers on: repeated errors, uncertain behavior, guessing, assuming, 'I think', 'probably', 'should work', failed attempts, unexpected results, library behavior, API behavior, framework behavior, tool behavior, how does X work, what does X do."
---

# Research Before Guessing

CardanoInterface depends on PyCardano, Ogmios, Kupo, and CIP standards
that have specific, non-obvious behavior. Guessing costs turns. Research
saves them.

## When to load this skill

Load this skill when you detect ANY of the following in your own reasoning:

- You are about to say "I think" or "probably" about how PyCardano, CIP, Ogmios, or Kupo behaves
- You have made multiple attempts at something and they keep failing
- You are not 100% certain how a library, API, or tool behaves
- You are making assumptions about error messages, exceptions, or output
- You are about to reimplement something that likely already exists in PyCardano
- You are in a loop: try → fail → try again → fail → try again

## The rule

**Never guess when you can know.** Before writing code that depends on specific behavior of PyCardano, CIP-1854, CIP-8, CIP-30, CIP-5, Ogmios, Kupo, or any external tool:

1. **Stop** — acknowledge you are uncertain
2. **Research** — use available tools to find the actual answer:
   - `context7_query-docs` for PyCardano, cardano-cli, Ogmios, Kupo documentation
   - `websearch` for current CIP specifications
   - `webfetch` to read CIPs directly from the cardano-foundation GitHub
   - Source code inspection when docs are unclear
3. **Document** — record what you learned and why your previous assumption was wrong
4. **Proceed** — write the code based on facts, not guesses

## Cardano-specific research targets

| Situation | Research method |
|-----------|----------------|
| PyCardano API behavior | `context7_query-docs` with libraryId for pycardano |
| CIP-1854 (multisig HD paths) | `webfetch` from cardano-foundation/CIPs GitHub |
| CIP-8 (signData / COSE_Sign1) | `webfetch` from cardano-foundation/CIPs GitHub |
| CIP-30 (dApp-to-wallet bridge) | `webfetch` from cardano-foundation/CIPs GitHub |
| CIP-5 (bech32 prefixes) | `webfetch` from cardano-foundation/CIPs GitHub |
| Ogmios JSON-RPC methods | Ogmios docs or source |
| Kupo REST API endpoints | Kupo docs or source |
| Cardano transaction body structure | PyCardano source or CIP-0008/CIP-0002 |
| Native script serialization | PyCardano `NativeScript.to_cbor()` / `from_cbor()` |

## Anti-patterns to recognize

```
# ❌ "This should work" — you don't know, find out
# ❌ "The error is probably because..." — read the actual error
# ❌ Retrying the same approach with minor variations — something is fundamentally wrong
# ❌ "I'll just try this" without understanding why — research first
# ❌ Assuming PyCardano behaves like cardano-cli — each has different conventions
# ❌ Ignoring error messages to try a different approach — the error IS the answer
# ❌ Assuming CIP-1854 derivation works like BIP-44 — the purpose coin type is 1815, not 1815'
```

## Post-research checklist

- [ ] Did you find the OFFICIAL source (not blog posts, not AI-generated content)?
- [ ] Did you verify the version matches what CardanoInterface uses?
- [ ] Did you update your understanding based on what you found?
- [ ] Are you now writing code based on FACTS, not assumptions?

## Common failure modes

1. **The "similar but different" trap**: "cardano-cli does it this way, so PyCardano probably does too" — often wrong
2. **The "version drift" trap**: "This worked in PyCardano 0.x, so it should work in 0.y" — APIs change
3. **The "I read it somewhere" trap**: Memory is unreliable — verify against current docs
4. **The "quick retry" trap**: Making small changes without understanding the root cause — waste of turns
5. **The "it's obvious" trap**: Nothing is obvious when you're wrong — research anyway
6. **The "CIP says so" trap**: CIPs define behavior, not implementation — check how PyCardano actually implements it

## When you MUST research (non-negotiable)

- Before using any PyCardano API for the first time in this session
- After any unexpected error, exception, or behavior
- When you have failed at something more than once
- Before implementing complex logic that depends on Cardano transaction structure
- When you're about to say "I think this is how PyCardano handles it"
- When the user corrects you — understand WHY before retrying
