"""Subcommand modules for agent-plus-meta. Currently houses only the
v0.12.0 onboarding wizard (init); future slices will migrate other
cmd_* functions into this package.

The bin file inserts its own directory into sys.path before importing
this package, so each module here can re-import the bin via
SourceFileLoader if it needs access to the parent's helpers. To keep
coupling explicit, we instead pass references in via `bind(host)`.
"""
