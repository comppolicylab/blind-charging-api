class LazyObjectProxy:
    """A proxy object that lazily loads an object when an attribute is accessed."""

    def __init__(self, loader, *args, **kwargs):
        """Create a new LazyObjectProxy.

        Args:
            loader (callable): A function that returns the object to be proxied.
            *args: Positional arguments to pass to the loader.
            **kwargs: Keyword arguments to pass to the loader.
        """
        self._loader = (loader, args, kwargs)
        self._obj = None

    def __getattr__(self, name):
        if self._obj is None:
            f, args, kwargs = self._loader
            self._obj = f(*args, **kwargs)
        return getattr(self._obj, name)

    def _reset(self, *args, **kwargs):
        """Delete the cached object and reset the loader.

        The next time an attribute is accessed, the loader will be
        called with the new arguments.

        Args:
            *args: Positional arguments to pass to the loader.
            **kwargs: Keyword arguments to pass to the loader.
        """
        del self._obj
        self._obj = None
        self._loader = (self._loader[0], args, kwargs)
