"""Web search providers."""

from agent.search.base import SearchProvider, SearchResponse, SearchResult
from agent.search.perplexity import PerplexityProvider
from agent.search.tavily import TavilyProvider

_PROVIDERS = {
    "tavily": TavilyProvider,
    "perplexity": PerplexityProvider,
}


def build_search_provider(config: dict[str, object]) -> SearchProvider:
    """Instantiate the provider named in ``config['provider']``."""
    from agent.errors import ConfigError

    name = str(config.get("provider", "tavily")).lower()
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        raise ConfigError(f"Unknown web_search provider: {name!r}")
    api_key = str(config.get("api_key", "") or "")
    provider: SearchProvider
    if name == "perplexity":
        provider = PerplexityProvider(api_key, model=str(config.get("model", "") or "sonar"))
    else:
        provider = TavilyProvider(api_key)
    return provider


__all__ = [
    "PerplexityProvider",
    "SearchProvider",
    "SearchResponse",
    "SearchResult",
    "TavilyProvider",
    "build_search_provider",
]
