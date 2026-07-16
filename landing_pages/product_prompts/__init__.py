"""Product marketing prompt generator.

Reads product URLs, fetches product + blog data, downloads imagery, and emits
one JSON file per product describing image-generation prompts and social copy
for a set of creative concepts. A downstream script consumes this output to
generate marketing images with a cloud image model (e.g. Grok).
"""

__version__ = "1.0.0"
