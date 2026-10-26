"""Content Agent Portfolio (cap): the pipeline behind Patch and Falak.

Design rules that this package enforces in code rather than in prompts
(PRD D4, D11, D24, D28; Prompt Pack section 2):

* No model holds a publish tool. The publisher accepts only an approval token
  bound to the SHA-256 of the rendered file.
* Models never do money, view or score arithmetic.
* Every model output validates against one canonical schema; one retry, then park.
* Untrusted text reaches a model only inside nonce-keyed tags, as a tool result.
* Secrets come from the environment or Secret Manager; nothing here reads a key file.
"""

__version__ = "0.1.0"
