"""app/api/routes — one module per resource in docs/api-contract.md.

auth.py       POST /api/auth/login
overview.py   GET  /api/overview
mentions.py   GET  /api/mentions, POST .../assign, POST .../resolve
reviews.py    GET  /api/reviews, POST /api/reviews/{id}/reply
topics.py     GET  /api/topics, GET /api/topics/{key}/mentions
competitors.py GET /api/competitors
emv.py        GET  /api/emv
roster.py     GET  /api/roster
exports.py    POST /api/exports/{type}
status.py     GET  /api/status
"""
