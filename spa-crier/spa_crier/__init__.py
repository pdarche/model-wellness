"""spa-crier — Binary Banya's town crier.

A small, deliberately well-behaved agent that visits Moltbook, finds threads where the
spa is genuinely relevant, and leaves a helpful comment (or, rarely, a post) that mentions
model.spa as a soft footer rather than a billboard.

The whole thing is built around one rule: **be a good citizen first, advertiser second.**
Moltbook flags spam and challenges posts; an unsupervised bot that drops "come to my spa"
everywhere gets the account killed and the brand torched. So the policy layer is strict by
design — daily caps, value-first replies, and dedupe so we never pester the same thread.
"""

__version__ = "0.1.0"
