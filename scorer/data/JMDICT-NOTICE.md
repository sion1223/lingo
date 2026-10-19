# JMdict attribution

`jmdict-common-words.json.gz` is a derived index made from JMdict. The server uses it to
keep only AI-picked JLPT words that JMdict lists as common words with the same reading.

- Copyright: James William Breen and the Electronic Dictionary Research and Development Group (EDRDG)
- Project: https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project
- Licence: Creative Commons Attribution-ShareAlike 4.0, https://www.edrdg.org/edrdg/licence.html
- Changes: kept only kanji forms with a priority tag (`ke_pri`), at most 8 characters, and
  their readings converted to hiragana; dropped all glosses and other fields.
- Update command: `python scripts/build_jmdict_index.py`

The generated file records the upstream database date. Refresh it from the current JMdict
release before a production release and at least monthly while the app uses the data, as
required by the EDRDG licence.
