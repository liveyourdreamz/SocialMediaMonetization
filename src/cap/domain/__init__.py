"""The domain layer: rules about a post that need no model, network, disk or web.

Anything here may be imported by any other layer; nothing here imports an agent, a
gate, an adapter, the web app or the pipeline (the import contracts in pyproject.toml
enforce it).
"""
