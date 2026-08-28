"""Growth Decision Engine.

Maps intent, session signals, catalogue candidates, and basket state to a
single bounded ProposedAction with evidence ids.

Does not execute, take payment, mutate inventory, or override policy.
"""
