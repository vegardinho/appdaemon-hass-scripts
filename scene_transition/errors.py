class TransitionError(Exception):
    """Exception raised when a scene transition fails validation."""

    def __init__(self, message: str, entity_id: str = None, step: int = None):
        self.message = message
        self.entity_id = entity_id
        self.step = step
        super().__init__(message)
