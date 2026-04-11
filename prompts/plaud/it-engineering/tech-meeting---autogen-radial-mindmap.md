---
name: "Tech Meeting - AutoGen Radial Mindmap"
category: "IT & Engineering"
author: "Bill McCracken"
usage_count: 1829
source: plaud-community
---

* The objective is to analyze a meeting transcript and produce a detailed technical summary, documenting system designs, debugging, API interactions, coding, infrastructure, and security protocols with AI-driven insights and recommendations for unsolved aspects.
* Topics discussed should be indexed, including sub-topics, with each summarized in technical detail, and all code, commands, logs, configurations, architecture, workflows, and infrastructure discussions captured.
* A horizontal radial mindmap with a left-anchored, right-sweeping layout on a mid-grey background is to be generated, featuring a central node for 'Meeting' in dark blue, with first-level spokes for major topics in specified colors and second-level spokes for sub-topics with colored circles at their tips.
* The output should be a markdown image tag rendering the final radial diagram using QuickChart’s GraphViz API, with the center node as the meeting title, using `layout=twopi`, `shape=box`, and `style=rounded,filled`, and curved edges, where each top-level topic has a unique fill color.
* The meeting summary is intended for technical team members, both attendees and non-attendees, to serve as a reference for review and work.
