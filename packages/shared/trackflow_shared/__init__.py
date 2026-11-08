"""Domain rules shared by the TrackFlow backend and the /scripts tooling.

Nothing here imports FastAPI, TinyDB or pydantic. The incident vocabulary and
the CSV validation rules are plain Python so the seed script, the API and any
future analyser can agree on them without one depending on the other.
"""
