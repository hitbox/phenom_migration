import re

link_regex = re.compile(r'<(?P<href>[^>]+)>;\s*rel="(?P<rel>\w+)",?')

def parse_headers_links(link_string):
    """
    Return dicts of "rel" and "href" from 
    <https:...>; rel="first",<http:...>; rel="prev",<http:...>; rel="next"
    Note: the rel(ationship) part comes after the weirdly <> wrapped href.
    """
    for match in link_regex.finditer(link_string):
        yield match.groupdict()

test_link_string = (
    '<https://service2.ultipro.com/talent/recruiting/v2/AIR1013ATSG/api/applications?updated_after=2025-12-05T00%3A00%3A00.000Z>; '
    'rel="first",<https://service2.ultipro.com/talent/recruiting/v2/AIR1013ATSG/api/applications?updated_after=2025-12-05T00%3A00%3A00.000Z&before=759c0200-b4f0-4faf-a840-68c9be4a842d>; '
    'rel="prev",<https://service2.ultipro.com/talent/recruiting/v2/AIR1013ATSG/api/applications?updated_after=2025-12-05T00%3A00%3A00.000Z&after=0f5f6600-49ae-4a49-9ab7-84e96733208b>; '
    'rel="next"'
)

expect = [
    {
        'href': 'https://service2.ultipro.com/talent/recruiting/v2/AIR1013ATSG/api/applications?updated_after=2025-12-05T00%3A00%3A00.000Z',
        'rel': 'first',
    },
    {
        'href': 'https://service2.ultipro.com/talent/recruiting/v2/AIR1013ATSG/api/applications?updated_after=2025-12-05T00%3A00%3A00.000Z&before=759c0200-b4f0-4faf-a840-68c9be4a842d',
        'rel': 'prev',
    },
    {
        'href': 'https://service2.ultipro.com/talent/recruiting/v2/AIR1013ATSG/api/applications?updated_after=2025-12-05T00%3A00%3A00.000Z&after=0f5f6600-49ae-4a49-9ab7-84e96733208b',
        'rel': 'next',
    },
]

def old_code():
    """
    The above generator replaces this and gives us fully parsed data dicts.
    """
    # Search for the url inside <> for the next url page.
    match = re.search(r'<([^>]+)>;\s*rel="next"', link)
    if match:
        url = match.group(1)
        # Only send query for first request to avoid appending the params again and again.
        query = None
        logger.info('next applications: %s', url)
    else:
        logger.info('end of applications: %s', url)
        url = None

