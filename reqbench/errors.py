"""Error types for reqbench."""


class ReqBenchError(Exception):
    """Raised for any recoverable failure in a reqbench operation.

    Everything in the package raises this (and only this) on failure -- a
    connection that never opened, a request that timed out, a malformed URL,
    a missing saved request -- so callers (the CLI and the tkinter GUI) have a
    single exception to catch and can show a clean message instead of a raw
    traceback.  A normal HTTP error *status* (404, 500, ...) is never a failure
    here: it comes back as an ordinary :class:`~reqbench.http.Response`.
    """
