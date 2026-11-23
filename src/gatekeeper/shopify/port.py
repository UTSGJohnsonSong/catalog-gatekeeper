"""The boundary between the app and a Shopify store."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from gatekeeper.domain.product import Product


class ShopifyError(Exception):
    pass


class ShopifyThrottled(ShopifyError):
    def __init__(self, retry_after: float = 1.0):
        super().__init__(f"throttled; retry after {retry_after}s")
        self.retry_after = retry_after


class ShopifyTransient(ShopifyError):
    pass


class ShopifyAuthError(ShopifyError):
    pass


class WritesNotAllowed(ShopifyAuthError):
    """This adapter must not write to this store. Raised before anything is sent."""


class ShopifyGraphQLError(ShopifyError):
    pass


@dataclass(frozen=True)
class UserError:
    field: list[str]
    message: str
    code: str | None = None


@dataclass
class UpdateResult:
    product: Product | None
    user_errors: list[UserError] = field(default_factory=list)


@dataclass
class ProductPage:
    products: list[Product]
    next_cursor: str | None


class ShopifyPort(Protocol):
    def list_products(
        self, *, cursor: str | None = None, first: int = 50, updated_since: datetime | None = None
    ) -> ProductPage: ...

    def get_product(self, gid: str) -> Product | None: ...

    def update_product(
        self, gid: str, fields: Mapping[str, str], *, expected_content_hash: str | None = None
    ) -> UpdateResult: ...

    def ensure_writable(self) -> None:
        """Raises WritesNotAllowed when a write must not happen. Asked before a write is recorded as sent."""
        ...
