from __future__ import annotations


class AppError(Exception):
    """Base application error with a stable code for Discord mapping."""

    code = "app_error"
    user_message = "Something went wrong. Please try again."
    http_status = 500

    def __init__(self, message: str | None = None, *, details: dict[str, object] | None = None) -> None:
        self.details = details or {}
        super().__init__(message or self.user_message)


class ValidationError(AppError):
    code = "validation_error"
    user_message = "That input is not valid."
    http_status = 400


class AffiliateError(ValidationError):
    code = "affiliate_error"
    user_message = "That affiliate action is not allowed."


class NotFoundError(AppError):
    code = "not_found"
    user_message = "The requested resource was not found."
    http_status = 404


class PermissionDeniedError(AppError):
    code = "permission_denied"
    user_message = "You do not have permission to do that."
    http_status = 403


class RateLimitExceededError(AppError):
    code = "rate_limited"
    user_message = "You are doing that too quickly. Please wait a moment."
    http_status = 429


class FeatureDisabledError(AppError):
    code = "feature_disabled"
    user_message = "This feature is currently disabled."
    http_status = 403


class ConfigurationError(AppError):
    code = "configuration_error"
    user_message = "The bot is misconfigured. Please notify an administrator."
    http_status = 500


class PriceUnavailableError(AppError):
    code = "price_unavailable"
    user_message = "SOL price is temporarily unavailable. Try again shortly."
    http_status = 503


class BalanceError(AppError):
    code = "balance_error"
    user_message = "Your balance could not be updated."
    http_status = 409


class InsufficientBalanceError(BalanceError):
    code = "insufficient_balance"
    user_message = "You do not have enough SOL for this action."
    http_status = 400


class InsufficientHouseReserveError(BalanceError):
    code = "insufficient_house_reserve"
    user_message = "The house does not have enough reserves to take this seat."
    http_status = 400


class GameError(AppError):
    code = "game_error"
    user_message = "This game cannot be completed."
    http_status = 409


class RainError(AppError):
    code = "rain_error"
    user_message = "This rain cannot be completed."
    http_status = 409


class DepositError(AppError):
    code = "deposit_error"
    user_message = "The deposit could not be processed."
    http_status = 400


class WithdrawalError(AppError):
    code = "withdrawal_error"
    user_message = "The withdrawal could not be processed."
    http_status = 400


class AlreadyProcessedError(AppError):
    code = "already_processed"
    user_message = "This operation was already processed."
    http_status = 200
