"""Mpire Mortgage Ops REST API (FastAPI). Read-mostly decision-support surface over services/.

Nothing in this package writes to an LOS, sends a message, runs AUS, triggers TRID, or reads a
source document. The only writes are append-only dashboard rows and a service-only local sync.
"""
__version__ = "1.0.0"
