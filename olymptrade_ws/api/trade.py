"""Hard safety boundary: Candice v8 never places broker orders."""
class TradeAPI:
    def __init__(self, client):
        self._client = client
    def __getattr__(self, name):
        raise RuntimeError('Candice v8 is read-only: broker order API is disabled.')
