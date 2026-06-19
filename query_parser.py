import re


def parse_search_query(query: str) -> dict:
    """
    Parse a search query into semantic text and metadata filters.

    Supports:
      - char:Name     -> tag "char:name"
      - series:Name   -> tag "series:name"
      - artist:Name   -> tag "artist:name"
      - genre:Name    -> tag "genre:name"
      - #tagname      -> tag "tagname"
      - "Exact Name"  -> tag search for name (partial match on any tag containing the name)
      - free text     -> semantic search query

    Returns dict with:
      - 'semantic_query': str  (remaining free text for embedding)
      - 'tags': list[str]      (exact tag strings to match)
      - 'tag_likes': list[str] (substrings to match in any tag, e.g. from quoted names)
    """
    tags = []
    tag_likes = []
    remaining_parts = []

    # Extract quoted strings as tag-like searches
    quoted_re = re.compile(r'"([^"]+)"')
    quotes = quoted_re.findall(query)
    for q in quotes:
        tag_likes.append(q.lower().strip())
    query = quoted_re.sub('', query)

    # Extract keyword:value patterns
    prefix_re = re.compile(r'\b(char|series|artist|genre):(\S+)', re.IGNORECASE)
    prefixes = prefix_re.findall(query)
    for prefix, value in prefixes:
        tags.append(f"{prefix.lower()}:{value.lower().strip(',.;:')}")
    query = prefix_re.sub('', query)

    # Extract #tag patterns
    hashtag_re = re.compile(r'#(\S+)')
    hashtags = hashtag_re.findall(query)
    for h in hashtags:
        tags.append(h.lower().strip(',.;:'))
    query = hashtag_re.sub('', query)

    # Cleanup remaining text for semantic search
    semantic_query = ' '.join(query.split()).strip()

    return {
        'semantic_query': semantic_query,
        'tags': tags,
        'tag_likes': tag_likes,
    }
