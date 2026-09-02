"""Demo application entry point."""


class App:
    """Minimal stand-in for a web framework's router — just enough for
    RepoImpact's entry-point decorator detection (@app.post(...)) to have
    something real to point at."""

    def post(self, path):
        def decorator(fn):
            return fn

        return decorator


app = App()
