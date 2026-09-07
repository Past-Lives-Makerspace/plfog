"""Domain exceptions for the billing app."""


class TabLockedError(Exception):
    """Raised when attempting to add an entry to a locked tab."""


class TabLimitExceededError(Exception):
    """Raised when adding an entry would exceed the member's tab limit."""


class NoPaymentMethodError(Exception):
    """Raised when billing is attempted but no payment method is on file."""


class RefundError(Exception):
    """Base for refund failures — also raised directly when Stripe rejects a refund."""


class RefundNotPossibleError(RefundError):
    """Raised when a source has no Stripe payment on file to refund."""


class AlreadyRefundedError(RefundError):
    """Raised when a source's payment is already fully refunded."""


class InvalidRefundAmountError(RefundError):
    """Raised when a requested refund amount is zero, negative, or exceeds the refundable remainder."""
