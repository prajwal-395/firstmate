"""The review channel store - the hook layer's feed, without the browser.

P2 (Ren consolidate, D3): the review dashboard server, its API models and
its static assets are retired from the product. What stays is this store:
`hooks._fire_steer` and `reel_hearing.announce` queue anchored notes here,
and anything that needs them reads `channel.json` off disk. There is no
browser half any more - no server, no `/api/review/*`, no `wait_for_batch`
consumer in this repository.
"""
