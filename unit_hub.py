#!/usr/bin/env python3
"""Off-reactor lookup for one subscriber home.

bridge_helpers stays free of Twisted. The voice path calls this once per
stream so the HTTP GET does not stall every other call.
"""


def schedule_off_reactor(func, args, callback, errback):
    """Run func(*args) off the Twisted reactor and deliver the result back on it."""
    from twisted.internet import reactor
    from twisted.internet.threads import deferToThread
    deferred = deferToThread(func, *args)

    def _ok(result):
        reactor.callFromThread(callback, result)

    def _bad(failure):
        reactor.callFromThread(errback, failure)

    deferred.addCallbacks(_ok, _bad)
    return deferred
