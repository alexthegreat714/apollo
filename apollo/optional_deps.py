class MissingOptionalDependencyError(RuntimeError):
    def __init__(
        self,
        dependency: str,
        how_to_install: str = "py -3 -m pip install -r requirements-optional.txt",
    ) -> None:
        super().__init__(f"missing_optional_dependency:{dependency}")
        self.dependency = dependency
        self.how_to_install = how_to_install


def require_matplotlib_pyplot(backend: str | None = "Agg", force: bool = False):
    try:
        import matplotlib
        if backend:
            matplotlib.use(backend, force=force)
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise MissingOptionalDependencyError("matplotlib") from exc
    return plt
