"""Whereabouts ETL: turns Dr A Colin Day's village-map PDFs into a dataset."""

# Sent on every outbound request this package makes. Two of the three services
# we touch are other people's: Colin Day's own web server, which hosts the maps
# this project is built on, and OSM's Nominatim, whose usage policy requires a
# genuine identifying agent with a way to make contact. Both deserve to be able
# to see who is calling and to reach a human about it.
USER_AGENT = (
    "Whereabouts-ETL/1.0 "
    "(+https://whereabouts.adamdent.uk; whereabouts@adamdent.uk)"
)
