---
title: ChainLens
emoji: 🔍
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
short_description: Offline Bitcoin investigation platform with explainable evidence
---

# ChainLens

An offline Bitcoin investigation platform. Imports transaction and network metadata,
detects behavioural patterns, and presents prioritised leads with evidence you can trace
back to the source row.

This Space is password protected — it has one shared operator login and no user accounts.

Upload `demo_mixed_patterns.csv` (included in the image) to try it: expect `COLLECTOR`
as a high-priority collection pattern, and `EXCHANGE` deliberately held at medium by the
high-volume safeguard.

**Storage note.** Free Spaces have an ephemeral filesystem. The application works fully,
but saved cases are cleared when the Space restarts. Nothing is lost that cannot be
recreated by re-uploading a file.
