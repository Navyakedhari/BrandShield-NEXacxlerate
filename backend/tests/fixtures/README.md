# Collector fixtures

**These files are hand-written from the public API documentation. They are NOT captured from live responses.**
No live Tavily, iTunes or SearXNG request was made to produce them, so a field that the real service sends
but is not shown here, or a shape that differs in detail, is possible. Collectors therefore treat every field
as optional (see the `search_missing_fields.json` files).

After you add your keys, re-capture real responses (see `docs/COLLECTORS.md`, "First live check") and replace
or add to these files, then re-run `pytest tests/collectors`.

| Folder | Source of the shape | Files |
|---|---|---|
| `tavily/` | Tavily Search API docs: `POST /search`, results[{title,url,content,score}], `usage.credits`, error codes 401/429/432/433 | `search_ok`, `search_empty`, `search_missing_fields`, `error_401/429/432/433/500`, `malformed.txt` |
| `itunes/` | Apple iTunes Search API docs: results fields `trackName`, `bundleId`, `sellerName`, `artworkUrl512`, `trackViewUrl`, ... | `search_ok`, `search_empty`, `search_missing_fields`, `error_403`, `malformed.txt` |
| `searxng/` | SearXNG search API docs: `results[{title,url,content,engine}]`; 403 when `json` is not in `search.formats` | `search_ok`, `search_empty`, `search_missing_fields`, `error_403.html`, `malformed.txt` |

The names in the fixtures are fictional ("Acme Bank"). Status codes and headers (for example `Retry-After`) are
set in the tests, not stored in the files.
