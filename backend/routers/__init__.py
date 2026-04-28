"""
Backend modular routers package (incremental refactor).

Phase-1 (current): scaffolding + a single proof-of-concept endpoint
extracted from the server.py monolith. The pattern uses lazy imports
from `server` inside each handler so we don't create a circular import
at module load time.

Future phases will extract the rest of the admin / sheets / shipments /
couriers / wallet / plans endpoints into their own files here. Each
phase keeps the public API surface 100% identical to today's, so the
frontend never has to change.

Why incremental:
  • server.py is ~4400 lines with intricate cross-helper coupling
    (auth, sheet writer, plan-credit middleware, pincode lookup).
  • A "big bang" refactor risks regressing the 36/36 backend test
    suite — moving them piece-by-piece lets us verify after each
    extraction.
"""
