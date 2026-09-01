"""Ports: what the core requires, expressed without naming who provides it.

Every port is a ``Protocol``, so adapters need no import of the port and the dependency
arrow points inward only. Every method touching tenant data takes ``tenant`` as its
required first argument - see contracts/ports.md for why that is structural rather than
stylistic.
"""
