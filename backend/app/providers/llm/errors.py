"""Typed LLM / extraction failures. Do not turn these into a fake intent."""


class ProviderError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)
