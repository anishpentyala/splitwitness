# Motivation and existing work

Sources read November 13, 2026. These are motivation and alternatives, not
endorsements of this tool or evidence of its adoption.

- **scikit-learn, cross-validation guide:** "GroupKFold is a variation of K-fold
  which ensures that the same group is not represented in both testing and
  training sets." This motivates co-location constraints. GroupKFold constructs
  folds given groups; splitwitness audits fixed assignments given explicit links.
  https://scikit-learn.org/stable/modules/cross_validation.html
- **Yoel Zeldes, The Story of a Bad Train-Test Split, December 16, 2018:**
  "making sure each connected component resides in its entirety either in train
  or test set." The article describes interacting title/thumbnail relationships
  and discusses how splitting components can itself bias a sample. Connected
  components are established practice, not an algorithm invented here.
  https://towardsdatascience.com/the-story-of-a-bad-train-test-split-3343fcc33d2c/
- **Vector Agentic Skills, Duplicates and Entity Leakage:** "Pairs -> connected
  components (union-find) -> one group id per component -> grouped split."
  This is an existing workflow recipe. splitwitness does not detect the pairs.
  https://agenticskills.vector-ai-lab.com/skills/train-test-split-auditor/duplicates-and-entity-leakage/
- **leakaudit reference manual:** `find_duplicate_groups` propagates hash-based
  matches transitively, and `dhash_audit` reports cross-split image groups. This
  already covers image-oriented connected-component auditing. Use it when those
  perceptual-hash inputs and R workflow fit.
  https://anakincodex.r-universe.dev/leakaudit/doc/manual.html
- **spaCR leakage-audit documentation:** "the same path or crop identity never
  crosses a boundary" and "byte-identical crops are detected by streaming SHA-256
  even if renamed" are among its checks. It also has augmentation, group and CV
  coverage checks. That is broader, domain-aware auditing of image datasets;
  splitwitness reads no payloads and makes none of those detection claims.
  https://einarolafsson.github.io/spacr/leakage_audit.html
- **trustcv data-leakage guide:** "Some leaks happen before trustcv receives the
  data; no check can see those". It documents group, duplicate, temporal and other
  checks. splitwitness is deliberately narrower: a framework-independent graph
  constraint receipt, not a replacement for model/evaluation validation.
  https://ki-smile.github.io/trustcv/guides/data-leakage.html

## The small gap this implementation targets

Two strict CSV manifests are enough to audit arbitrary caller-declared
co-location links across an existing assignment. Reports separate direct
crossing links from the full affected transitive family, avoid quadratic pair
enumeration, and optionally show deterministic shortest evidence paths with
bounded report size. No ML framework, image decoder, embeddings, database or
payload column is required. This is a packaging and evidence-policy choice, not
a claim that other tools cannot do the same graph calculation or that this tool
has demonstrated real-world demand. Synthetic tests do not establish that.
