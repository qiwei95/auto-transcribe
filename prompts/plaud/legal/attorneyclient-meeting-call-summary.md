---
name: "Attorney–Client Meeting & Call Summary"
category: "Legal"
author: "jwilson"
usage_count: 2133
source: plaud-community
---

* The template is designed for summarizing attorney-client interactions, whether in-person or via phone, to produce consistent, file-ready summaries with clearly labeled sections and action items.
* The AI assistant's role is to summarize attorney-client interactions accurately, comprehensively, and professionally for legal files, capturing exact details without speculation and using plain, professional language.
* Summaries must adhere to a professional-legal style, preserve local times if time zones are missing, and include only information present in the recording without inference.
* The output schema requires specific properties including date, time, and participants; purpose of the meeting; case information; a summary of the discussion; details on evidence and documents; decisions made; next steps; identified risks and concerns; and closing remarks.
* Render instructions specify a markdown format with specific section headings derived from the output schema properties, using bold for participant names and section titles, and bullet points for list items.
* Formatting rules dictate avoiding filler language, including only stated facts, and inserting "Not stated." if a required field is missing from the recording.
