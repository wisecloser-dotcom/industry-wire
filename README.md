# Industry Wire

A phone-installable news feed of major headlines in each industry.

- `feed.json` holds the current edition. A scheduled Claude task rewrites it three times a day (about 7:40am, 12:40pm and 6:40pm Singapore time) and pushes it here.
- `index.html` reads `feed.json` and renders it. `sw.js` keeps the last edition available offline.
- Open the GitHub Pages link on your phone, then use **Add to Home Screen** to install it.

Summaries are written from public reporting and link to their sources. Not investment advice.
