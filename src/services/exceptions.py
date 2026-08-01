"""Typed application-service failures."""


class ServiceError(RuntimeError):
    pass


class ValidationError(ServiceError):
    pass


class NotFoundError(ServiceError):
    pass


class InvalidTransitionError(ServiceError):
    pass


class InsufficientStockError(ServiceError):
    pass


class DataConflictError(ServiceError):
    pass

