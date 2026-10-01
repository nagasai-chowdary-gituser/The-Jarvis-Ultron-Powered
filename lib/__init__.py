"""
lib/ — capability, as importable Python instead of tool schemas.

THE WHOLE POINT. Thirty-nine tool schemas cost 5,648 tokens on EVERY request,
whether or not a single one was used. Anthropic's measured version of this
problem: 150,000 tokens of definitions down to 2,000 by letting the model read
what it needs instead of being handed everything - 98.7%. Cloudflare exposes
2,500+ endpoints as two tools.

A function in here costs nothing until it is imported. LIB.md is a flat index -
one line each, about 300 tokens - and the model writes `from lib import
whatsapp` when it actually needs it.

So the rule is: a new capability is a FUNCTION HERE, never a fifth tool.
"""
